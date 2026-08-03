from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from metis.cli import main
from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import SQLiteStateStore
from metis.draft_notes import DraftNoteStore
from metis.evidence import EvidenceStore
from metis.filing import FilingService, FilingStatus
from metis.proposal_content import ProposalContentStore
from metis.proposal_evidence import ProposalEvidenceStore
from tests.data_access.inspection import (
    approval_rows,
    delete_approvals,
    force_approval_committed_at,
    force_intake_state,
    table_row_count,
)
from tests.test_proposal_integration import PipelineAdapter


COMMITTED_AT = "2026-08-02T12:00:00Z"
GOAL_ID = "goal.health-baseline"
GOAL_NOTE = (
    b"---\nid: goal.health-baseline\ntype: goal\n"
    b"title: Establish a health baseline\nstatus: active\n"
    b"horizon: annual\ncreated: 2026-08-02\n---\n"
)


class FilingFixture(unittest.TestCase):
    """Drive the real loop to `approved`, then exercise Step 6 against it."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.adapter = PipelineAdapter()
        self.store = SQLiteStateStore(self.root / "state" / "metis.db")
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _approved(self, *, links: bytes = b'links:\n  - "[[%s]]"\n' % GOAL_ID.encode()):
        self.capture_id = self._run(["capture", "Build a review workflow."])[
            "capture_id"
        ]
        self._run(["classify", self.capture_id])
        self.proposed = self._run(["propose", self.capture_id])
        self.draft = self.root / self.proposed["draft_path"]
        self._write_goal()
        self.draft.write_bytes(
            self.draft.read_bytes()
            .replace(b"links: []\n", links, 1)
            .replace(b"status: proposed\n", b"status: approved\n", 1)
        )
        self._run(["approvals"])
        # The approval ran on the real clock; pin the transition time so the
        # fixed filing clock is deterministically after it.
        force_intake_state(
            self.store,
            self.capture_id,
            state="approved",
            state_updated_at="2026-08-02T11:00:00Z",
            failure_reason=None,
        )
        self.filed_path = f"vault/notes/filed/note.{self.capture_id}.md"
        return self.capture_id

    def _write_goal(self, note: bytes = GOAL_NOTE) -> None:
        goal = self.root / "vault" / "goals" / f"{GOAL_ID}.md"
        goal.parent.mkdir(parents=True, exist_ok=True)
        goal.write_bytes(note)

    def _service(self) -> FilingService:
        return FilingService(
            self.store,
            EvidenceStore(self.root),
            ClassificationEvidenceStore(self.root),
            ProposalEvidenceStore(self.root),
            ProposalContentStore(self.root),
            DraftNoteStore(self.root),
            DraftNoteStore(self.root, stage="filed"),
            self.root,
            clock=lambda: datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        )

    def _run(self, argv: list[str]) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                argv,
                runtime_root=self.root,
                model_adapter_factory=lambda: self.adapter,
            )
        self.assertEqual(code, 0, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def _vault_snapshot(self) -> dict:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in sorted((self.root / "vault").rglob("*"))
            if path.is_file()
        }

    def _filed_exists(self) -> bool:
        return (self.root / self.filed_path).is_file()


class FilingTests(FilingFixture):
    def test_approved_note_is_filed_with_provenance_and_links(self) -> None:
        self._approved()

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FILED)
        self.assertEqual(result.capture_id, self.capture_id)
        self.assertEqual(result.proposal_id, self.proposed["proposal_id"])
        self.assertEqual(result.filed_path, self.filed_path)
        self.assertEqual(result.links, (GOAL_ID,))
        self.assertEqual(result.committed_at, COMMITTED_AT)
        self.assertEqual(result.intake_state, "filed")
        self.assertIsNone(result.reason)

        filed = (self.root / self.filed_path).read_bytes()
        frontmatter = filed.split(b"---\n\n", 1)[0]
        self.assertIn(b"status: approved\n", frontmatter)
        self.assertIn(f'capture_id: "{self.capture_id}"\n'.encode(), frontmatter)
        self.assertIn(
            f'  capture: "evidence/{self.capture_id}/raw.txt"\n'.encode(),
            frontmatter,
        )
        self.assertIn(b"verification: unverified\n", frontmatter)
        self.assertIn(b'links:\n  - "[[goal.health-baseline]]"\n', frontmatter)

        intake = self.store.find_intake_by_capture_id(self.capture_id)
        rows = approval_rows(self.store)
        # `approved:` is the moment the human's decision was detected, not the
        # moment of filing, so the note dates itself to the approval.
        self.assertIn(f'approved: "{rows[0].detected_at}"\n'.encode(), frontmatter)
        self.assertEqual(intake.state, "filed")
        self.assertIsNone(intake.failure_reason)
        self.assertEqual(rows[0].committed_at, COMMITTED_AT)
        self.assertIsNone(rows[0].revoked_at)
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_filed_note_matches_the_approved_draft_outside_the_expected_fields(
        self,
    ) -> None:
        self._approved()

        self._service().file(self.capture_id)

        approved_draft = self.draft.read_bytes()
        filed = (self.root / self.filed_path).read_bytes()
        self.assertEqual(
            filed.split(b"---\n\n", 1)[1],
            approved_draft.split(b"---\n\n", 1)[1],
        )
        normalize = lambda note: [  # noqa: E731 - test-local comparison
            line
            for line in note.split(b"---\n\n", 1)[0].splitlines()
            if not line.startswith(b"approved:")
        ]
        self.assertEqual(normalize(filed), normalize(approved_draft))

    def test_note_without_provenance_fails_validation(self) -> None:
        """The validator must be able to fail; the renderer always emits these."""
        self._approved()
        store = DraftNoteStore(self.root, stage="filed")
        capture_line = f'capture_id: "{self.capture_id}"\n'
        evidence_line = f'  capture: "evidence/{self.capture_id}/raw.txt"\n'
        complete = (
            "---\n"
            f'id: "note.{self.capture_id}"\n'
            "type: idea\n"
            'title: "Review"\n'
            "status: approved\n"
            f"{capture_line}"
            "evidence:\n"
            f"{evidence_line}"
            'links:\n  - "[[goal.health-baseline]]"\n'
            "---\n\nBody.\n"
        ).encode("utf-8")
        stripped = (
            ("no capture_id", complete.replace(capture_line.encode(), b"", 1)),
            ("no evidence block", complete.replace(b"evidence:\n", b"", 1)),
            ("no evidence path", complete.replace(evidence_line.encode(), b"", 1)),
        )
        for label, note in stripped:
            with self.subTest(label):
                with self.assertRaises(Exception) as raised:
                    store.create(self.filed_path, note)
                self.assertIn("provenance", str(raised.exception))
                self.assertFalse(self._filed_exists())

    def test_unresolvable_link_blocks_commit(self) -> None:
        self._approved(links=b'links:\n  - "[[goal.does-not-exist]]"\n')
        before = self._vault_snapshot()

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.link_unresolvable")
        self.assertIn("goal.does-not-exist", result.message)
        self.assertEqual(self._vault_snapshot(), before)
        self.assertFalse(self._filed_exists())
        self.assertEqual(
            self.store.find_intake_by_capture_id(self.capture_id).state,
            "approved",
        )
        self.assertIsNone(approval_rows(self.store)[0].committed_at)

    def test_partly_unresolvable_links_block_the_whole_commit(self) -> None:
        self._approved(
            links=b'links:\n  - "[[goal.health-baseline]]"\n  - "[[proj.missing]]"\n'
        )

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.link_unresolvable")
        self.assertIn("proj.missing", result.message)
        self.assertNotIn("goal.health-baseline", result.message)
        self.assertFalse(self._filed_exists())

    def test_approved_draft_without_links_blocks_commit(self) -> None:
        self._approved(links=b"links: []\n")

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.links_absent")
        self.assertFalse(self._filed_exists())
        self.assertEqual(
            self.store.find_intake_by_capture_id(self.capture_id).state,
            "approved",
        )

    def test_absent_goal_and_project_directories_are_unresolvable_not_a_crash(
        self,
    ) -> None:
        self._approved()
        for note in (self.root / "vault" / "goals").iterdir():
            note.unlink()
        (self.root / "vault" / "goals").rmdir()

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.link_unresolvable")
        self.assertFalse(self._filed_exists())

    def test_a_project_note_resolves_a_link_as_well_as_a_goal(self) -> None:
        self._approved(links=b'links:\n  - "[[proj.metis-core]]"\n')
        project = self.root / "vault" / "projects" / "proj.metis-core.md"
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_bytes(
            b"---\nid: proj.metis-core\ntype: project\n"
            b"title: Metis core loop\nstatus: active\n---\n"
        )

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FILED)
        self.assertEqual(result.links, ("proj.metis-core",))

    def test_a_link_resolves_on_note_id_not_on_filename(self) -> None:
        self._approved()
        goal = self.root / "vault" / "goals" / f"{GOAL_ID}.md"
        goal.rename(goal.with_name("renamed-by-the-owner.md"))

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FILED)

    def test_a_matching_filename_without_an_id_field_does_not_resolve(self) -> None:
        self._approved()
        goal = self.root / "vault" / "goals" / f"{GOAL_ID}.md"
        goal.write_bytes(GOAL_NOTE.replace(b"id: goal.health-baseline\n", b"", 1))

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.link_unresolvable")
        self.assertFalse(self._filed_exists())


class UnapprovedWriteTests(FilingFixture):
    def test_unapproved_write_is_refused(self) -> None:
        """The note writer refuses without a recorded approval (REQ-GOV-001)."""
        illegal = (
            "captured",
            "classifying",
            "classified",
            "proposing",
            "proposed",
            "awaiting_approval",
            "rejected",
            "failed",
        )
        for state in illegal:
            with self.subTest(state=state):
                self.setUp()
                self._approved()
                force_intake_state(
                    self.store,
                    self.capture_id,
                    state=state,
                    state_updated_at="2026-08-02T11:30:00Z",
                    failure_reason=None,
                )
                before = self._vault_snapshot()

                result = self._service().file(self.capture_id)

                self.assertEqual(result.status, FilingStatus.REFUSED)
                self.assertEqual(result.reason, "filing.not_approved")
                self.assertEqual(result.intake_state, state)
                self.assertIsNone(result.filed_path)
                self.assertEqual(self._vault_snapshot(), before)
                self.assertFalse(self._filed_exists())
                self.assertIsNone(approval_rows(self.store)[0].committed_at)

    def test_filing_without_an_approval_row_is_refused(self) -> None:
        self._approved()
        delete_approvals(self.store)

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.approval_missing")
        self.assertFalse(self._filed_exists())
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_a_note_dropped_into_the_filed_directory_is_not_an_approval(self) -> None:
        """Nothing in the vault can manufacture a state transition."""
        rogue_capture = "3f6ca1b8-4b2e-4a4c-9d2f-1c7b0e5a9d41"
        rogue = self.root / f"vault/notes/filed/note.{rogue_capture}.md"
        rogue.parent.mkdir(parents=True, exist_ok=True)
        rogue.write_bytes(b"---\nid: rogue\nstatus: approved\n---\n\nSmuggled.\n")

        result = self._service().file(rogue_capture)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.capture_unknown")
        self.assertEqual(table_row_count(self.store, "intake"), 0)
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_an_invalid_capture_id_is_refused_before_any_lookup(self) -> None:
        for capture_id in ("", "not-a-uuid", "../../etc/passwd", "8F14E45F" * 4):
            with self.subTest(capture_id=capture_id):
                result = self._service().file(capture_id)

                self.assertEqual(result.status, FilingStatus.FAILED)
                self.assertEqual(result.reason, "filing.capture_id_invalid")
        # The refusals are recorded; nothing was looked up or transitioned.
        self.assertEqual(table_row_count(self.store, "audit_event"), 4)
        self.assertEqual(table_row_count(self.store, "intake"), 0)


class FilingConsistencyTests(FilingFixture):
    def test_body_edited_after_approval_fails_closed(self) -> None:
        self._approved()
        self.draft.write_bytes(
            self.draft.read_bytes().replace(b"Define", b"Redefine", 1)
        )

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.draft_inconsistent")
        self.assertFalse(self._filed_exists())

    def test_status_reverted_after_approval_fails_closed(self) -> None:
        self._approved()
        self.draft.write_bytes(
            self.draft.read_bytes().replace(
                b"status: approved\n", b"status: proposed\n", 1
            )
        )

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.draft_not_approved")
        self.assertFalse(self._filed_exists())

    def test_missing_draft_fails_closed(self) -> None:
        self._approved()
        self.draft.unlink()

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.draft_inconsistent")
        self.assertFalse(self._filed_exists())

    def test_broken_evidence_chain_fails_closed(self) -> None:
        cases = (
            ("capture evidence", "evidence"),
            ("classification evidence", "classification-evidence"),
            ("proposal evidence", "proposal-evidence"),
        )
        for label, directory in cases:
            with self.subTest(label):
                self.setUp()
                self._approved()
                for path in sorted((self.root / directory).rglob("*.txt")):
                    path.write_bytes(b"tampered\n")

                result = self._service().file(self.capture_id)

                self.assertEqual(result.status, FilingStatus.FAILED)
                self.assertEqual(result.reason, "filing.evidence_chain_broken")
                self.assertFalse(self._filed_exists())
                self.assertEqual(
                    self.store.find_intake_by_capture_id(self.capture_id).state,
                    "approved",
                )

    def test_canonical_content_disagreement_fails_closed(self) -> None:
        self._approved()
        body = (
            self.root
            / "proposal-content"
            / self.proposed["proposal_id"]
            / "body.md"
        )
        body.write_bytes(body.read_bytes().replace(b"Define", b"Redefine", 1))

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.content_inconsistent")
        self.assertFalse(self._filed_exists())


class FilingReplayTests(FilingFixture):
    def test_second_file_run_reports_duplicate_without_a_second_note(self) -> None:
        self._approved()
        first = self._service().file(self.capture_id)
        after_first = self._vault_snapshot()

        second = self._service().file(self.capture_id)

        self.assertEqual(first.status, FilingStatus.FILED)
        self.assertEqual(second.status, FilingStatus.DUPLICATE)
        self.assertEqual(second.filed_path, first.filed_path)
        self.assertEqual(second.committed_at, COMMITTED_AT)
        self.assertEqual(second.intake_state, "filed")
        self.assertEqual(self._vault_snapshot(), after_first)
        self.assertEqual(
            len(list((self.root / "vault" / "notes" / "filed").iterdir())),
            1,
        )
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_deleted_filed_note_never_reports_duplicate(self) -> None:
        self._approved()
        self._service().file(self.capture_id)
        (self.root / self.filed_path).unlink()

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.filed_note_missing")

    def test_altered_filed_note_never_reports_duplicate(self) -> None:
        self._approved()
        self._service().file(self.capture_id)
        path = self.root / self.filed_path
        path.write_bytes(path.read_bytes() + b"appended by hand\n")

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.filed_note_missing")

    def test_crash_between_write_and_transition_resumes_on_the_identical_note(
        self,
    ) -> None:
        self._approved()
        self._service().file(self.capture_id)
        # Rewind the state exactly as a crash after the write would leave it.
        force_intake_state(
            self.store,
            self.capture_id,
            state="approved",
            state_updated_at="2026-08-02T11:00:00Z",
            failure_reason=None,
        )
        force_approval_committed_at(self.store, None)

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FILED)
        self.assertEqual(result.intake_state, "filed")
        self.assertEqual(
            len(list((self.root / "vault" / "notes" / "filed").iterdir())),
            1,
        )

    def test_a_differing_note_at_the_filed_path_fails_closed_without_repair(
        self,
    ) -> None:
        self._approved()
        squatter = self.root / self.filed_path
        squatter.parent.mkdir(parents=True, exist_ok=True)
        squatter.write_bytes(b"---\nid: squatter\n---\n\nNot ours.\n")

        result = self._service().file(self.capture_id)

        self.assertEqual(result.status, FilingStatus.FAILED)
        self.assertEqual(result.reason, "filing.note_write_failed")
        self.assertEqual(squatter.read_bytes(), b"---\nid: squatter\n---\n\nNot ours.\n")
        self.assertEqual(
            self.store.find_intake_by_capture_id(self.capture_id).state,
            "approved",
        )

    def test_filing_sets_committed_at_exactly_once(self) -> None:
        self._approved()
        self._service().file(self.capture_id)
        self._service().file(self.capture_id)

        rows = approval_rows(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].committed_at, COMMITTED_AT)


if __name__ == "__main__":
    unittest.main()

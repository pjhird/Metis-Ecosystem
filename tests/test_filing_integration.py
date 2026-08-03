from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.data_access import SQLiteStateStore
from tests.data_access.inspection import (
    approval_rows,
    audit_event_rows,
    table_row_count,
)
from tests.test_proposal_integration import PipelineAdapter


CAPTURED_TEXT = "Build a review workflow."
GOAL_NOTE = (
    b"---\nid: goal.health-baseline\ntype: goal\n"
    b"title: Establish a health baseline\nstatus: active\n"
    b"horizon: annual\ncreated: 2026-08-02\n---\n"
)


class FilingIntegrationTests(unittest.TestCase):
    def test_capture_classify_propose_approve_file_completes_the_loop(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)

            filed = self._run(root, adapter, ["file", capture_id])

            self.assertEqual(filed["status"], "filed")
            self.assertEqual(filed["capture_id"], capture_id)
            self.assertEqual(filed["links"], ["goal.health-baseline"])
            self.assertEqual(filed["intake_state"], "filed")
            self.assertIsNone(filed["reason"])

            note = root / filed["filed_path"]
            frontmatter = note.read_bytes().split(b"---\n\n", 1)[0]
            self.assertEqual(
                filed["filed_path"],
                f"vault/notes/filed/note.{capture_id}.md",
            )
            self.assertIn(b"status: approved\n", frontmatter)
            self.assertIn(b"verification: unverified\n", frontmatter)
            self.assertIn(f'capture_id: "{capture_id}"\n'.encode(), frontmatter)
            self.assertIn(
                f'  capture: "evidence/{capture_id}/raw.txt"\n'.encode(),
                frontmatter,
            )
            self.assertIn(b'links:\n  - "[[goal.health-baseline]]"\n', frontmatter)
            self.assertIn(
                b"Define the reviewable workflow before permanent filing.",
                note.read_bytes(),
            )

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                intake = store.find_intake_by_capture_id(capture_id)
                rows = approval_rows(store)
                self.assertEqual(intake.state, "filed")
                self.assertIsNone(intake.failure_reason)
                self.assertEqual(rows[0].committed_at, filed["committed_at"])
                self.assertIsNone(rows[0].revoked_at)
                self.assertEqual(table_row_count(store, "approval"), 1)
                # One event per transition; the trail is the state path this
                # capture walked. Its exact shape is asserted in test_audit.
                trail = audit_event_rows(store)
                self.assertEqual(len(trail), 8)
                self.assertEqual(trail[-1].action, "note.committed")
                self.assertEqual(trail[-1].outcome, "success")

            self.assertEqual(adapter.classification_calls, 1)
            self.assertEqual(adapter.proposal_calls, 1)
            # The draft is left in place; nothing in Step 6 deletes it.
            self.assertTrue((root / "vault" / "notes" / "proposed").is_dir())

    def test_duplicate_replay_creates_one_note(self) -> None:
        """The identical typed input, twice, produces exactly one filed note."""
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)
            self._run(root, adapter, ["file", capture_id])
            filed_directory = root / "vault" / "notes" / "filed"
            after_first = sorted(path.name for path in filed_directory.iterdir())

            stdout, stderr, _ = self._capture(root, adapter, ["capture", CAPTURED_TEXT])
            replayed_capture = json.loads(stderr or stdout)
            replayed_file = self._run(root, adapter, ["file", capture_id])

            # Step 2 refuses a replay once the intake advances past `captured`
            # (`_row_matches_evidence`). It still resolves to the original
            # capture and writes nothing, which is what REQ-INTK-002 requires;
            # the reason code it reports is a step-2 concern, not a step-6 one.
            self.assertEqual(replayed_capture["capture_id"], capture_id)
            self.assertEqual(replayed_file["status"], "duplicate")
            self.assertEqual(
                sorted(path.name for path in filed_directory.iterdir()),
                after_first,
            )
            self.assertEqual(len(after_first), 1)

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                self.assertEqual(table_row_count(store, "intake"), 1)
                self.assertEqual(table_row_count(store, "proposal"), 1)
                self.assertEqual(table_row_count(store, "approval"), 1)
                trail = audit_event_rows(store)
                self.assertEqual(
                    [event.action for event in trail].count("note.committed"),
                    1,
                )
                self.assertEqual(
                    (trail[-1].action, trail[-1].outcome),
                    ("command.file", "refused"),
                )
            self.assertEqual(adapter.classification_calls, 1)
            self.assertEqual(adapter.proposal_calls, 1)

    def test_filing_an_unapproved_capture_writes_nothing_and_exits_zero(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])[
                "capture_id"
            ]
            self._run(root, adapter, ["classify", capture_id])
            self._run(root, adapter, ["propose", capture_id])

            refused = self._run(root, adapter, ["file", capture_id])

            self.assertEqual(refused["status"], "refused")
            self.assertEqual(refused["reason"], "filing.not_approved")
            self.assertEqual(refused["intake_state"], "awaiting_approval")
            self.assertFalse((root / "vault" / "notes" / "filed").exists())

    def test_file_shell_outcomes_use_stable_json_streams_and_codes(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)

            stdout, stderr, code = self._capture(root, adapter, ["file", capture_id])
            payload = json.loads(stdout)
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["status"], "filed")
            self.assertEqual(
                set(payload),
                {
                    "status",
                    "capture_id",
                    "proposal_id",
                    "approval_id",
                    "filed_path",
                    "links",
                    "committed_at",
                    "intake_state",
                    "reason",
                    "message",
                },
            )

            (root / payload["filed_path"]).unlink()
            stdout, stderr, code = self._capture(root, adapter, ["file", capture_id])
            failure = json.loads(stderr)
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["reason"], "filing.filed_note_missing")

    def test_state_initialization_failure_reports_a_filing_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "state").write_bytes(b"not a directory\n")

            stdout, stderr, code = self._capture(
                root,
                PipelineAdapter(),
                ["file", "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"],
            )

            payload = json.loads(stderr)
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "state_initialization_failed")
            self.assertEqual(
                payload["capture_id"],
                "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
            )

    def _approve(self, root: Path, adapter: PipelineAdapter) -> str:
        capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])["capture_id"]
        self._run(root, adapter, ["classify", capture_id])
        proposed = self._run(root, adapter, ["propose", capture_id])
        goal = root / "vault" / "goals" / "goal.health-baseline.md"
        goal.parent.mkdir(parents=True, exist_ok=True)
        goal.write_bytes(GOAL_NOTE)
        draft = root / proposed["draft_path"]
        draft.write_bytes(
            draft.read_bytes()
            .replace(b"links: []\n", b'links:\n  - "[[goal.health-baseline]]"\n', 1)
            .replace(b"status: proposed\n", b"status: approved\n", 1)
        )
        self._run(root, adapter, ["approvals"])
        return capture_id

    def _capture(
        self,
        root: Path,
        adapter: PipelineAdapter,
        argv: list[str],
    ) -> tuple[str, str, int]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv, runtime_root=root, model_adapter_factory=lambda: adapter)
        return stdout.getvalue(), stderr.getvalue(), code

    def _run(self, root: Path, adapter: PipelineAdapter, argv: list[str]) -> dict:
        stdout, stderr, code = self._capture(root, adapter, argv)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        return json.loads(stdout)


if __name__ == "__main__":
    unittest.main()

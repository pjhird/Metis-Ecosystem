from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from metis.approval import (
    APPROVER,
    ApprovalRunStatus,
    ApprovalService,
    ApprovalStatus,
)
from metis.data_access import SQLiteStateStore
from metis.draft_notes import DraftNoteStore, render_proposed_draft
from metis.proposal_content import ProposalContentStore
from tests.data_access.inspection import (
    approval_rows,
    force_proposal_draft_path,
    table_row_count,
)
from tests.data_access.test_proposal_store import (
    CLASSIFICATION_ID,
    CLASSIFIED_AT,
    PROPOSAL_ID,
    classification_record,
    intake_record,
    proposal_record,
    reservation_record,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
DRAFT_AT = "2026-08-02T10:15:05Z"
DETECTED_AT = "2026-08-02T11:00:00Z"
DRAFT_PATH = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
RAW_RESPONSE_HASH = "c" * 64
BODY_BYTES = (
    b"Define the reviewable workflow.\n\n## Proposal rationale\n"
    b"It follows from the captured idea.\n\n## Uncertainties\n"
    b"None identified by the proposal model.\n"
)
CONTENT_HASH = hashlib.sha256(BODY_BYTES).hexdigest()


class ApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = SQLiteStateStore(self.root / "state" / "metis.db")
        self.addCleanup(self.store.close)
        self.store.initialize()
        self.identifiers = iter(
            f"01K1D5Q5M0000000000000{index:04d}" for index in range(100)
        )

    def _service(self) -> ApprovalService:
        return ApprovalService(
            self.store,
            ProposalContentStore(self.root),
            DraftNoteStore(self.root),
            self.root,
            id_factory=lambda: next(self.identifiers),
            clock=lambda: datetime(2026, 8, 2, 11, 0, 0, tzinfo=timezone.utc),
        )

    def _awaiting_approval(self, capture_id: str = CAPTURE_ID) -> None:
        """Reproduce the exact end state Step 4 leaves behind."""
        proposal_id = PROPOSAL_ID if capture_id == CAPTURE_ID else next(
            self.identifiers
        )
        classification_id = (
            CLASSIFICATION_ID if capture_id == CAPTURE_ID else next(self.identifiers)
        )
        content_hash = hashlib.sha256(
            f"sha256:{capture_id}".encode("utf-8")
        ).hexdigest()
        self.store.register_intake(
            intake_record(capture_id=capture_id, content_hash=content_hash)
        )
        self.store.begin_classification(capture_id, CLASSIFIED_AT)
        self.store.complete_classification(
            classification_record(
                classification_id=classification_id,
                capture_id=capture_id,
                raw_response_path=(
                    f"classification-evidence/{classification_id}/raw-response.txt"
                ),
            )
        )
        self.store.begin_proposal(
            reservation_record(
                proposal_id=proposal_id,
                capture_id=capture_id,
                classification_id=classification_id,
                lease_token=str(uuid4()),
            )
        )
        reservation = self.store.find_proposal_reservation_by_capture_id(capture_id)
        proposal = proposal_record(
            proposal_id=proposal_id,
            capture_id=capture_id,
            classification_id=classification_id,
            body_path=f"proposal-content/{proposal_id}/body.md",
            raw_response_path=f"proposal-evidence/{proposal_id}/raw-response.txt",
            content_hash=CONTENT_HASH,
        )
        self.store.complete_proposal(proposal, reservation.lease_token)
        ProposalContentStore(self.root).create(
            proposal_id,
            classification_id,
            capture_id,
            RAW_RESPONSE_HASH,
            BODY_BYTES,
        )
        draft_path = f"vault/notes/proposed/note.{capture_id}.md"
        registered = self.store.register_proposal_draft(
            capture_id,
            proposal_id,
            draft_path,
            DRAFT_AT,
        )
        DraftNoteStore(self.root).create(
            draft_path,
            render_proposed_draft(registered, BODY_BYTES),
        )

    def _set_draft_status(self, status: str, capture_id: str = CAPTURE_ID) -> None:
        path = self.root / f"vault/notes/proposed/note.{capture_id}.md"
        path.write_bytes(
            path.read_bytes().replace(
                b"status: proposed\n",
                f"status: {status}\n".encode("utf-8"),
                1,
            )
        )

    def _set_draft_links(self, block: bytes, capture_id: str = CAPTURE_ID) -> None:
        path = self.root / f"vault/notes/proposed/note.{capture_id}.md"
        path.write_bytes(path.read_bytes().replace(b"links: []\n", block, 1))

    def _vault_snapshot(self) -> dict[str, bytes]:
        vault = self.root / "vault"
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in sorted(vault.rglob("*"))
            if path.is_file()
        }

    def test_approved_status_records_one_decision_and_transitions_intake(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("approved")

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(len(run.decisions), 1)
        decision = run.decisions[0]
        self.assertEqual(decision.status, ApprovalStatus.APPROVED)
        self.assertEqual(decision.capture_id, CAPTURE_ID)
        self.assertEqual(decision.proposal_id, PROPOSAL_ID)
        self.assertEqual(decision.decision, "approved")
        self.assertEqual(decision.observed_status, "approved")
        self.assertEqual(decision.approver, APPROVER)
        self.assertEqual(decision.draft_path, DRAFT_PATH)
        self.assertEqual(decision.intake_state, "approved")
        self.assertEqual(decision.detected_at, DETECTED_AT)
        self.assertIsNone(decision.reason)

        rows = approval_rows(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].approval_id, decision.approval_id)
        self.assertEqual(rows[0].approver, "human:owner")
        self.assertEqual(rows[0].observed_status, "approved")
        self.assertIsNone(rows[0].committed_at)
        self.assertIsNone(rows[0].revoked_at)
        self.assertEqual(
            self.store.find_proposal_by_capture_id(CAPTURE_ID).state,
            "approved",
        )
        self.assertFalse((self.root / "vault" / "notes" / "filed").exists())

    def test_hand_authored_links_are_approved_and_reported(self) -> None:
        self._awaiting_approval()
        self._set_draft_links(
            b'links:\n  - "[[goal.health-baseline]]"\n  - "[[proj.metis-core]]"\n'
        )
        self._set_draft_status("approved")

        run = self._service().review()

        decision = run.decisions[0]
        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(decision.status, ApprovalStatus.APPROVED)
        self.assertEqual(
            decision.observed_links,
            ("goal.health-baseline", "proj.metis-core"),
        )
        self.assertEqual(len(approval_rows(self.store)), 1)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "approved",
        )

    def test_links_added_without_a_status_change_stay_pending(self) -> None:
        self._awaiting_approval()
        self._set_draft_links(b'links:\n  - "[[goal.health-baseline]]"\n')

        run = self._service().review()

        self.assertEqual(run.decisions[0].status, ApprovalStatus.PENDING)
        self.assertEqual(run.decisions[0].observed_links, ("goal.health-baseline",))
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_malformed_links_fail_closed_without_recording(self) -> None:
        self._awaiting_approval()
        self._set_draft_links(b'links:\n  - "[[goal one]]"\n')
        self._set_draft_status("approved")

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.decisions[0].reason, "approval_draft_inconsistent")
        self.assertEqual(run.decisions[0].observed_links, ())
        self.assertEqual(table_row_count(self.store, "approval"), 0)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "awaiting_approval",
        )

    def test_rejected_status_is_a_recorded_successful_outcome(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("rejected")

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(run.decisions[0].status, ApprovalStatus.REJECTED)
        self.assertEqual(run.decisions[0].intake_state, "rejected")
        self.assertEqual(approval_rows(self.store)[0].decision, "rejected")
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "rejected",
        )

    def test_proposed_status_stays_pending_without_an_approval_record(self) -> None:
        self._awaiting_approval()

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(run.decisions[0].status, ApprovalStatus.PENDING)
        self.assertEqual(run.decisions[0].observed_status, "proposed")
        self.assertIsNone(run.decisions[0].approval_id)
        self.assertIsNone(run.decisions[0].decision)
        self.assertEqual(table_row_count(self.store, "approval"), 0)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "awaiting_approval",
        )

    def test_empty_queue_completes_without_decisions(self) -> None:
        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(run.decisions, ())
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_draft_edited_outside_status_fails_closed_without_recording(self) -> None:
        self._awaiting_approval()
        path = self.root / DRAFT_PATH
        path.write_bytes(
            path.read_bytes()
            .replace(b"status: proposed\n", b"status: approved\n", 1)
            .replace(b"verification: unverified\n", b"verification: verified\n", 1)
        )

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.decisions[0].status, ApprovalStatus.FAILED)
        self.assertEqual(run.decisions[0].reason, "approval_draft_inconsistent")
        self.assertIsNone(run.decisions[0].observed_status)
        self.assertEqual(table_row_count(self.store, "approval"), 0)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "awaiting_approval",
        )

    def test_missing_draft_fails_closed(self) -> None:
        self._awaiting_approval()
        (self.root / DRAFT_PATH).unlink()

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.decisions[0].reason, "approval_draft_inconsistent")
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_content_disagreement_fails_closed(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("approved")
        body = self.root / f"proposal-content/{PROPOSAL_ID}/body.md"
        body.write_bytes(BODY_BYTES + b"Tampered.\n")

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.decisions[0].reason, "approval_content_inconsistent")
        self.assertEqual(table_row_count(self.store, "approval"), 0)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "awaiting_approval",
        )

    def test_unregistered_draft_path_fails_closed(self) -> None:
        self._awaiting_approval()
        force_proposal_draft_path(
            self.store,
            PROPOSAL_ID,
            "vault/notes/proposed/note.other.md",
        )

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.decisions[0].reason, "approval_proposal_inconsistent")
        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_approval_run_writes_nothing_to_the_vault(self) -> None:
        for status in ("proposed", "approved", "rejected"):
            with self.subTest(status=status):
                self.setUp()
                self._awaiting_approval()
                if status != "proposed":
                    self._set_draft_status(status)
                before = self._vault_snapshot()

                self._service().review()

                self.assertEqual(self._vault_snapshot(), before)
                self.assertFalse((self.root / "vault" / "notes" / "filed").exists())

    def test_note_written_directly_to_the_vault_is_not_treated_as_approved(
        self,
    ) -> None:
        rogue_capture = "3f6ca1b8-4b2e-4a4c-9d2f-1c7b0e5a9d41"
        rogue_path = self.root / f"vault/notes/proposed/note.{rogue_capture}.md"
        rogue_path.parent.mkdir(parents=True, exist_ok=True)
        rogue_bytes = (
            "---\n"
            f'id: "note.{rogue_capture}"\n'
            "type: idea\n"
            'title: "Unauthorized"\n'
            "status: approved\n"
            "verification: verified\n"
            f'capture_id: "{rogue_capture}"\n'
            "links: []\n"
            "---\n\nSmuggled knowledge.\n"
        ).encode("utf-8")
        rogue_path.write_bytes(rogue_bytes)

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(run.decisions, ())
        self.assertEqual(table_row_count(self.store, "approval"), 0)
        self.assertEqual(table_row_count(self.store, "intake"), 0)
        self.assertEqual(rogue_path.read_bytes(), rogue_bytes)

    def test_second_run_after_a_decision_records_no_second_approval(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("approved")
        first = self._service().review()

        second = self._service().review()

        self.assertEqual(first.decisions[0].status, ApprovalStatus.APPROVED)
        self.assertEqual(second.status, ApprovalRunStatus.COMPLETED)
        self.assertEqual(second.decisions, ())
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_status_reverted_after_approval_records_no_second_decision(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("approved")
        self._service().review()
        path = self.root / DRAFT_PATH
        path.write_bytes(
            path.read_bytes().replace(b"status: approved\n", b"status: rejected\n", 1)
        )

        run = self._service().review()

        self.assertEqual(run.decisions, ())
        rows = approval_rows(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].decision, "approved")
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "approved",
        )

    def test_mixed_queue_reports_failed_without_losing_valid_decisions(self) -> None:
        broken_capture = "1c2f9a70-5d31-4c8b-8f2e-6a9d4b3c8e15"
        self._awaiting_approval()
        self._awaiting_approval(broken_capture)
        self._set_draft_status("approved")
        (self.root / f"vault/notes/proposed/note.{broken_capture}.md").unlink()

        run = self._service().review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        outcomes = {
            decision.capture_id: decision.status for decision in run.decisions
        }
        self.assertEqual(outcomes[CAPTURE_ID], ApprovalStatus.APPROVED)
        self.assertEqual(outcomes[broken_capture], ApprovalStatus.FAILED)
        self.assertEqual(table_row_count(self.store, "approval"), 1)
        self.assertEqual(
            self.store.find_intake_by_capture_id(broken_capture).state,
            "awaiting_approval",
        )

    def test_approval_emits_one_audit_event_and_no_permanent_note(self) -> None:
        self._awaiting_approval()
        self._set_draft_status("approved")

        self._service().review()

        # The fixture drives the store directly and audits nothing, so the one
        # event here is the decision the service itself recorded.
        self.assertEqual(table_row_count(self.store, "audit_event"), 1)
        self.assertFalse((self.root / "vault" / "notes" / "filed").exists())
        self.assertEqual(
            sorted(
                path.name
                for path in (self.root / "vault" / "notes" / "proposed").iterdir()
            ),
            [f"note.{CAPTURE_ID}.md"],
        )

    def test_state_lookup_failure_is_an_honest_run_failure(self) -> None:
        class BrokenStore:
            def find_intakes_awaiting_approval(self):
                from metis.data_access import StateStoreError

                raise StateStoreError("queue unavailable")

        service = ApprovalService(
            BrokenStore(),
            ProposalContentStore(self.root),
            DraftNoteStore(self.root),
            self.root,
        )

        run = service.review()

        self.assertEqual(run.status, ApprovalRunStatus.FAILED)
        self.assertEqual(run.reason, "approval_state_undetermined")
        self.assertEqual(run.decisions, ())


if __name__ == "__main__":
    unittest.main()

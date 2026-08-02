from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from metis.data_access import (
    ApprovalRecord,
    SQLiteStateStore,
    StateStoreError,
    StateTransitionRefused,
)
from tests.data_access.inspection import approval_rows, table_row_count
from tests.data_access.test_proposal_store import (
    CAPTURE_ID,
    CLASSIFIED_AT,
    LEASE_TOKEN,
    PROPOSAL_ID,
    classification_record,
    intake_record,
    proposal_record,
    reservation_record,
)


APPROVAL_ID = "01K1D5Q5M00000000000000002"
DRAFT_AT = "2026-08-02T10:15:05Z"
DETECTED_AT = "2026-08-02T11:00:00Z"
DRAFT_PATH = f"vault/notes/proposed/note.{CAPTURE_ID}.md"


def approval_record(**changes: object) -> ApprovalRecord:
    values = {
        "approval_id": APPROVAL_ID,
        "proposal_id": PROPOSAL_ID,
        "decision": "approved",
        "approver": "human:owner",
        "observed_status": "approved",
        "detected_at": DETECTED_AT,
        "committed_at": None,
        "revoked_at": None,
    }
    values.update(changes)
    return ApprovalRecord(**values)


class ApprovalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = SQLiteStateStore(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _awaiting_approval(self) -> None:
        self.store.register_intake(intake_record())
        self.store.begin_classification(CAPTURE_ID, CLASSIFIED_AT)
        self.store.complete_classification(classification_record())
        self.store.begin_proposal(reservation_record())
        self.store.complete_proposal(proposal_record(), LEASE_TOKEN)
        self.store.register_proposal_draft(
            CAPTURE_ID,
            PROPOSAL_ID,
            DRAFT_PATH,
            DRAFT_AT,
        )

    def _force_intake_state(
        self,
        *,
        state: str,
        state_updated_at: str,
        failure_reason: str | None = None,
    ) -> None:
        cursor = self.store._connection.execute(
            "UPDATE intake SET state = ?, state_updated_at = ?, failure_reason = ? "
            "WHERE capture_id = ?",
            (state, state_updated_at, failure_reason, CAPTURE_ID),
        )
        self.assertEqual(cursor.rowcount, 1)
        self.store._connection.commit()

    def _force_proposal(self, **columns: object) -> None:
        assignments = ", ".join(f"{column} = ?" for column in columns)
        cursor = self.store._connection.execute(
            f"UPDATE proposal SET {assignments} WHERE proposal_id = ?",
            (*columns.values(), PROPOSAL_ID),
        )
        self.assertEqual(cursor.rowcount, 1)
        self.store._connection.commit()

    def test_awaiting_approval_queue_lists_only_registered_drafts(self) -> None:
        self.assertEqual(self.store.find_intakes_awaiting_approval(), ())

        self._awaiting_approval()

        queue = self.store.find_intakes_awaiting_approval()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0].capture_id, CAPTURE_ID)
        self.assertEqual(queue[0].state, "awaiting_approval")

    def test_approved_decision_transitions_intake_and_proposal_atomically(self) -> None:
        self._awaiting_approval()

        intake = self.store.record_approval(approval_record())

        self.assertEqual(intake.state, "approved")
        self.assertEqual(intake.state_updated_at, DETECTED_AT)
        self.assertIsNone(intake.failure_reason)
        proposal = self.store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertEqual(proposal.state, "approved")
        self.assertEqual(proposal.draft_note_path, DRAFT_PATH)
        rows = approval_rows(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], approval_record())
        self.assertIsNone(rows[0].committed_at)
        self.assertIsNone(rows[0].revoked_at)
        self.assertEqual(self.store.find_intakes_awaiting_approval(), ())

    def test_rejected_decision_is_recorded_as_a_terminal_outcome(self) -> None:
        self._awaiting_approval()

        intake = self.store.record_approval(
            approval_record(decision="rejected", observed_status="rejected")
        )

        self.assertEqual(intake.state, "rejected")
        self.assertEqual(
            self.store.find_proposal_by_capture_id(CAPTURE_ID).state,
            "rejected",
        )
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_illegal_intake_states_are_rejected_for_approval(self) -> None:
        for state, failure_reason in (
            ("captured", None),
            ("classifying", None),
            ("classified", None),
            ("proposing", None),
            ("proposed", None),
            ("approved", None),
            ("rejected", None),
            ("filed", None),
            ("failed", "proposal.draft_write_failed"),
        ):
            with self.subTest(state=state):
                self.setUp()
                self._awaiting_approval()
                self._force_intake_state(
                    state=state,
                    state_updated_at=DRAFT_AT,
                    failure_reason=failure_reason,
                )

                with self.assertRaises(StateTransitionRefused):
                    self.store.record_approval(approval_record())

                self.assertEqual(table_row_count(self.store, "approval"), 0)
                self.assertEqual(
                    self.store.find_proposal_by_capture_id(CAPTURE_ID).state,
                    "pending",
                )
                self.assertEqual(
                    self.store.find_intake_by_capture_id(CAPTURE_ID).state,
                    state,
                )

    def test_awaiting_approval_with_a_failure_reason_is_refused(self) -> None:
        self._awaiting_approval()
        self._force_intake_state(
            state="awaiting_approval",
            state_updated_at=DRAFT_AT,
            failure_reason="proposal.draft_collision",
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.record_approval(approval_record())

        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_approval_requires_a_registered_pending_proposal(self) -> None:
        cases = (
            {"draft_note_path": None},
            {"state": "approved"},
            {"state": "rejected"},
            {"state": "superseded"},
        )
        for columns in cases:
            with self.subTest(columns=columns):
                self.setUp()
                self._awaiting_approval()
                self._force_proposal(**columns)

                with self.assertRaises(StateTransitionRefused):
                    self.store.record_approval(approval_record())

                self.assertEqual(table_row_count(self.store, "approval"), 0)
                self.assertEqual(
                    self.store.find_intake_by_capture_id(CAPTURE_ID).state,
                    "awaiting_approval",
                )

    def test_approval_for_an_unknown_proposal_is_refused(self) -> None:
        self._awaiting_approval()

        with self.assertRaises((StateStoreError, StateTransitionRefused)):
            self.store.record_approval(
                approval_record(proposal_id="01K1D5Q5M0000000000000FFFF")
            )

        self.assertEqual(table_row_count(self.store, "approval"), 0)

    def test_inconsistent_approval_records_are_refused_before_any_write(self) -> None:
        cases = (
            approval_record(decision="proposed", observed_status="proposed"),
            approval_record(observed_status="proposed"),
            approval_record(decision="rejected"),
            approval_record(approver="agent:classifier"),
            approval_record(committed_at=DETECTED_AT),
            approval_record(revoked_at=DETECTED_AT),
        )
        for record in cases:
            with self.subTest(record=record):
                self.setUp()
                self._awaiting_approval()

                with self.assertRaises((StateStoreError, StateTransitionRefused)):
                    self.store.record_approval(record)

                self.assertEqual(table_row_count(self.store, "approval"), 0)
                self.assertEqual(
                    self.store.find_intake_by_capture_id(CAPTURE_ID).state,
                    "awaiting_approval",
                )

    def test_second_approval_for_one_proposal_is_refused(self) -> None:
        self._awaiting_approval()
        self.store.record_approval(approval_record())

        with self.assertRaises(StateTransitionRefused):
            self.store.record_approval(
                approval_record(approval_id="01K1D5Q5M00000000000000003")
            )

        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_duplicate_approval_is_refused_by_sqlite(self) -> None:
        self._awaiting_approval()
        self.store.record_approval(approval_record())

        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute(
                "INSERT INTO approval (approval_id, proposal_id, decision, approver, "
                "observed_status, detected_at, committed_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "01K1D5Q5M00000000000000004",
                    PROPOSAL_ID,
                    "rejected",
                    "human:owner",
                    "rejected",
                    DETECTED_AT,
                    None,
                    None,
                ),
            )
        self.store._connection.rollback()
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_concurrent_approval_leaves_exactly_one_decision(self) -> None:
        self._awaiting_approval()
        competitor = SQLiteStateStore(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.addCleanup(competitor.close)
        competitor.initialize()

        self.store.record_approval(approval_record())
        with self.assertRaises(StateTransitionRefused):
            competitor.record_approval(
                approval_record(
                    approval_id="01K1D5Q5M00000000000000005",
                    decision="rejected",
                    observed_status="rejected",
                )
            )

        self.assertEqual(table_row_count(self.store, "approval"), 1)
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "approved",
        )


if __name__ == "__main__":
    unittest.main()

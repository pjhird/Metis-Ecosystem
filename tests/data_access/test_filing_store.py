from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from metis.data_access import (
    SQLiteStateStore,
    StateStoreError,
    StateTransitionRefused,
)
from tests.data_access.inspection import approval_rows, table_row_count
from tests.data_access.test_approval_store import (
    APPROVAL_ID,
    DETECTED_AT,
    DRAFT_AT,
    DRAFT_PATH,
    approval_record,
)
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


COMMITTED_AT = "2026-08-02T12:00:00Z"
OTHER_APPROVAL_ID = "01K1D5Q5M00000000000000009"


class FilingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = SQLiteStateStore(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _approved(self) -> None:
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
        self.store.record_approval(approval_record())

    def _file(self, **changes: object):
        arguments = {
            "capture_id": CAPTURE_ID,
            "proposal_id": PROPOSAL_ID,
            "approval_id": APPROVAL_ID,
            "committed_at": COMMITTED_AT,
        }
        arguments.update(changes)
        return self.store.record_filing(**arguments)

    def _force_intake_state(
        self,
        *,
        state: str,
        state_updated_at: str = DETECTED_AT,
        failure_reason: str | None = None,
    ) -> None:
        cursor = self.store._connection.execute(
            "UPDATE intake SET state = ?, state_updated_at = ?, failure_reason = ? "
            "WHERE capture_id = ?",
            (state, state_updated_at, failure_reason, CAPTURE_ID),
        )
        self.assertEqual(cursor.rowcount, 1)
        self.store._connection.commit()

    def _refused(self, **changes: object) -> None:
        before = approval_rows(self.store)
        with self.assertRaises((StateStoreError, StateTransitionRefused)):
            self._file(**changes)
        self.assertEqual(approval_rows(self.store), before)
        self.assertNotEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "filed",
        )

    def test_find_approval_by_proposal_id_returns_the_recorded_decision(self) -> None:
        self.assertIsNone(self.store.find_approval_by_proposal_id(PROPOSAL_ID))

        self._approved()

        approval = self.store.find_approval_by_proposal_id(PROPOSAL_ID)
        self.assertEqual(approval, approval_record())
        self.assertIsNone(self.store.find_approval_by_proposal_id(OTHER_APPROVAL_ID))

    def test_filing_commits_the_approval_and_marks_the_intake_filed(self) -> None:
        self._approved()

        updated = self._file()

        self.assertEqual(updated.state, "filed")
        self.assertEqual(updated.state_updated_at, COMMITTED_AT)
        self.assertIsNone(updated.failure_reason)
        rows = approval_rows(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].committed_at, COMMITTED_AT)
        self.assertIsNone(rows[0].revoked_at)
        self.assertEqual(table_row_count(self.store, "approval"), 1)

    def test_filing_twice_is_refused(self) -> None:
        self._approved()
        self._file()

        with self.assertRaises(StateTransitionRefused):
            self._file()

        self.assertEqual(approval_rows(self.store)[0].committed_at, COMMITTED_AT)

    def test_illegal_intake_states_are_rejected_for_filing(self) -> None:
        illegal = (
            "captured",
            "classifying",
            "classified",
            "proposing",
            "proposed",
            "awaiting_approval",
            "rejected",
            "failed",
            "filed",
        )
        for state in illegal:
            with self.subTest(state=state):
                self.setUp()
                self._approved()
                self._force_intake_state(state=state)

                with self.assertRaises(StateTransitionRefused):
                    self._file()

                self.assertIsNone(approval_rows(self.store)[0].committed_at)
                self.assertEqual(
                    self.store.find_intake_by_capture_id(CAPTURE_ID).state,
                    state,
                )

    def test_an_approved_intake_carrying_a_failure_reason_is_refused(self) -> None:
        self._approved()
        self._force_intake_state(state="approved", failure_reason="unexpected")

        self._refused()

    def test_filing_requires_an_approved_proposal(self) -> None:
        for state in ("pending", "rejected", "superseded"):
            with self.subTest(state=state):
                self.setUp()
                self._approved()
                self.store._connection.execute(
                    "UPDATE proposal SET state = ? WHERE proposal_id = ?",
                    (state, PROPOSAL_ID),
                )
                self.store._connection.commit()

                self._refused()

    def test_an_approval_from_another_proposal_cannot_authorize_this_filing(
        self,
    ) -> None:
        self._approved()

        self._refused(approval_id=OTHER_APPROVAL_ID)

    def test_a_mismatched_proposal_id_is_refused(self) -> None:
        self._approved()

        self._refused(proposal_id="01K1D5Q5M00000000000000008")

    def test_an_already_committed_approval_cannot_be_committed_again(self) -> None:
        self._approved()
        self.store._connection.execute(
            "UPDATE approval SET committed_at = ? WHERE approval_id = ?",
            ("2026-08-02T11:59:00Z", APPROVAL_ID),
        )
        self.store._connection.commit()

        self._refused()

    def test_a_revoked_approval_cannot_authorize_a_filing(self) -> None:
        self._approved()
        self.store._connection.execute(
            "UPDATE approval SET revoked_at = ? WHERE approval_id = ?",
            ("2026-08-02T11:59:00Z", APPROVAL_ID),
        )
        self.store._connection.commit()

        self._refused()

    def test_a_rejected_decision_cannot_authorize_a_filing(self) -> None:
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
        self.store.record_approval(
            approval_record(decision="rejected", observed_status="rejected")
        )
        self._force_intake_state(state="approved")

        self._refused()

    def test_a_commit_timestamp_before_the_approval_is_refused(self) -> None:
        self._approved()

        self._refused(committed_at="2026-08-02T10:00:00Z")

    def test_an_unknown_capture_is_refused(self) -> None:
        self._approved()

        with self.assertRaises(StateStoreError):
            self._file(capture_id="3f6ca1b8-4b2e-4a4c-9d2f-1c7b0e5a9d41")

        self.assertIsNone(approval_rows(self.store)[0].committed_at)


if __name__ == "__main__":
    unittest.main()

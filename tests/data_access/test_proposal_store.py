from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Callable

from metis.data_access import (
    ClassificationRecord,
    IntakeRecord,
    ProposalRecord,
    ProposalReservationRecord,
    SQLiteStateStore,
    StateStoreError,
    StateTransitionRefused,
)
from tests.data_access.inspection import table_row_count


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CAPTURED_AT = "2026-08-02T10:00:00Z"
CLASSIFIED_AT = "2026-08-02T10:00:01Z"
RESERVED_AT = "2026-08-02T10:00:02Z"
EXPIRES_AT = "2026-08-02T10:15:02Z"
RECLAIMED_AT = "2026-08-02T10:15:03Z"
RECLAIM_EXPIRES_AT = "2026-08-02T10:30:03Z"
COMPLETED_AT = "2026-08-02T10:15:04Z"
DRAFT_AT = "2026-08-02T10:15:05Z"
LEASE_TOKEN = "f52c0470-93d3-4a47-a864-9e504cf1bfcd"
REPLACEMENT_TOKEN = "f45a1b74-2c02-4cf2-8321-fcf321f9bfdd"


def intake_record(**changes: object) -> IntakeRecord:
    values = {
        "capture_id": CAPTURE_ID,
        "content_hash": "sha256:" + "a" * 64,
        "captured_at": CAPTURED_AT,
        "source_type": "cli-typed",
        "evidence_path": f"evidence/{CAPTURE_ID}",
        "state": "captured",
        "state_updated_at": CAPTURED_AT,
        "failure_reason": None,
        "trace_id": CAPTURE_ID,
        "type_pin": "",
        "parent_id": "",
    }
    values.update(changes)
    return IntakeRecord(**values)


def classification_record(**changes: object) -> ClassificationRecord:
    values = {
        "classification_id": CLASSIFICATION_ID,
        "capture_id": CAPTURE_ID,
        "candidate_type": "idea",
        "sensitivity": "normal",
        "confidence": 0.82,
        "routing": "proposal:idea",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "classify-v1",
        "raw_response_path": (
            f"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt"
        ),
        "created_at": CLASSIFIED_AT,
    }
    values.update(changes)
    return ClassificationRecord(**values)


def reservation_record(**changes: object) -> ProposalReservationRecord:
    values = {
        "proposal_id": PROPOSAL_ID,
        "capture_id": CAPTURE_ID,
        "classification_id": CLASSIFICATION_ID,
        "lease_token": LEASE_TOKEN,
        "reserved_at": RESERVED_AT,
        "lease_expires_at": EXPIRES_AT,
    }
    values.update(changes)
    return ProposalReservationRecord(**values)


def proposal_record(**changes: object) -> ProposalRecord:
    values = {
        "proposal_id": PROPOSAL_ID,
        "capture_id": CAPTURE_ID,
        "classification_id": CLASSIFICATION_ID,
        "note_type": "idea",
        "title": "Review me",
        "body_path": f"proposal-content/{PROPOSAL_ID}/body.md",
        "proposed_links": "[]",
        "evidence_refs": (
            f'["evidence/{CAPTURE_ID}/raw.txt",'
            f'"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt",'
            f'"proposal-evidence/{PROPOSAL_ID}/raw-response.txt"]'
        ),
        "confidence": 0.82,
        "sensitivity": "normal",
        "risk_level": "low",
        "reason": "It follows from the captured idea.",
        "uncertainties_json": "[]",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "propose-v1",
        "raw_response_path": (
            f"proposal-evidence/{PROPOSAL_ID}/raw-response.txt"
        ),
        "content_hash": "b" * 64,
        "draft_note_path": None,
        "state": "pending",
        "created_at": COMPLETED_AT,
    }
    values.update(changes)
    return ProposalRecord(**values)


class ProposalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = SQLiteStateStore(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _classified(self) -> None:
        self.store.register_intake(intake_record())
        self.store.begin_classification(CAPTURE_ID, CLASSIFIED_AT)
        self.store.complete_classification(classification_record())

    def _reserved(self) -> ProposalReservationRecord:
        self._classified()
        return self.store.begin_proposal(reservation_record())

    def _completed(self) -> ProposalRecord:
        self._reserved()
        return self.store.complete_proposal(proposal_record(), LEASE_TOKEN)

    def _force_intake_state(
        self,
        *,
        state: str,
        state_updated_at: str,
        failure_reason: str | None,
    ) -> None:
        cursor = self.store._connection.execute(
            "UPDATE intake SET state = ?, state_updated_at = ?, failure_reason = ? "
            "WHERE capture_id = ?",
            (state, state_updated_at, failure_reason, CAPTURE_ID),
        )
        self.assertEqual(cursor.rowcount, 1)
        self.store._connection.commit()

    def _new_store(self, name: str) -> SQLiteStateStore:
        store = SQLiteStateStore(
            Path(self.temporary_directory.name) / f"{name}.db"
        )
        self.addCleanup(store.close)
        store.initialize()
        return store

    def _classified_store(self, name: str) -> SQLiteStateStore:
        store = self._new_store(name)
        store.register_intake(intake_record())
        store.begin_classification(CAPTURE_ID, CLASSIFIED_AT)
        store.complete_classification(classification_record())
        return store

    def _reserved_store(self, name: str) -> SQLiteStateStore:
        store = self._classified_store(name)
        store.begin_proposal(reservation_record())
        return store

    def _completed_store(self, name: str) -> SQLiteStateStore:
        store = self._reserved_store(name)
        store.complete_proposal(proposal_record(), LEASE_TOKEN)
        return store

    def _force_store_intake_state(
        self,
        store: SQLiteStateStore,
        state: str,
        state_updated_at: str,
        failure_reason: str | None,
    ) -> None:
        cursor = store._connection.execute(
            "UPDATE intake SET state = ?, state_updated_at = ?, failure_reason = ? "
            "WHERE capture_id = ?",
            (state, state_updated_at, failure_reason, CAPTURE_ID),
        )
        self.assertEqual(cursor.rowcount, 1)
        store._connection.commit()

    def _snapshot(self, store: SQLiteStateStore) -> tuple[object, ...]:
        return (
            store.find_intake_by_capture_id(CAPTURE_ID),
            store.find_classification_by_capture_id(CAPTURE_ID),
            store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            store.find_proposal_by_capture_id(CAPTURE_ID),
            tuple(
                table_row_count(store, table)
                for table in (
                    "intake",
                    "classification",
                    "proposal_reservation",
                    "proposal",
                    "approval",
                    "audit_event",
                )
            ),
        )

    def test_proposal_lookups_return_none_when_absent(self) -> None:
        self._classified()

        self.assertIsNone(self.store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertIsNone(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )

    def test_begin_proposal_reserves_and_transitions_atomically(self) -> None:
        self._classified()
        expected = reservation_record()

        result = self.store.begin_proposal(expected)

        self.assertEqual(result, expected)
        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            expected,
        )
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertIsNotNone(intake)
        self.assertEqual(intake.state, "proposing")
        self.assertEqual(intake.state_updated_at, RESERVED_AT)
        self.assertIsNone(intake.failure_reason)

    def test_begin_proposal_wrong_state_rolls_back_reservation(self) -> None:
        self.store.register_intake(intake_record())

        with self.assertRaises(StateTransitionRefused):
            self.store.begin_proposal(reservation_record())

        self.assertIsNone(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID),
            intake_record(),
        )

    def test_reclaim_requires_expiry_and_replaces_only_the_token(self) -> None:
        original = self._reserved()
        replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )
        active_replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.reclaim_proposal(
                original,
                active_replacement,
                RESERVED_AT,
            )

        result = self.store.reclaim_proposal(original, replacement, RECLAIMED_AT)

        self.assertEqual(result, replacement)
        self.assertEqual(result.proposal_id, original.proposal_id)
        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            replacement,
        )

    def test_reclaim_refuses_corrupt_proposing_failure_reason(self) -> None:
        original = self._reserved()
        self._force_intake_state(
            state="proposing",
            state_updated_at=RESERVED_AT,
            failure_reason="proposal.model_request_failed",
        )
        replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.reclaim_proposal(original, replacement, RECLAIMED_AT)

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).failure_reason,
            "proposal.model_request_failed",
        )

    def test_begin_refuses_nonstandard_lease_duration(self) -> None:
        self._classified()
        invalid = reservation_record(
            lease_expires_at="2026-08-02T10:16:02Z"
        )

        with self.assertRaises(StateStoreError):
            self.store.begin_proposal(invalid)

        self.assertIsNone(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "classified",
        )

    def test_reclaim_refuses_state_reservation_timestamp_disagreement(self) -> None:
        original = self._reserved()
        self._force_intake_state(
            state="proposing",
            state_updated_at="2026-08-02T10:00:03Z",
            failure_reason=None,
        )
        replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.reclaim_proposal(original, replacement, RECLAIMED_AT)

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_reclaim_refuses_failed_reservation_timestamp_disagreement(self) -> None:
        original = self._reserved()
        self.store.record_proposal_failure(
            CAPTURE_ID,
            LEASE_TOKEN,
            "proposal.model_request_failed",
            RESERVED_AT,
        )
        failed_reservation = reservation_record(lease_expires_at=RESERVED_AT)
        self._force_intake_state(
            state="failed",
            state_updated_at=RECLAIMED_AT,
            failure_reason="proposal.model_request_failed",
        )
        replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.reclaim_proposal(
                failed_reservation,
                replacement,
                RECLAIMED_AT,
            )

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            failed_reservation,
        )

    def test_stale_reclaim_token_is_refused(self) -> None:
        original = self._reserved()
        stale = reservation_record(lease_token=REPLACEMENT_TOKEN)
        replacement = reservation_record(
            lease_token="2b6ff669-ea8c-4af9-a506-403b9e9ffaba",
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.reclaim_proposal(stale, replacement, RECLAIMED_AT)

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_known_failure_expires_lease_and_allows_reclaim(self) -> None:
        original = self._reserved()

        failed = self.store.record_proposal_failure(
            CAPTURE_ID,
            LEASE_TOKEN,
            "proposal.model_request_failed",
            RESERVED_AT,
        )

        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.failure_reason, "proposal.model_request_failed")
        expired = self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        self.assertIsNotNone(expired)
        self.assertEqual(expired.lease_expires_at, RESERVED_AT)
        replacement = reservation_record(
            lease_token=REPLACEMENT_TOKEN,
            reserved_at=RECLAIMED_AT,
            lease_expires_at=RECLAIM_EXPIRES_AT,
        )
        self.assertEqual(
            self.store.reclaim_proposal(expired, replacement, RECLAIMED_AT),
            replacement,
        )

    def test_stale_token_cannot_record_failure(self) -> None:
        original = self._reserved()

        with self.assertRaises(StateTransitionRefused):
            self.store.record_proposal_failure(
                CAPTURE_ID,
                REPLACEMENT_TOKEN,
                "proposal.model_request_failed",
                RECLAIMED_AT,
            )

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )
        self.assertEqual(
            self.store.find_intake_by_capture_id(CAPTURE_ID).state,
            "proposing",
        )

    def test_failure_recording_refuses_state_reservation_timestamp_disagreement(
        self,
    ) -> None:
        original = self._reserved()
        self._force_intake_state(
            state="proposing",
            state_updated_at="2026-08-02T10:00:03Z",
            failure_reason=None,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.record_proposal_failure(
                CAPTURE_ID,
                LEASE_TOKEN,
                "proposal.model_request_failed",
                RECLAIMED_AT,
            )

        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_complete_proposal_inserts_deletes_reservation_and_transitions(self) -> None:
        self._reserved()
        expected = proposal_record()

        result = self.store.complete_proposal(expected, LEASE_TOKEN)

        self.assertEqual(result, expected)
        self.assertEqual(self.store.find_proposal_by_capture_id(CAPTURE_ID), expected)
        self.assertIsNone(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "proposed")
        self.assertEqual(intake.state_updated_at, COMPLETED_AT)

    def test_stale_token_cannot_complete_or_insert_proposal(self) -> None:
        original = self._reserved()

        with self.assertRaises(StateTransitionRefused):
            self.store.complete_proposal(proposal_record(), REPLACEMENT_TOKEN)

        self.assertIsNone(self.store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_completion_refuses_state_reservation_timestamp_disagreement(self) -> None:
        original = self._reserved()
        self._force_intake_state(
            state="proposing",
            state_updated_at="2026-08-02T10:00:03Z",
            failure_reason=None,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.complete_proposal(proposal_record(), LEASE_TOKEN)

        self.assertIsNone(self.store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertEqual(
            self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_draft_failure_resume_and_registration_are_atomic(self) -> None:
        expected = self._completed()

        failed = self.store.record_draft_failure(
            CAPTURE_ID,
            PROPOSAL_ID,
            "proposal.draft_write_failed",
            DRAFT_AT,
        )

        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.failure_reason, "proposal.draft_write_failed")
        resumed = self.store.resume_proposal_draft(
            CAPTURE_ID,
            PROPOSAL_ID,
            "2026-08-02T10:15:06Z",
        )
        self.assertEqual(resumed.state, "proposed")
        draft_path = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
        registered = self.store.register_proposal_draft(
            CAPTURE_ID,
            PROPOSAL_ID,
            draft_path,
            "2026-08-02T10:15:07Z",
        )

        self.assertEqual(registered.draft_note_path, draft_path)
        self.assertEqual(registered.proposal_id, expected.proposal_id)
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "awaiting_approval")
        self.assertEqual(intake.state_updated_at, "2026-08-02T10:15:07Z")

    def test_draft_registration_wrong_state_does_not_set_path(self) -> None:
        self._completed()
        self.store.record_draft_failure(
            CAPTURE_ID,
            PROPOSAL_ID,
            "proposal.draft_write_failed",
            DRAFT_AT,
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.register_proposal_draft(
                CAPTURE_ID,
                PROPOSAL_ID,
                f"vault/notes/proposed/note.{CAPTURE_ID}.md",
                "2026-08-02T10:15:07Z",
            )

        proposal = self.store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertIsNone(proposal.draft_note_path)

    def test_draft_failure_refuses_reservation_stage_reason(self) -> None:
        expected = self._completed()

        with self.assertRaises(StateStoreError):
            self.store.record_draft_failure(
                CAPTURE_ID,
                PROPOSAL_ID,
                "proposal.model_request_failed",
                DRAFT_AT,
            )

        self.assertEqual(self.store.find_proposal_by_capture_id(CAPTURE_ID), expected)
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "proposed")
        self.assertIsNone(intake.failure_reason)

    def test_draft_resume_refuses_reservation_stage_reason(self) -> None:
        expected = self._completed()
        self._force_intake_state(
            state="failed",
            state_updated_at=DRAFT_AT,
            failure_reason="proposal.model_request_failed",
        )

        with self.assertRaises(StateTransitionRefused):
            self.store.resume_proposal_draft(
                CAPTURE_ID,
                PROPOSAL_ID,
                "2026-08-02T10:15:06Z",
            )

        self.assertEqual(self.store.find_proposal_by_capture_id(CAPTURE_ID), expected)
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(intake.failure_reason, "proposal.model_request_failed")

    def test_every_illegal_step_four_source_state_is_rejected_and_rolled_back(
        self,
    ) -> None:
        states = (
            "captured",
            "classifying",
            "classified",
            "proposing",
            "proposed",
            "awaiting_approval",
            "approved",
            "filed",
            "rejected",
            "failed",
        )

        def begin(store: SQLiteStateStore) -> None:
            store.begin_proposal(reservation_record())

        def reclaim(store: SQLiteStateStore) -> None:
            store.reclaim_proposal(
                reservation_record(),
                reservation_record(
                    lease_token=REPLACEMENT_TOKEN,
                    reserved_at=RECLAIMED_AT,
                    lease_expires_at=RECLAIM_EXPIRES_AT,
                ),
                RECLAIMED_AT,
            )

        def fail(store: SQLiteStateStore) -> None:
            store.record_proposal_failure(
                CAPTURE_ID,
                LEASE_TOKEN,
                "proposal.model_request_failed",
                RECLAIMED_AT,
            )

        def complete(store: SQLiteStateStore) -> None:
            store.complete_proposal(proposal_record(), LEASE_TOKEN)

        def draft_fail(store: SQLiteStateStore) -> None:
            store.record_draft_failure(
                CAPTURE_ID,
                PROPOSAL_ID,
                "proposal.draft_write_failed",
                DRAFT_AT,
            )

        def resume(store: SQLiteStateStore) -> None:
            store.resume_proposal_draft(
                CAPTURE_ID,
                PROPOSAL_ID,
                DRAFT_AT,
            )

        def register(store: SQLiteStateStore) -> None:
            store.register_proposal_draft(
                CAPTURE_ID,
                PROPOSAL_ID,
                f"vault/notes/proposed/note.{CAPTURE_ID}.md",
                DRAFT_AT,
            )

        operations: tuple[
            tuple[
                str,
                frozenset[str],
                Callable[[str], SQLiteStateStore],
                Callable[[SQLiteStateStore], None],
                str,
                str,
            ],
            ...,
        ] = (
            (
                "begin",
                frozenset({"classified"}),
                self._classified_store,
                begin,
                CLASSIFIED_AT,
                "proposal.model_request_failed",
            ),
            (
                "reclaim",
                frozenset({"proposing", "failed"}),
                self._reserved_store,
                reclaim,
                RESERVED_AT,
                "proposal.model_request_failed",
            ),
            (
                "failure",
                frozenset({"proposing"}),
                self._reserved_store,
                fail,
                RESERVED_AT,
                "proposal.model_request_failed",
            ),
            (
                "completion",
                frozenset({"proposing"}),
                self._reserved_store,
                complete,
                RESERVED_AT,
                "proposal.model_request_failed",
            ),
            (
                "draft-failure",
                frozenset({"proposed"}),
                self._completed_store,
                draft_fail,
                COMPLETED_AT,
                "proposal.draft_write_failed",
            ),
            (
                "resume",
                frozenset({"failed"}),
                self._completed_store,
                resume,
                COMPLETED_AT,
                "proposal.draft_write_failed",
            ),
            (
                "registration",
                frozenset({"proposed"}),
                self._completed_store,
                register,
                COMPLETED_AT,
                "proposal.draft_write_failed",
            ),
        )

        case_number = 0
        for (
            operation,
            legal_states,
            setup,
            invoke,
            state_updated_at,
            failed_reason,
        ) in operations:
            for state in states:
                if state in legal_states:
                    continue
                case_number += 1
                with self.subTest(operation=operation, source_state=state):
                    store = setup(f"edge-{case_number}")
                    self._force_store_intake_state(
                        store,
                        state,
                        state_updated_at,
                        failed_reason if state == "failed" else None,
                    )
                    before = self._snapshot(store)

                    with self.assertRaises(StateTransitionRefused):
                        invoke(store)

                    self.assertEqual(self._snapshot(store), before)


if __name__ == "__main__":
    unittest.main()

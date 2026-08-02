from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from metis.data_access import (
    ClassificationRecord,
    IntakeRecord,
    SQLiteStateStore,
    StateStoreError,
    StateTransitionRefused,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURED_AT = "2026-08-01T19:00:00Z"
STARTED_AT = "2026-08-01T20:00:00Z"
COMPLETED_AT = "2026-08-01T20:00:01Z"
FAILED_AT = "2026-08-01T20:00:02Z"


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
        "routing": "proposal",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "classify-v1",
        "raw_response_path": (
            f"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt"
        ),
        "created_at": COMPLETED_AT,
    }
    values.update(changes)
    return ClassificationRecord(**values)


class ClassificationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        self.store = SQLiteStateStore(self.database_path)
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _register(self, **changes: object) -> IntakeRecord:
        record = intake_record(**changes)
        self.store.register_intake(record)
        return record

    def test_capture_id_lookup_round_trips_and_returns_none_when_absent(self) -> None:
        self.assertIsNone(self.store.find_intake_by_capture_id(CAPTURE_ID))
        expected = self._register()

        self.assertEqual(self.store.find_intake_by_capture_id(CAPTURE_ID), expected)

    def test_classification_lookup_returns_none_when_absent(self) -> None:
        self._register()

        self.assertIsNone(
            self.store.find_classification_by_capture_id(CAPTURE_ID)
        )

    def test_begin_classification_updates_captured_intake(self) -> None:
        self._register(failure_reason="capture.previous")

        result = self.store.begin_classification(CAPTURE_ID, STARTED_AT)

        self.assertEqual(result.state, "classifying")
        self.assertEqual(result.state_updated_at, STARTED_AT)
        self.assertIsNone(result.failure_reason)
        self.assertEqual(self.store.find_intake_by_capture_id(CAPTURE_ID), result)

    def test_begin_classification_retries_only_classification_failure(self) -> None:
        self._register(
            state="failed",
            failure_reason="classification.request_failed",
        )

        result = self.store.begin_classification(CAPTURE_ID, STARTED_AT)

        self.assertEqual(result.state, "classifying")
        self.assertIsNone(result.failure_reason)

    def test_begin_classification_refuses_every_other_known_state(self) -> None:
        cases = (
            ("classifying", None),
            ("classified", None),
            ("proposed", None),
            ("awaiting_approval", None),
            ("approved", None),
            ("filed", None),
            ("rejected", None),
            ("failed", "capture.write_failed"),
            ("failed", None),
        )
        for index, (state, reason) in enumerate(cases):
            with self.subTest(state=state, reason=reason):
                capture_id = f"8f14e45f-ea3c-4f7a-9f2d-{index:012d}"
                original = self._register(
                    capture_id=capture_id,
                    content_hash=f"sha256:{index:064x}",
                    evidence_path=f"evidence/{capture_id}",
                    state=state,
                    failure_reason=reason,
                    trace_id=capture_id,
                )

                with self.assertRaises(StateTransitionRefused) as raised:
                    self.store.begin_classification(capture_id, STARTED_AT)

                self.assertEqual(raised.exception.record, original)
                self.assertEqual(
                    self.store.find_intake_by_capture_id(capture_id), original
                )

    def test_begin_classification_missing_intake_is_store_failure(self) -> None:
        with self.assertRaises(StateStoreError):
            self.store.begin_classification(CAPTURE_ID, STARTED_AT)

    def test_complete_classification_inserts_and_transitions_atomically(self) -> None:
        self._register()
        self.store.begin_classification(CAPTURE_ID, STARTED_AT)
        record = classification_record()

        result = self.store.complete_classification(record)

        self.assertEqual(result, record)
        self.assertEqual(
            self.store.find_classification_by_capture_id(CAPTURE_ID), record
        )
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertIsNotNone(intake)
        self.assertEqual(intake.state, "classified")
        self.assertEqual(intake.state_updated_at, COMPLETED_AT)
        self.assertIsNone(intake.failure_reason)

    def test_complete_classification_wrong_state_rolls_back_insert(self) -> None:
        original = self._register()

        with self.assertRaises(StateTransitionRefused) as raised:
            self.store.complete_classification(classification_record())

        self.assertEqual(raised.exception.record, original)
        self.assertIsNone(
            self.store.find_classification_by_capture_id(CAPTURE_ID)
        )
        self.assertEqual(self.store.find_intake_by_capture_id(CAPTURE_ID), original)

    def test_record_classification_failure_updates_classifying_intake(self) -> None:
        self._register()
        self.store.begin_classification(CAPTURE_ID, STARTED_AT)

        result = self.store.record_classification_failure(
            CAPTURE_ID,
            "classification.response_invalid",
            FAILED_AT,
        )

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.state_updated_at, FAILED_AT)
        self.assertEqual(
            result.failure_reason,
            "classification.response_invalid",
        )
        self.assertEqual(self.store.find_intake_by_capture_id(CAPTURE_ID), result)

    def test_record_classification_failure_refuses_wrong_state(self) -> None:
        original = self._register()

        with self.assertRaises(StateTransitionRefused) as raised:
            self.store.record_classification_failure(
                CAPTURE_ID,
                "classification.request_failed",
                FAILED_AT,
            )

        self.assertEqual(raised.exception.record, original)
        self.assertEqual(self.store.find_intake_by_capture_id(CAPTURE_ID), original)

    def test_sqlite_lookup_errors_are_wrapped(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TABLE classification")

        with self.assertRaises(StateStoreError):
            self.store.find_classification_by_capture_id(CAPTURE_ID)

    def test_sqlite_connection_errors_are_wrapped(self) -> None:
        self.store.close()
        self.store = SQLiteStateStore(Path(self.temporary_directory.name))
        self.addCleanup(self.store.close)

        with self.assertRaises(StateStoreError):
            self.store.begin_classification(CAPTURE_ID, STARTED_AT)

    def test_sqlite_completion_errors_roll_back(self) -> None:
        self._register()
        self.store.begin_classification(CAPTURE_ID, STARTED_AT)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TABLE classification")

        with self.assertRaises(StateStoreError):
            self.store.complete_classification(classification_record())

        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertIsNotNone(intake)
        self.assertEqual(intake.state, "classifying")


if __name__ == "__main__":
    unittest.main()

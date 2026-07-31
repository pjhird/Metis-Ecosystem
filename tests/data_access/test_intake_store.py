from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from metis.data_access import (
    IntakeRecord,
    IntakeRegistrationStatus,
    SQLiteStateStore,
    StateStoreError,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"


def intake_record(**changes: object) -> IntakeRecord:
    values = {
        "capture_id": CAPTURE_ID,
        "content_hash": "sha256:" + "a" * 64,
        "captured_at": "2026-07-31T20:00:00Z",
        "source_type": "cli-typed",
        "evidence_path": f"evidence/{CAPTURE_ID}",
        "state": "captured",
        "state_updated_at": "2026-07-31T20:00:00Z",
        "failure_reason": None,
        "trace_id": CAPTURE_ID,
    }
    values.update(changes)
    return IntakeRecord(**values)


class IntakeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        self.store = SQLiteStateStore(self.database_path)
        self.addCleanup(self.store.close)
        self.store.initialize()

    def test_find_intake_by_content_hash_returns_none_when_absent(self) -> None:
        self.assertIsNone(
            self.store.find_intake_by_content_hash("sha256:" + "0" * 64)
        )

    def test_register_and_find_intake_round_trip(self) -> None:
        record = intake_record()

        result = self.store.register_intake(record)

        self.assertEqual(result.status, IntakeRegistrationStatus.REGISTERED)
        self.assertEqual(result.record, record)
        self.assertEqual(
            self.store.find_intake_by_content_hash(record.content_hash), record
        )

    def test_duplicate_content_hash_returns_existing_record(self) -> None:
        original = intake_record()
        duplicate = intake_record(
            capture_id="6ba7b810-9dad-41d1-80b4-00c04fd430c8",
            evidence_path="evidence/6ba7b810-9dad-41d1-80b4-00c04fd430c8",
            trace_id="6ba7b810-9dad-41d1-80b4-00c04fd430c8",
        )
        self.store.register_intake(original)

        result = self.store.register_intake(duplicate)

        self.assertEqual(result.status, IntakeRegistrationStatus.DUPLICATE)
        self.assertEqual(result.record, original)

    def test_capture_id_collision_is_a_state_store_failure(self) -> None:
        self.store.register_intake(intake_record())

        with self.assertRaises(StateStoreError):
            self.store.register_intake(
                intake_record(content_hash="sha256:" + "b" * 64)
            )

    def test_missing_intake_table_is_a_state_store_failure(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP TABLE intake")

        with self.assertRaises(StateStoreError):
            self.store.find_intake_by_content_hash("sha256:" + "a" * 64)

    def test_unopenable_database_is_a_state_store_failure(self) -> None:
        self.store.close()
        self.store = SQLiteStateStore(Path(self.temporary_directory.name))
        self.addCleanup(self.store.close)

        with self.assertRaises(StateStoreError):
            self.store.register_intake(intake_record())


if __name__ == "__main__":
    unittest.main()

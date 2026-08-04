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
OTHER_CAPTURE_ID = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
CONTENT_HASH = "sha256:" + "a" * 64


def intake_record(**changes: object) -> IntakeRecord:
    capture_id = str(changes.get("capture_id", CAPTURE_ID))
    values = {
        "capture_id": capture_id,
        "content_hash": CONTENT_HASH,
        "captured_at": "2026-07-31T20:00:00Z",
        "source_type": "cli-typed",
        "evidence_path": f"evidence/{capture_id}",
        "state": "captured",
        "state_updated_at": "2026-07-31T20:00:00Z",
        "failure_reason": None,
        "trace_id": capture_id,
        "type_pin": "",
        "parent_id": "",
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

    def test_find_intake_by_pin_key_returns_none_when_absent(self) -> None:
        self.assertIsNone(
            self.store.find_intake_by_pin_key("sha256:" + "0" * 64, "", "")
        )

    def test_register_and_find_intake_round_trip(self) -> None:
        record = intake_record()

        result = self.store.register_intake(record)

        self.assertEqual(result.status, IntakeRegistrationStatus.REGISTERED)
        self.assertEqual(result.record, record)
        self.assertEqual(
            self.store.find_intake_by_pin_key(record.content_hash, "", ""), record
        )

    def test_duplicate_content_hash_returns_existing_record(self) -> None:
        original = intake_record()
        duplicate = intake_record(capture_id=OTHER_CAPTURE_ID)
        self.store.register_intake(original)

        result = self.store.register_intake(duplicate)

        self.assertEqual(result.status, IntakeRegistrationStatus.DUPLICATE)
        self.assertEqual(result.record, original)

    def test_identical_text_under_two_parents_registers_two_rows(self) -> None:
        """ADR-022 clause 9: the same text under a different parent is a
        different intent, so it is a second capture and not a replay."""
        self.store.register_intake(
            intake_record(type_pin="task", parent_id="proj.one")
        )

        second = self.store.register_intake(
            intake_record(
                capture_id=OTHER_CAPTURE_ID, type_pin="task", parent_id="proj.two"
            )
        )

        self.assertEqual(second.status, IntakeRegistrationStatus.REGISTERED)
        self.assertEqual(
            len(self.store.find_intakes_by_content_hash(CONTENT_HASH)), 2
        )

    def test_identical_text_and_pin_registers_once(self) -> None:
        original = intake_record(type_pin="task", parent_id="proj.one")
        self.store.register_intake(original)

        second = self.store.register_intake(
            intake_record(
                capture_id=OTHER_CAPTURE_ID, type_pin="task", parent_id="proj.one"
            )
        )

        self.assertEqual(second.status, IntakeRegistrationStatus.DUPLICATE)
        self.assertEqual(second.record, original)

    def test_unpinned_replay_still_registers_once(self) -> None:
        """Regression (ADR-022 clause 9): NULLs would compare distinct and
        silently allow both, disabling replay protection for plain capture."""
        self.store.register_intake(intake_record())

        second = self.store.register_intake(intake_record(capture_id=OTHER_CAPTURE_ID))

        self.assertEqual(second.status, IntakeRegistrationStatus.DUPLICATE)

    def test_pin_columns_reject_null(self) -> None:
        # Matched on the constraint itself: any IntegrityError reaches the same
        # StateStoreError through the duplicate-resolution path, so a bare
        # assertRaises would pass even with the NOT NULL clauses removed.
        with self.assertRaisesRegex(StateStoreError, "NOT NULL constraint failed"):
            self.store.register_intake(intake_record(type_pin=None, parent_id=None))

    def test_find_intakes_by_content_hash_is_empty_when_absent(self) -> None:
        self.assertEqual(
            self.store.find_intakes_by_content_hash("sha256:" + "0" * 64), ()
        )

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
            self.store.find_intake_by_pin_key(CONTENT_HASH, "", "")

    def test_unopenable_database_is_a_state_store_failure(self) -> None:
        self.store.close()
        self.store = SQLiteStateStore(Path(self.temporary_directory.name))
        self.addCleanup(self.store.close)

        with self.assertRaises(StateStoreError):
            self.store.register_intake(intake_record())


if __name__ == "__main__":
    unittest.main()

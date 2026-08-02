from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from metis.data_access import (
    ClassificationRecord,
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
    MigrationError,
    SQLiteStateStore,
    StateStore,
)


EXPECTED_COLUMNS = {
    "intake": (
        "capture_id",
        "content_hash",
        "captured_at",
        "source_type",
        "evidence_path",
        "state",
        "state_updated_at",
        "failure_reason",
        "trace_id",
    ),
    "classification": (
        "classification_id",
        "capture_id",
        "candidate_type",
        "sensitivity",
        "confidence",
        "routing",
        "model_id",
        "prompt_version",
        "raw_response_path",
        "created_at",
    ),
    "proposal": (
        "proposal_id",
        "capture_id",
        "classification_id",
        "note_type",
        "title",
        "body_path",
        "proposed_links",
        "evidence_refs",
        "confidence",
        "risk_level",
        "reason",
        "draft_note_path",
        "state",
        "created_at",
    ),
    "approval": (
        "approval_id",
        "proposal_id",
        "decision",
        "approver",
        "observed_status",
        "detected_at",
        "committed_at",
        "revoked_at",
    ),
    "audit_event": (
        "event_id",
        "trace_id",
        "capture_id",
        "actor",
        "action",
        "outcome",
        "detail",
        "created_at",
    ),
}
NULLABLE_COLUMNS = {
    "intake": {"failure_reason"},
    "classification": set(),
    "proposal": {"draft_note_path"},
    "approval": {"committed_at", "revoked_at"},
    "audit_event": {"capture_id"},
}
REAL_COLUMNS = {"confidence"}
PRIMARY_KEYS = {
    "intake": "capture_id",
    "classification": "classification_id",
    "proposal": "proposal_id",
    "approval": "approval_id",
    "audit_event": "event_id",
}


class FakeStateStore:
    def __init__(self) -> None:
        self.schema_version = 0

    def initialize(self) -> None:
        self.schema_version = 2

    def close(self) -> None:
        return None

    def find_intake_by_content_hash(self, content_hash: str) -> IntakeRecord | None:
        return None

    def find_intake_by_capture_id(self, capture_id: str) -> IntakeRecord | None:
        return None

    def find_classification_by_capture_id(
        self, capture_id: str
    ) -> ClassificationRecord | None:
        return None

    def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
        return IntakeRegistrationResult(IntakeRegistrationStatus.REGISTERED, record)

    def begin_classification(self, capture_id: str, started_at: str) -> IntakeRecord:
        raise NotImplementedError

    def complete_classification(
        self, record: ClassificationRecord
    ) -> ClassificationRecord:
        raise NotImplementedError

    def record_classification_failure(
        self, capture_id: str, reason: str, failed_at: str
    ) -> IntakeRecord:
        raise NotImplementedError


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "state" / "metis.db"

    def _initialize(self) -> SQLiteStateStore:
        store = SQLiteStateStore(self.database_path)
        self.addCleanup(store.close)
        store.initialize()
        return store

    def _column_rows(self, table: str) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(f"PRAGMA table_info({table})").fetchall()

    def _columns(self, table: str) -> tuple[str, ...]:
        return tuple(row[1] for row in self._column_rows(table))

    def _assert_column_contract(self, table: str) -> None:
        rows = self._column_rows(table)
        self.assertEqual(tuple(row[1] for row in rows), EXPECTED_COLUMNS[table])
        for _, name, declared_type, not_null, _, primary_key_position in rows:
            with self.subTest(table=table, column=name):
                expected_type = "REAL" if name in REAL_COLUMNS else "TEXT"
                self.assertEqual(declared_type, expected_type)
                self.assertEqual(bool(not_null), name not in NULLABLE_COLUMNS[table])
                self.assertEqual(
                    primary_key_position,
                    1 if name == PRIMARY_KEYS[table] else 0,
                )

    def _foreign_keys(self, table: str) -> set[tuple[str, str, str]]:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        return {(row[3], row[2], row[4]) for row in rows}

    def _insert_valid_intake(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO intake (
                capture_id, content_hash, captured_at, source_type,
                evidence_path, state, state_updated_at, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "01J8X2K4P7M3QRSTVWXYZ0ABCD",
                "sha256:first",
                "2026-07-31T12:00:00Z",
                "cli-typed",
                "evidence/01J8X2K4P7M3QRSTVWXYZ0ABCD",
                "captured",
                "2026-07-31T12:00:00Z",
                "trace-1",
            ),
        )

    def _insert_valid_classification(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO classification (
                classification_id, capture_id, candidate_type, sensitivity,
                confidence, routing, model_id, prompt_version,
                raw_response_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "classification-1",
                "01J8X2K4P7M3QRSTVWXYZ0ABCD",
                "idea",
                "normal",
                0.8,
                "proposal",
                "model-1",
                "prompt-1",
                "evidence/model-response.json",
                "2026-07-31T12:00:01Z",
            ),
        )

    def _insert_valid_proposal(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO proposal (
                proposal_id, capture_id, classification_id, note_type, title,
                body_path, proposed_links, evidence_refs, confidence, risk_level,
                reason, draft_note_path, state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proposal-1",
                "01J8X2K4P7M3QRSTVWXYZ0ABCD",
                "classification-1",
                "idea",
                "Title",
                "proposals/proposal-1.md",
                "[]",
                "[]",
                0.8,
                "low",
                "Captured as an idea",
                None,
                "pending",
                "2026-07-31T12:00:02Z",
            ),
        )

    def test_state_store_contract_is_engine_agnostic(self) -> None:
        self.assertIsInstance(FakeStateStore(), StateStore)

    def test_initial_migration_creates_only_the_five_operational_tables(self) -> None:
        store = self._initialize()

        self.assertEqual(store.schema_version, 2)
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
            ).fetchall()

        self.assertEqual(tuple(row[0] for row in rows), tuple(sorted(EXPECTED_COLUMNS)))

    def test_intake_schema_matches_contract(self) -> None:
        self._initialize()
        self._assert_column_contract("intake")
        self.assertEqual(self._foreign_keys("intake"), set())

    def test_classification_schema_matches_contract(self) -> None:
        self._initialize()
        self._assert_column_contract("classification")
        self.assertEqual(
            self._foreign_keys("classification"),
            {("capture_id", "intake", "capture_id")},
        )

    def test_proposal_schema_matches_contract(self) -> None:
        self._initialize()
        self._assert_column_contract("proposal")
        self.assertEqual(
            self._foreign_keys("proposal"),
            {
                ("capture_id", "intake", "capture_id"),
                ("classification_id", "classification", "classification_id"),
            },
        )

    def test_approval_schema_matches_contract(self) -> None:
        self._initialize()
        self._assert_column_contract("approval")
        self.assertEqual(
            self._foreign_keys("approval"),
            {("proposal_id", "proposal", "proposal_id")},
        )

    def test_audit_event_schema_matches_contract(self) -> None:
        self._initialize()
        self._assert_column_contract("audit_event")
        self.assertEqual(self._foreign_keys("audit_event"), set())

    def test_content_hash_uniqueness_is_enforced_by_sqlite(self) -> None:
        self._initialize()
        with sqlite3.connect(self.database_path) as connection:
            self._insert_valid_intake(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO intake (
                        capture_id, content_hash, captured_at, source_type,
                        evidence_path, state, state_updated_at, trace_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "01J8X2K4P7M3QRSTVWXYZ0ABCE",
                        "sha256:first",
                        "2026-07-31T12:01:00Z",
                        "cli-typed",
                        "evidence/01J8X2K4P7M3QRSTVWXYZ0ABCE",
                        "captured",
                        "2026-07-31T12:01:00Z",
                        "trace-2",
                    ),
                )

    def test_foreign_keys_are_enabled_on_managed_connections(self) -> None:
        store = self._initialize()

        self.assertTrue(store.foreign_keys_enabled)

    def test_intake_enums_are_enforced(self) -> None:
        self._initialize()
        statement = """
            INSERT INTO intake (
                capture_id, content_hash, captured_at, source_type,
                evidence_path, state, state_updated_at, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        valid = [
            "capture-invalid",
            "sha256:invalid",
            "2026-07-31T12:00:00Z",
            "cli-typed",
            "evidence/capture-invalid",
            "captured",
            "2026-07-31T12:00:00Z",
            "trace-invalid",
        ]

        with sqlite3.connect(self.database_path) as connection:
            for index, invalid_value in ((3, "email"), (5, "complete")):
                parameters = valid.copy()
                parameters[index] = invalid_value
                with self.subTest(index=index, invalid_value=invalid_value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)

    def test_classification_enums_and_confidence_are_enforced(self) -> None:
        self._initialize()
        statement = """
            INSERT INTO classification (
                classification_id, capture_id, candidate_type, sensitivity,
                confidence, routing, model_id, prompt_version,
                raw_response_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        valid = [
            "classification-invalid",
            "01J8X2K4P7M3QRSTVWXYZ0ABCD",
            "idea",
            "normal",
            0.8,
            "proposal",
            "model-1",
            "prompt-1",
            "evidence/model-response.json",
            "2026-07-31T12:00:01Z",
        ]

        with sqlite3.connect(self.database_path) as connection:
            self._insert_valid_intake(connection)
            for index, invalid_value in (
                (2, "unknown"),
                (3, "public"),
                (4, 1.1),
            ):
                parameters = valid.copy()
                parameters[index] = invalid_value
                with self.subTest(index=index, invalid_value=invalid_value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)

    def test_classification_capture_is_unique(self) -> None:
        self._initialize()
        with sqlite3.connect(self.database_path) as connection:
            self._insert_valid_intake(connection)
            self._insert_valid_classification(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO classification (
                        classification_id, capture_id, candidate_type, sensitivity,
                        confidence, routing, model_id, prompt_version,
                        raw_response_path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "classification-2",
                        "01J8X2K4P7M3QRSTVWXYZ0ABCD",
                        "reference",
                        "normal",
                        0.7,
                        "proposal",
                        "model-1",
                        "prompt-1",
                        "evidence/model-response-2.json",
                        "2026-07-31T12:00:02Z",
                    ),
                )

    def test_proposal_enums_and_confidence_are_enforced(self) -> None:
        self._initialize()
        statement = """
            INSERT INTO proposal (
                proposal_id, capture_id, classification_id, note_type, title,
                body_path, proposed_links, evidence_refs, confidence, risk_level,
                reason, draft_note_path, state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        valid = [
            "proposal-invalid",
            "01J8X2K4P7M3QRSTVWXYZ0ABCD",
            "classification-1",
            "idea",
            "Title",
            "proposals/proposal-invalid.md",
            "[]",
            "[]",
            0.8,
            "low",
            "Reason",
            None,
            "pending",
            "2026-07-31T12:00:02Z",
        ]

        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._insert_valid_intake(connection)
            self._insert_valid_classification(connection)
            for index, invalid_value in (
                (3, "unknown"),
                (8, -0.1),
                (9, "critical"),
                (12, "filed"),
            ):
                parameters = valid.copy()
                parameters[index] = invalid_value
                with self.subTest(index=index, invalid_value=invalid_value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)

    def test_approval_decision_and_human_approver_are_enforced(self) -> None:
        self._initialize()
        statement = """
            INSERT INTO approval (
                approval_id, proposal_id, decision, approver,
                observed_status, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        valid = [
            "approval-invalid",
            "proposal-1",
            "approved",
            "human:philly",
            "approved",
            "2026-07-31T12:00:03Z",
        ]

        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._insert_valid_intake(connection)
            self._insert_valid_classification(connection)
            self._insert_valid_proposal(connection)
            for index, invalid_value in ((2, "pending"), (3, "agent:reviewer")):
                parameters = valid.copy()
                parameters[index] = invalid_value
                with self.subTest(index=index, invalid_value=invalid_value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)

    def test_audit_outcome_is_enforced(self) -> None:
        self._initialize()
        statement = """
            INSERT INTO audit_event (
                event_id, trace_id, actor, action, outcome, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        parameters = (
            "event-invalid",
            "trace-1",
            "orchestrator",
            "capture.written",
            "unknown",
            "{}",
            "2026-07-31T12:00:02Z",
        )

        with sqlite3.connect(self.database_path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)

    def test_reapplying_migrations_is_idempotent(self) -> None:
        store = self._initialize()
        store.initialize()
        self.assertEqual(store.schema_version, 2)
        self.assertEqual(self._columns("intake"), EXPECTED_COLUMNS["intake"])

    def test_failed_migration_rolls_back_only_its_partial_changes(self) -> None:
        migration_directory = Path(self.temporary_directory.name) / "migrations"
        migration_directory.mkdir()
        (migration_directory / "001_anchor.sql").write_text(
            "CREATE TABLE anchor (id TEXT PRIMARY KEY);\n"
        )
        (migration_directory / "002_broken.sql").write_text(
            "CREATE TABLE partial (id TEXT PRIMARY KEY);\nTHIS IS NOT SQL;\n"
        )
        store = SQLiteStateStore(
            self.database_path,
            migrations_directory=migration_directory,
        )
        self.addCleanup(store.close)

        with self.assertRaises(MigrationError):
            store.initialize()

        self.assertEqual(store.schema_version, 1)
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
            ).fetchall()
        self.assertEqual(rows, [("anchor",)])

    def test_empty_migration_directory_fails_closed(self) -> None:
        migration_directory = Path(self.temporary_directory.name) / "empty-migrations"
        migration_directory.mkdir()
        store = SQLiteStateStore(
            self.database_path,
            migrations_directory=migration_directory,
        )
        self.addCleanup(store.close)

        with self.assertRaisesRegex(MigrationError, "no migrations found"):
            store.initialize()

    def test_migration_version_gap_fails_closed(self) -> None:
        migration_directory = Path(self.temporary_directory.name) / "gapped-migrations"
        migration_directory.mkdir()
        (migration_directory / "001_anchor.sql").write_text(
            "CREATE TABLE anchor (id TEXT PRIMARY KEY);\n"
        )
        (migration_directory / "003_gap.sql").write_text(
            "CREATE TABLE gap (id TEXT PRIMARY KEY);\n"
        )
        store = SQLiteStateStore(
            self.database_path,
            migrations_directory=migration_directory,
        )
        self.addCleanup(store.close)

        with self.assertRaisesRegex(MigrationError, "expected migration 002"):
            store.initialize()

    def test_newer_database_schema_fails_closed(self) -> None:
        self.database_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA user_version = 3")
        store = SQLiteStateStore(self.database_path)
        self.addCleanup(store.close)

        with self.assertRaisesRegex(
            MigrationError,
            "database schema version 3 is newer than supported version 2",
        ):
            store.initialize()


if __name__ == "__main__":
    unittest.main()

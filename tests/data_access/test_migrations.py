from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from metis.data_access import MigrationError, SQLiteStateStore, StateStore


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


class FakeStateStore:
    def __init__(self) -> None:
        self.schema_version = 0

    def initialize(self) -> None:
        self.schema_version = 1

    def close(self) -> None:
        return None


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

    def _columns(self, table: str) -> tuple[str, ...]:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return tuple(row[1] for row in rows)

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

    def test_state_store_contract_is_engine_agnostic(self) -> None:
        self.assertIsInstance(FakeStateStore(), StateStore)

    def test_initial_migration_creates_only_the_five_operational_tables(self) -> None:
        store = self._initialize()

        self.assertEqual(store.schema_version, 1)
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
            ).fetchall()

        self.assertEqual(tuple(row[0] for row in rows), tuple(sorted(EXPECTED_COLUMNS)))

    def test_intake_schema_matches_contract(self) -> None:
        self._initialize()
        self.assertEqual(self._columns("intake"), EXPECTED_COLUMNS["intake"])

    def test_classification_schema_matches_contract(self) -> None:
        self._initialize()
        self.assertEqual(
            self._columns("classification"), EXPECTED_COLUMNS["classification"]
        )
        self.assertEqual(
            self._foreign_keys("classification"),
            {("capture_id", "intake", "capture_id")},
        )

    def test_proposal_schema_matches_contract(self) -> None:
        self._initialize()
        self.assertEqual(self._columns("proposal"), EXPECTED_COLUMNS["proposal"])
        self.assertEqual(
            self._foreign_keys("proposal"),
            {
                ("capture_id", "intake", "capture_id"),
                ("classification_id", "classification", "classification_id"),
            },
        )

    def test_approval_schema_matches_contract(self) -> None:
        self._initialize()
        self.assertEqual(self._columns("approval"), EXPECTED_COLUMNS["approval"])
        self.assertEqual(
            self._foreign_keys("approval"),
            {("proposal_id", "proposal", "proposal_id")},
        )

    def test_audit_event_schema_matches_contract(self) -> None:
        self._initialize()
        self.assertEqual(self._columns("audit_event"), EXPECTED_COLUMNS["audit_event"])

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

    def test_documented_enum_and_confidence_constraints_are_enforced(self) -> None:
        self._initialize()

        invalid_statements = (
            (
                """
                INSERT INTO classification (
                    classification_id, capture_id, candidate_type, sensitivity,
                    confidence, routing, model_id, prompt_version,
                    raw_response_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "classification-invalid",
                    "01J8X2K4P7M3QRSTVWXYZ0ABCD",
                    "idea",
                    "normal",
                    1.1,
                    "proposal",
                    "model-1",
                    "prompt-1",
                    "evidence/model-response.json",
                    "2026-07-31T12:00:01Z",
                ),
            ),
            (
                """
                INSERT INTO audit_event (
                    event_id, trace_id, actor, action, outcome, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "event-invalid",
                    "trace-1",
                    "orchestrator",
                    "capture.written",
                    "unknown",
                    "{}",
                    "2026-07-31T12:00:02Z",
                ),
            ),
        )

        with sqlite3.connect(self.database_path) as connection:
            self._insert_valid_intake(connection)
            for statement, parameters in invalid_statements:
                with self.subTest(parameters=parameters):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, parameters)

    def test_reapplying_migrations_is_idempotent(self) -> None:
        store = self._initialize()
        store.initialize()
        self.assertEqual(store.schema_version, 1)
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


if __name__ == "__main__":
    unittest.main()

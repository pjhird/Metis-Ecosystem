from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from metis.capture import CaptureService, CaptureStatus
from metis.data_access import SQLiteStateStore
from metis.evidence import EvidenceStore


CAPTURE_ID = UUID("8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70")
CAPTURED_AT = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)


class CaptureIntegrationTests(unittest.TestCase):
    def test_exact_replay_creates_one_row_and_one_evidence_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_root = Path(temporary_directory)
            database_path = runtime_root / "state.db"
            with SQLiteStateStore(database_path) as state_store:
                state_store.initialize()
                service = CaptureService(
                    state_store,
                    EvidenceStore(runtime_root),
                    id_factory=lambda: CAPTURE_ID,
                    clock=lambda: CAPTURED_AT,
                )

                first = service.capture("same input")
                second = service.capture("same input")

                self.assertEqual(first.status, CaptureStatus.CAPTURED)
                self.assertEqual(second.status, CaptureStatus.DUPLICATE)
                self.assertEqual(first.capture_id, second.capture_id)
                self.assertEqual(
                    len(list((runtime_root / "evidence").iterdir())), 1
                )
                with sqlite3.connect(database_path) as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM intake"
                    ).fetchone()[0]
                self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

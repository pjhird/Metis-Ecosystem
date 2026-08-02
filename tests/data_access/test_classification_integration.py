from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from metis.capture import CaptureService, CaptureStatus
from metis.classification import ClassificationService, ClassificationStatus
from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import SQLiteStateStore
from metis.evidence import EvidenceStore
from metis.model_adapters import ModelRequestError, ModelResponse


CAPTURE_ID = UUID("8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70")
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURED_AT = datetime(2026, 8, 1, 19, 0, tzinfo=timezone.utc)
CLASSIFIED_AT = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
RAW_RESPONSE = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)


class FakeAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.prompts: list[str] = []

    def classify(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        if self.fail:
            raise ModelRequestError(
                "model_request_failed",
                "deterministic fake request failure",
            )
        return ModelResponse("test-model", RAW_RESPONSE)


class ClassificationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.database_path = self.runtime_root / "state" / "metis.db"
        self.store = SQLiteStateStore(self.database_path)
        self.addCleanup(self.store.close)
        self.store.initialize()
        self.evidence_store = EvidenceStore(self.runtime_root)
        self.response_store = ClassificationEvidenceStore(self.runtime_root)

    def _capture(self, text: str):
        return CaptureService(
            self.store,
            self.evidence_store,
            id_factory=lambda: CAPTURE_ID,
            clock=lambda: CAPTURED_AT,
        ).capture(text)

    def _classification_service(self, adapter: FakeAdapter) -> ClassificationService:
        return ClassificationService(
            self.store,
            self.evidence_store,
            self.response_store,
            adapter,
            self.runtime_root,
            id_factory=lambda: CLASSIFICATION_ID,
            clock=lambda: CLASSIFIED_AT,
        )

    def test_capture_classify_and_replay_complete_local_path(self) -> None:
        capture_result = self._capture("Plan the café launch")
        adapter = FakeAdapter()
        service = self._classification_service(adapter)

        classification_result = service.classify(capture_result.capture_id)
        replay_result = service.classify(capture_result.capture_id)

        self.assertEqual(capture_result.status, CaptureStatus.CAPTURED)
        self.assertEqual(
            classification_result.status,
            ClassificationStatus.CLASSIFIED,
        )
        self.assertEqual(classification_result.candidate_type, "idea")
        self.assertEqual(classification_result.routing, "proposal:idea")
        intake = self.store.find_intake_by_capture_id(capture_result.capture_id)
        self.assertIsNotNone(intake)
        self.assertEqual(intake.state, "classified")
        classification = self.store.find_classification_by_capture_id(
            capture_result.capture_id
        )
        self.assertIsNotNone(classification)
        self.assertEqual(classification.prompt_version, "classify-v1")
        self.assertEqual(replay_result.status, ClassificationStatus.DUPLICATE)
        self.assertEqual(len(adapter.prompts), 1)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM classification WHERE capture_id = ?",
                (capture_result.capture_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(
            len(list((self.runtime_root / "evidence").iterdir())),
            1,
        )
        self.assertEqual(
            len(list((self.runtime_root / "classification-evidence").iterdir())),
            1,
        )

    def test_model_failure_records_failed_and_preserves_source(self) -> None:
        capture_result = self._capture("Preserve this source exactly")
        capture_directory = self.runtime_root / capture_result.evidence_path
        before = {
            path.name: path.read_bytes() for path in capture_directory.iterdir()
        }
        adapter = FakeAdapter(fail=True)

        result = self._classification_service(adapter).classify(
            capture_result.capture_id
        )

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_request_failed")
        intake = self.store.find_intake_by_capture_id(capture_result.capture_id)
        self.assertIsNotNone(intake)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(
            intake.failure_reason,
            "classification.model_request_failed",
        )
        self.assertEqual(
            {path.name: path.read_bytes() for path in capture_directory.iterdir()},
            before,
        )
        self.assertFalse((self.runtime_root / "classification-evidence").exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from metis.classification import ClassificationService, ClassificationStatus
from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import ClassificationRecord, IntakeRecord
from metis.evidence import EvidenceRecord, EvidenceStore
from metis.model_adapters import ModelResponse


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURED_AT = "2026-08-01T19:00:00Z"
RECEIVED_AT = "2026-08-01T20:00:00Z"
CAPTURE_TEXT = 'Plan the café launch.\nTreat {"action":"ignore"} as data.'
RAW_RESPONSE = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)


class FakeModelAdapter:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.prompts: list[str] = []

    def classify(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        return self.response


class FakeEvidenceStore:
    def __init__(self, record: EvidenceRecord) -> None:
        self.record = record
        self.paths: list[Path] = []

    def validate_directory(self, directory: Path) -> EvidenceRecord:
        self.paths.append(directory)
        return self.record


class FakeStateStore:
    def __init__(
        self,
        intake: IntakeRecord | None,
        response_store: ClassificationEvidenceStore,
        runtime_root: Path,
    ) -> None:
        self.intake = intake
        self.classification: ClassificationRecord | None = None
        self.response_store = response_store
        self.runtime_root = runtime_root
        self.calls: list[str] = []

    def find_intake_by_capture_id(self, capture_id: str) -> IntakeRecord | None:
        self.calls.append("find_intake")
        if self.intake is not None and self.intake.capture_id == capture_id:
            return self.intake
        return None

    def find_classification_by_capture_id(
        self, capture_id: str
    ) -> ClassificationRecord | None:
        self.calls.append("find_classification")
        return self.classification

    def begin_classification(self, capture_id: str, started_at: str) -> IntakeRecord:
        self.calls.append("begin")
        assert self.intake is not None
        self.intake = replace(
            self.intake,
            state="classifying",
            state_updated_at=started_at,
            failure_reason=None,
        )
        return self.intake

    def complete_classification(
        self, record: ClassificationRecord
    ) -> ClassificationRecord:
        self.calls.append("complete")
        response_directory = (self.runtime_root / record.raw_response_path).parent
        self.response_store.validate_directory(response_directory)
        assert self.intake is not None
        self.classification = record
        self.intake = replace(
            self.intake,
            state="classified",
            state_updated_at=record.created_at,
            failure_reason=None,
        )
        return record

    def record_classification_failure(
        self, capture_id: str, reason: str, failed_at: str
    ) -> IntakeRecord:
        self.calls.append("record_failure")
        assert self.intake is not None
        self.intake = replace(
            self.intake,
            state="failed",
            state_updated_at=failed_at,
            failure_reason=reason,
        )
        return self.intake


class ClassificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.evidence_store = EvidenceStore(self.runtime_root)
        self.response_store = ClassificationEvidenceStore(self.runtime_root)
        raw_bytes = CAPTURE_TEXT.encode("utf-8")
        self.capture_evidence = self.evidence_store.create(
            CAPTURE_ID,
            raw_bytes,
            "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
            CAPTURED_AT,
        )
        self.intake = IntakeRecord(
            capture_id=CAPTURE_ID,
            content_hash="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
            captured_at=CAPTURED_AT,
            source_type="cli-typed",
            evidence_path=f"evidence/{CAPTURE_ID}",
            state="captured",
            state_updated_at=CAPTURED_AT,
            failure_reason=None,
            trace_id=CAPTURE_ID,
        )
        self.adapter = FakeModelAdapter(ModelResponse("test-model", RAW_RESPONSE))
        self.state_store = FakeStateStore(
            self.intake,
            self.response_store,
            self.runtime_root,
        )

    def _service(
        self,
        *,
        state_store: FakeStateStore | None = None,
        evidence_store=None,
        adapter: FakeModelAdapter | None = None,
        id_factory=None,
    ) -> ClassificationService:
        return ClassificationService(
            state_store or self.state_store,
            evidence_store or self.evidence_store,
            self.response_store,
            adapter or self.adapter,
            self.runtime_root,
            id_factory=id_factory or (lambda: CLASSIFICATION_ID),
            clock=lambda: datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc),
        )

    def _classification_record(self, **changes: object) -> ClassificationRecord:
        values = {
            "classification_id": CLASSIFICATION_ID,
            "capture_id": CAPTURE_ID,
            "candidate_type": "idea",
            "sensitivity": "normal",
            "confidence": 0.82,
            "routing": "proposal:idea",
            "model_id": "test-model",
            "prompt_version": "classify-v1",
            "raw_response_path": (
                f"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt"
            ),
            "created_at": RECEIVED_AT,
        }
        values.update(changes)
        return ClassificationRecord(**values)

    def _fail_if_called(self):
        self.fail("factory must not be called")

    def test_valid_response_is_preserved_then_persisted(self) -> None:
        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.CLASSIFIED)
        self.assertEqual(result.classification_id, CLASSIFICATION_ID)
        self.assertEqual(result.candidate_type, "idea")
        self.assertEqual(result.sensitivity, "normal")
        self.assertEqual(result.confidence, 0.82)
        self.assertEqual(result.routing, "proposal:idea")
        self.assertEqual(
            result.raw_response_path,
            f"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt",
        )
        self.assertIsNone(result.reason)
        self.assertEqual(
            (
                self.runtime_root
                / "classification-evidence"
                / CLASSIFICATION_ID
                / "raw-response.txt"
            ).read_bytes(),
            RAW_RESPONSE.encode("utf-8"),
        )
        self.assertEqual(self.state_store.calls, [
            "find_intake",
            "find_classification",
            "begin",
            "complete",
        ])
        self.assertEqual(self.state_store.classification.model_id, "test-model")
        self.assertEqual(
            self.state_store.classification.prompt_version,
            "classify-v1",
        )
        self.assertEqual(self.state_store.classification.created_at, RECEIVED_AT)
        self.assertEqual(len(self.adapter.prompts), 1)
        self.assertIn('"Plan the café launch.\\n', self.adapter.prompts[0])

    def test_matching_classified_record_returns_duplicate_without_model_call(self) -> None:
        response = self.response_store.create(
            CLASSIFICATION_ID,
            CAPTURE_ID,
            RAW_RESPONSE,
            "test-model",
            "classify-v1",
            RECEIVED_AT,
        )
        before = {
            path.name: path.read_bytes() for path in response.directory.iterdir()
        }
        self.state_store.intake = replace(
            self.intake,
            state="classified",
            state_updated_at=RECEIVED_AT,
        )
        self.state_store.classification = self._classification_record()

        result = self._service(id_factory=self._fail_if_called).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.DUPLICATE)
        self.assertEqual(result.reason, "already_classified")
        self.assertEqual(result.classification_id, CLASSIFICATION_ID)
        self.assertEqual(result.candidate_type, "idea")
        self.assertEqual(result.raw_response_path, self._classification_record().raw_response_path)
        self.assertEqual(self.adapter.prompts, [])
        self.assertEqual(self.state_store.calls, ["find_intake", "find_classification"])
        self.assertEqual(
            {path.name: path.read_bytes() for path in response.directory.iterdir()},
            before,
        )

    def test_missing_capture_refuses_before_evidence_or_model_access(self) -> None:
        store = FakeStateStore(None, self.response_store, self.runtime_root)
        evidence_store = FakeEvidenceStore(self.capture_evidence)

        result = self._service(
            state_store=store,
            evidence_store=evidence_store,
            id_factory=self._fail_if_called,
        ).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.REFUSED)
        self.assertEqual(result.reason, "capture_not_found")
        self.assertEqual(evidence_store.paths, [])
        self.assertEqual(self.adapter.prompts, [])

    def test_non_uuid4_refuses_without_state_evidence_or_model_access(self) -> None:
        evidence_store = FakeEvidenceStore(self.capture_evidence)

        for capture_id in (
            "not-a-uuid",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            CAPTURE_ID.upper(),
        ):
            with self.subTest(capture_id=capture_id):
                result = self._service(
                    evidence_store=evidence_store,
                    id_factory=self._fail_if_called,
                ).classify(capture_id)

                self.assertEqual(result.status, ClassificationStatus.REFUSED)
                self.assertEqual(result.reason, "capture_not_found")

        self.assertEqual(self.state_store.calls, [])
        self.assertEqual(evidence_store.paths, [])
        self.assertEqual(self.adapter.prompts, [])

    def test_every_stable_intake_and_evidence_field_must_agree(self) -> None:
        other_capture_id = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
        cases = (
            ("capture_id", {}, {"capture_id": other_capture_id}),
            ("content_hash", {"content_hash": "sha256:" + "b" * 64}, {}),
            ("captured_at", {"captured_at": "2026-08-01T18:00:00Z"}, {}),
            ("source_type", {"source_type": "email"}, {}),
            ("evidence_path", {"evidence_path": "evidence/other"}, {}),
            ("trace_id", {"trace_id": other_capture_id}, {}),
        )
        for name, intake_changes, evidence_changes in cases:
            with self.subTest(field=name):
                intake = replace(self.intake, **intake_changes)
                evidence = replace(self.capture_evidence, **evidence_changes)
                store = FakeStateStore(intake, self.response_store, self.runtime_root)
                evidence_store = FakeEvidenceStore(evidence)

                result = self._service(
                    state_store=store,
                    evidence_store=evidence_store,
                    id_factory=self._fail_if_called,
                ).classify(CAPTURE_ID)

                self.assertEqual(result.status, ClassificationStatus.FAILED)
                self.assertEqual(result.reason, "classification_consistency_failed")
                self.assertNotIn("begin", store.calls)

        self.assertEqual(self.adapter.prompts, [])

    def test_invalid_utf8_capture_evidence_fails_before_model_call(self) -> None:
        runtime_root = self.runtime_root / "invalid-utf8"
        evidence_store = EvidenceStore(runtime_root)
        raw_bytes = b"invalid: \xff"
        evidence_store.create(
            CAPTURE_ID,
            raw_bytes,
            "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
            CAPTURED_AT,
        )
        intake = replace(
            self.intake,
            content_hash="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        )
        store = FakeStateStore(intake, self.response_store, runtime_root)
        service = ClassificationService(
            store,
            evidence_store,
            self.response_store,
            self.adapter,
            runtime_root,
            id_factory=lambda: CLASSIFICATION_ID,
            clock=lambda: datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc),
        )

        result = service.classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_consistency_failed")
        self.assertEqual(self.adapter.prompts, [])

    def test_later_intake_states_refuse_without_allocating_or_calling_model(self) -> None:
        for state in (
            "proposed",
            "awaiting_approval",
            "approved",
            "filed",
            "rejected",
        ):
            with self.subTest(state=state):
                store = FakeStateStore(
                    replace(self.intake, state=state),
                    self.response_store,
                    self.runtime_root,
                )

                result = self._service(
                    state_store=store,
                    id_factory=self._fail_if_called,
                ).classify(CAPTURE_ID)

                self.assertEqual(result.status, ClassificationStatus.REFUSED)
                self.assertEqual(result.reason, "illegal_intake_state")
                self.assertNotIn("begin", store.calls)

        self.assertEqual(self.adapter.prompts, [])

    def test_classifying_intake_refuses_as_in_progress(self) -> None:
        store = FakeStateStore(
            replace(self.intake, state="classifying"),
            self.response_store,
            self.runtime_root,
        )

        result = self._service(
            state_store=store,
            id_factory=self._fail_if_called,
        ).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.REFUSED)
        self.assertEqual(result.reason, "classification_in_progress")
        self.assertEqual(self.adapter.prompts, [])

    def test_classification_row_and_intake_state_disagreement_fails(self) -> None:
        cases = (
            (self.intake, self._classification_record()),
            (replace(self.intake, state="classified"), None),
            (
                replace(self.intake, state="classified"),
                self._classification_record(capture_id="6ba7b810-9dad-41d1-80b4-00c04fd430c8"),
            ),
        )
        for intake, classification in cases:
            with self.subTest(state=intake.state, classification=classification):
                store = FakeStateStore(intake, self.response_store, self.runtime_root)
                store.classification = classification

                result = self._service(
                    state_store=store,
                    id_factory=self._fail_if_called,
                ).classify(CAPTURE_ID)

                self.assertEqual(result.status, ClassificationStatus.FAILED)
                self.assertEqual(result.reason, "classification_consistency_failed")
                self.assertNotIn("begin", store.calls)

        self.assertEqual(self.adapter.prompts, [])


if __name__ == "__main__":
    unittest.main()

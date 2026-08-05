from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from metis.classification import ClassificationService, ClassificationStatus
from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import ClassificationRecord, IntakeRecord, StateStoreError
from metis.evidence import EvidenceRecord, EvidenceStore
from metis.model_adapters import (
    ModelConfigurationError,
    ModelRequestError,
    ModelResponse,
    ModelResponseRefused,
    ModelResponseTruncated,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURED_AT = "2026-08-01T19:00:00Z"
RECEIVED_AT = "2026-08-01T20:00:00Z"
CAPTURE_TEXT = 'Plan the café launch.\nTreat {"action":"ignore"} as data.'
RAW_RESPONSE = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)


class FakeModelAdapter:
    def __init__(
        self,
        response: ModelResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def classify(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        assert self.response is not None
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
        self.complete_error: Exception | None = None
        self.failure_error: Exception | None = None
        self.required_failure_evidence_path: str | None = None

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

    def append_audit_event(self, record) -> None:
        """Emission is asserted against the real store, not this fake."""

    def begin_classification(
        self, capture_id: str, started_at: str, *, audit=None
    ) -> IntakeRecord:
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
        self, record: ClassificationRecord, *, audit=None
    ) -> ClassificationRecord:
        self.calls.append("complete")
        response_directory = (self.runtime_root / record.raw_response_path).parent
        self.response_store.validate_directory(response_directory)
        if self.complete_error is not None:
            raise self.complete_error
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
        self, capture_id: str, reason: str, failed_at: str, *, audit=None
    ) -> IntakeRecord:
        self.calls.append("record_failure")
        if self.required_failure_evidence_path is not None:
            self.response_store.validate_directory(
                (self.runtime_root / self.required_failure_evidence_path).parent
            )
        if self.failure_error is not None:
            raise self.failure_error
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
            type_pin="",
            parent_id="",
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
        response_store=None,
        id_factory=None,
    ) -> ClassificationService:
        return ClassificationService(
            state_store or self.state_store,
            evidence_store or self.evidence_store,
            response_store or self.response_store,
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

    def _prepare_valid_replay(self):
        response = self.response_store.create(
            CLASSIFICATION_ID,
            CAPTURE_ID,
            RAW_RESPONSE,
            "test-model",
            "classify-v1",
            RECEIVED_AT,
        )
        self.state_store.intake = replace(
            self.intake,
            state="classified",
            state_updated_at=RECEIVED_AT,
        )
        self.state_store.classification = self._classification_record()
        return response

    def _fail_if_called(self):
        self.fail("factory must not be called")

    def _capture_snapshot(self) -> dict[str, bytes]:
        return {
            path.name: path.read_bytes()
            for path in self.capture_evidence.directory.iterdir()
        }

    def _assert_capture_unchanged(self, before: dict[str, bytes]) -> None:
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in self.capture_evidence.directory.iterdir()
            },
            before,
        )

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
        response = self._prepare_valid_replay()
        before = {
            path.name: path.read_bytes() for path in response.directory.iterdir()
        }

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

    def test_replay_refuses_duplicate_when_source_evidence_is_missing(self) -> None:
        self._prepare_valid_replay()
        self.capture_evidence.raw_path.unlink()

        result = self._service(id_factory=self._fail_if_called).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_consistency_failed")
        self.assertEqual(self.adapter.prompts, [])
        self.assertEqual(self.state_store.calls, ["find_intake", "find_classification"])

    def test_replay_refuses_duplicate_when_source_evidence_is_corrupt(self) -> None:
        self._prepare_valid_replay()
        self.capture_evidence.raw_path.write_bytes(b"corrupt source")

        result = self._service(id_factory=self._fail_if_called).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_consistency_failed")
        self.assertEqual(self.adapter.prompts, [])

    def test_replay_refuses_duplicate_when_source_row_disagrees(self) -> None:
        self._prepare_valid_replay()
        self.state_store.intake = replace(
            self.state_store.intake,
            content_hash="sha256:" + "b" * 64,
        )

        result = self._service(id_factory=self._fail_if_called).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_consistency_failed")
        self.assertEqual(self.adapter.prompts, [])

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

    def test_captured_state_invariants_must_agree_before_transition(self) -> None:
        cases = (
            {"failure_reason": "capture.previous"},
            {"state_updated_at": "2026-08-01T19:30:00Z"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                store = FakeStateStore(
                    replace(self.intake, **changes),
                    self.response_store,
                    self.runtime_root,
                )

                result = self._service(
                    state_store=store,
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
        capture_directory = runtime_root / "evidence" / CAPTURE_ID
        before = {
            path.name: path.read_bytes() for path in capture_directory.iterdir()
        }
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
        self.assertEqual(
            {path.name: path.read_bytes() for path in capture_directory.iterdir()},
            before,
        )

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

    def test_orphan_classification_row_is_a_consistency_failure(self) -> None:
        store = FakeStateStore(None, self.response_store, self.runtime_root)
        store.classification = self._classification_record()

        result = self._service(
            state_store=store,
            id_factory=self._fail_if_called,
        ).classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_consistency_failed")
        self.assertEqual(self.adapter.prompts, [])

    def test_source_survives_classification_failure(self) -> None:
        before = self._capture_snapshot()
        self.adapter.response = ModelResponse("test-model", "not json")

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_invalid")
        self._assert_capture_unchanged(before)

    def test_model_configuration_failure_records_failed_without_response_evidence(
        self,
    ) -> None:
        before = self._capture_snapshot()
        self.adapter.error = ModelConfigurationError(
            "model_configuration_failed",
            "unsafe provider configuration detail",
        )

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_configuration_failed")
        self.assertIsNone(result.raw_response_path)
        self.assertEqual(
            self.state_store.intake.failure_reason,
            "classification.model_configuration_failed",
        )
        self.assertFalse((self.runtime_root / "classification-evidence").exists())
        self.assertNotIn("unsafe", result.message)
        self._assert_capture_unchanged(before)

    def test_model_request_failure_records_failed_without_response_evidence(
        self,
    ) -> None:
        before = self._capture_snapshot()
        self.adapter.error = ModelRequestError(
            "model_request_failed",
            "unsafe request detail",
        )

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_request_failed")
        self.assertIsNone(result.raw_response_path)
        self.assertFalse((self.runtime_root / "classification-evidence").exists())
        self._assert_capture_unchanged(before)

    def test_refused_response_is_preserved_before_failed_result(self) -> None:
        before = self._capture_snapshot()
        refused_text = '{"refusal":"cannot classify"}\n'
        self.adapter.error = ModelResponseRefused(
            "model_response_refused",
            "unsafe refusal detail",
            model_id="test-model",
            raw_text=refused_text,
        )
        expected_path = (
            f"classification-evidence/{CLASSIFICATION_ID}/raw-response.txt"
        )
        self.state_store.required_failure_evidence_path = expected_path

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_refused")
        self.assertEqual(result.raw_response_path, expected_path)
        self.assertEqual(
            (self.runtime_root / expected_path).read_bytes(),
            refused_text.encode("utf-8"),
        )
        self.assertLess(
            self.state_store.calls.index("record_failure"),
            len(self.state_store.calls),
        )
        self._assert_capture_unchanged(before)

    def test_truncated_response_is_preserved_before_failed_result(self) -> None:
        before = self._capture_snapshot()
        truncated_text = '{"candidate_type":"idea"'
        self.adapter.error = ModelResponseTruncated(
            "model_response_truncated",
            "unsafe truncation detail",
            model_id="test-model",
            raw_text=truncated_text,
        )

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_truncated")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_bytes(),
            truncated_text.encode("utf-8"),
        )
        self._assert_capture_unchanged(before)

    def test_invalid_json_is_preserved_before_failed_result(self) -> None:
        before = self._capture_snapshot()
        invalid_text = "not-json\n"
        self.adapter.response = ModelResponse("test-model", invalid_text)

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_invalid")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_bytes(),
            invalid_text.encode("utf-8"),
        )
        self._assert_capture_unchanged(before)

    def test_duplicate_json_keys_are_preserved_and_rejected(self) -> None:
        before = self._capture_snapshot()
        duplicate_keys = (
            '{"candidate_type":"idea","candidate_type":"task",'
            '"sensitivity":"normal","confidence":0.5}'
        )
        self.adapter.response = ModelResponse("test-model", duplicate_keys)

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_invalid")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_bytes(),
            duplicate_keys.encode("utf-8"),
        )
        self.assertEqual(self.state_store.intake.state, "failed")
        self._assert_capture_unchanged(before)

    def test_extra_or_missing_keys_are_rejected(self) -> None:
        cases = (
            '{"candidate_type":"idea","sensitivity":"normal"}',
            (
                '{"candidate_type":"idea","sensitivity":"normal",'
                '"confidence":0.5,"extra":true}'
            ),
        )
        for index, raw_text in enumerate(cases):
            with self.subTest(raw_text=raw_text):
                runtime_root = self.runtime_root / f"keys-{index}"
                evidence_store, intake = self._capture_fixture(runtime_root)
                response_store = ClassificationEvidenceStore(runtime_root)
                store = FakeStateStore(intake, response_store, runtime_root)
                adapter = FakeModelAdapter(ModelResponse("test-model", raw_text))
                capture_directory = runtime_root / "evidence" / CAPTURE_ID
                before = {
                    path.name: path.read_bytes()
                    for path in capture_directory.iterdir()
                }
                service = ClassificationService(
                    store,
                    evidence_store,
                    response_store,
                    adapter,
                    runtime_root,
                    id_factory=lambda: CLASSIFICATION_ID,
                    clock=lambda: datetime(
                        2026, 8, 1, 20, 0, tzinfo=timezone.utc
                    ),
                )

                result = service.classify(CAPTURE_ID)

                self.assertEqual(result.reason, "model_response_invalid")
                self.assertEqual(
                    (runtime_root / result.raw_response_path).read_text(
                        encoding="utf-8"
                    ),
                    raw_text,
                )
                self.assertEqual(
                    {
                        path.name: path.read_bytes()
                        for path in capture_directory.iterdir()
                    },
                    before,
                )

    def test_invalid_enum_confidence_boolean_nonfinite_and_out_of_range_are_rejected(
        self,
    ) -> None:
        cases = (
            '{"candidate_type":"unknown","sensitivity":"normal","confidence":0.5}',
            '{"candidate_type":"idea","sensitivity":"public","confidence":0.5}',
            '{"candidate_type":"idea","sensitivity":"normal","confidence":true}',
            '{"candidate_type":"idea","sensitivity":"normal","confidence":NaN}',
            '{"candidate_type":"idea","sensitivity":"normal","confidence":-0.1}',
            '{"candidate_type":"idea","sensitivity":"normal","confidence":1.1}',
            '{"candidate_type":[],"sensitivity":"normal","confidence":0.5}',
        )
        for index, raw_text in enumerate(cases):
            with self.subTest(raw_text=raw_text):
                runtime_root = self.runtime_root / f"invalid-{index}"
                evidence_store, intake = self._capture_fixture(runtime_root)
                response_store = ClassificationEvidenceStore(runtime_root)
                store = FakeStateStore(intake, response_store, runtime_root)
                adapter = FakeModelAdapter(ModelResponse("test-model", raw_text))
                capture_directory = runtime_root / "evidence" / CAPTURE_ID
                before = {
                    path.name: path.read_bytes()
                    for path in capture_directory.iterdir()
                }
                service = ClassificationService(
                    store,
                    evidence_store,
                    response_store,
                    adapter,
                    runtime_root,
                    id_factory=lambda: CLASSIFICATION_ID,
                    clock=lambda: datetime(
                        2026, 8, 1, 20, 0, tzinfo=timezone.utc
                    ),
                )

                result = service.classify(CAPTURE_ID)

                self.assertEqual(result.status, ClassificationStatus.FAILED)
                self.assertEqual(result.reason, "model_response_invalid")
                self.assertEqual(
                    {
                        path.name: path.read_bytes()
                        for path in capture_directory.iterdir()
                    },
                    before,
                )

    def test_huge_integer_confidence_is_preserved_and_recorded_failed(self) -> None:
        before = self._capture_snapshot()
        raw_text = (
            '{"candidate_type":"idea","sensitivity":"normal","confidence":'
            + str(10**400)
            + "}"
        )
        self.adapter.response = ModelResponse("test-model", raw_text)

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "model_response_invalid")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_bytes(),
            raw_text.encode("utf-8"),
        )
        self.assertEqual(self.state_store.intake.state, "failed")
        self.assertEqual(
            self.state_store.intake.failure_reason,
            "classification.model_response_invalid",
        )
        self._assert_capture_unchanged(before)

    def test_response_evidence_failure_never_persists_classification(self) -> None:
        before = self._capture_snapshot()
        partial = self.runtime_root / "classification-evidence" / CLASSIFICATION_ID
        partial.mkdir(parents=True)
        (partial / "partial.txt").write_text("preserve me", encoding="utf-8")
        partial_before = {
            path.name: path.read_bytes() for path in partial.iterdir()
        }

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "response_evidence_failed")
        self.assertIsNone(result.raw_response_path)
        self.assertNotIn("complete", self.state_store.calls)
        self.assertIsNone(self.state_store.classification)
        self.assertEqual(
            {path.name: path.read_bytes() for path in partial.iterdir()},
            partial_before,
        )
        self._assert_capture_unchanged(before)

    def test_completion_failure_never_reports_classified(self) -> None:
        before = self._capture_snapshot()
        self.state_store.complete_error = StateStoreError("unsafe database detail")

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_persistence_failed")
        self.assertIsNone(self.state_store.classification)
        self.assertEqual(self.state_store.intake.state, "failed")
        self.assertIsNotNone(result.raw_response_path)
        self._assert_capture_unchanged(before)

    def test_failure_recording_failure_reports_state_undetermined(self) -> None:
        before = self._capture_snapshot()
        self.adapter.error = ModelRequestError(
            "model_request_failed",
            "unsafe request detail",
        )
        self.state_store.failure_error = StateStoreError("unsafe state detail")

        result = self._service().classify(CAPTURE_ID)

        self.assertEqual(result.status, ClassificationStatus.FAILED)
        self.assertEqual(result.reason, "classification_state_undetermined")
        self.assertEqual(self.state_store.intake.state, "classifying")
        self.assertNotIn("unsafe", result.message)
        self._assert_capture_unchanged(before)

    def test_recorded_classification_failure_can_retry(self) -> None:
        before = self._capture_snapshot()
        self.adapter.error = ModelRequestError(
            "model_request_failed",
            "first attempt failed",
        )
        identifiers = iter(
            (CLASSIFICATION_ID, "01K1D5Q5M00000000000000001")
        )
        service = self._service(id_factory=lambda: next(identifiers))

        first = service.classify(CAPTURE_ID)
        self.adapter.error = None
        self.adapter.response = ModelResponse("test-model", RAW_RESPONSE)
        second = service.classify(CAPTURE_ID)

        self.assertEqual(first.reason, "model_request_failed")
        self.assertEqual(second.status, ClassificationStatus.CLASSIFIED)
        self.assertEqual(
            second.classification_id,
            "01K1D5Q5M00000000000000001",
        )
        self.assertEqual(len(self.adapter.prompts), 2)
        self.assertEqual(self.state_store.intake.state, "classified")
        self._assert_capture_unchanged(before)

    def _capture_fixture(
        self, runtime_root: Path
    ) -> tuple[EvidenceStore, IntakeRecord]:
        evidence_store = EvidenceStore(runtime_root)
        raw_bytes = CAPTURE_TEXT.encode("utf-8")
        evidence_store.create(
            CAPTURE_ID,
            raw_bytes,
            "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
            CAPTURED_AT,
        )
        return evidence_store, replace(self.intake)


if __name__ == "__main__":
    unittest.main()

"""Deterministic orchestration for classification of immutable captures."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID

from .classification_evidence import (
    ClassificationEvidenceConsistencyError,
    ClassificationEvidenceStore,
)
from .data_access import (
    ClassificationRecord,
    StateStore,
    StateStoreError,
    StateTransitionRefused,
)
from .evidence import EvidenceConsistencyError, EvidenceStore
from .identifiers import is_ulid, new_ulid
from .model_adapters import ModelAdapter
from .prompts import PROMPT_VERSION, load_classification_prompt


ROUTING = {
    "idea": "proposal:idea",
    "reference": "proposal:reference",
    "decision": "proposal:decision",
    "question": "proposal:question",
    "task": "proposal:task",
}
SENSITIVITIES = {"normal", "sensitive"}
RESPONSE_KEYS = {"candidate_type", "sensitivity", "confidence"}


class ClassificationStatus(str, Enum):
    CLASSIFIED = "classified"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class ClassificationResult:
    status: ClassificationStatus
    capture_id: str
    classification_id: Optional[str]
    candidate_type: Optional[str]
    sensitivity: Optional[str]
    confidence: Optional[float]
    routing: Optional[str]
    raw_response_path: Optional[str]
    reason: Optional[str]
    message: Optional[str]


class ClassificationService:
    def __init__(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        response_store: ClassificationEvidenceStore,
        model_adapter: ModelAdapter,
        runtime_root: Path,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._evidence_store = evidence_store
        self._response_store = response_store
        self._model_adapter = model_adapter
        self._runtime_root = Path(runtime_root)
        self._id_factory = id_factory
        self._clock = clock

    def classify(self, capture_id: str) -> ClassificationResult:
        try:
            parsed_capture_id = UUID(capture_id)
        except (AttributeError, TypeError, ValueError):
            parsed_capture_id = None
        if (
            parsed_capture_id is None
            or parsed_capture_id.version != 4
            or str(parsed_capture_id) != capture_id
        ):
            return self._result(
                ClassificationStatus.REFUSED,
                capture_id,
                reason="capture_not_found",
                message="capture was not found",
            )

        try:
            intake = self._state_store.find_intake_by_capture_id(capture_id)
            existing = self._state_store.find_classification_by_capture_id(capture_id)
        except StateStoreError:
            return self._result(
                ClassificationStatus.FAILED,
                capture_id,
                reason="classification_state_undetermined",
                message="classification state could not be determined",
            )
        if intake is None:
            return self._result(
                ClassificationStatus.REFUSED,
                capture_id,
                reason="capture_not_found",
                message="capture was not found",
            )
        if existing is not None or intake.state == "classified":
            if existing is None or intake.state != "classified":
                return self._consistency_failure(capture_id)
            return self._replay(intake, existing)

        if intake.state == "classifying":
            return self._result(
                ClassificationStatus.REFUSED,
                capture_id,
                reason="classification_in_progress",
                message="classification is already in progress",
            )
        eligible_retry = (
            intake.state == "failed"
            and intake.failure_reason is not None
            and intake.failure_reason.startswith("classification.")
        )
        if intake.state != "captured" and not eligible_retry:
            return self._result(
                ClassificationStatus.REFUSED,
                capture_id,
                reason="illegal_intake_state",
                message=f"classification is not allowed from state {intake.state}",
            )

        if (
            intake.source_type != "cli-typed"
            or intake.evidence_path != f"evidence/{capture_id}"
            or intake.trace_id != capture_id
        ):
            return self._consistency_failure(capture_id)

        try:
            evidence = self._evidence_store.validate_directory(
                self._runtime_root / intake.evidence_path
            )
        except EvidenceConsistencyError:
            return self._consistency_failure(capture_id)
        if not self._row_matches_evidence(intake, evidence):
            return self._consistency_failure(capture_id)

        classification_id = self._id_factory()
        if not is_ulid(classification_id):
            return self._result(
                ClassificationStatus.FAILED,
                capture_id,
                reason="classification_consistency_failed",
                message="classification ID allocation failed",
            )
        received_at = self._timestamp()
        try:
            self._state_store.begin_classification(capture_id, received_at)
        except StateTransitionRefused as error:
            if error.record.state == "classifying":
                return self._result(
                    ClassificationStatus.REFUSED,
                    capture_id,
                    reason="classification_in_progress",
                    message="classification is already in progress",
                )
            return self._result(
                ClassificationStatus.REFUSED,
                capture_id,
                reason="illegal_intake_state",
                message=f"classification is not allowed from state {error.record.state}",
            )
        except StateStoreError:
            return self._result(
                ClassificationStatus.FAILED,
                capture_id,
                classification_id=classification_id,
                reason="classification_state_undetermined",
                message="classification start could not be determined",
            )

        try:
            captured_text = evidence.raw_path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError):
            return self._failure_after_start(
                capture_id,
                classification_id,
                "classification_consistency_failed",
            )
        prompt = load_classification_prompt().replace(
            "{{CAPTURE_JSON}}",
            json.dumps(captured_text, ensure_ascii=False),
        )
        model_response = self._model_adapter.classify(prompt)
        response_evidence = self._response_store.create(
            classification_id,
            capture_id,
            model_response.raw_text,
            model_response.model_id,
            PROMPT_VERSION,
            received_at,
        )
        response_evidence = self._response_store.validate_directory(
            response_evidence.directory
        )
        candidate_type, sensitivity, confidence = self._parse_response(
            model_response.raw_text
        )
        routing = ROUTING[candidate_type]
        record = ClassificationRecord(
            classification_id=classification_id,
            capture_id=capture_id,
            candidate_type=candidate_type,
            sensitivity=sensitivity,
            confidence=confidence,
            routing=routing,
            model_id=model_response.model_id,
            prompt_version=PROMPT_VERSION,
            raw_response_path=str(
                Path(response_evidence.evidence_path) / "raw-response.txt"
            ),
            created_at=received_at,
        )
        self._state_store.complete_classification(record)
        return self._result(
            ClassificationStatus.CLASSIFIED,
            capture_id,
            classification_id=classification_id,
            candidate_type=candidate_type,
            sensitivity=sensitivity,
            confidence=confidence,
            routing=routing,
            raw_response_path=record.raw_response_path,
        )

    def _replay(
        self,
        intake,
        record: ClassificationRecord,
    ) -> ClassificationResult:
        expected_raw_path = (
            f"classification-evidence/{record.classification_id}/raw-response.txt"
        )
        if (
            record.capture_id != intake.capture_id
            or record.candidate_type not in ROUTING
            or record.sensitivity not in SENSITIVITIES
            or type(record.confidence) not in (int, float)
            or not math.isfinite(record.confidence)
            or not 0 <= record.confidence <= 1
            or record.routing != ROUTING[record.candidate_type]
            or record.raw_response_path != expected_raw_path
        ):
            return self._consistency_failure(intake.capture_id)
        try:
            evidence = self._response_store.validate_directory(
                self._runtime_root / Path(record.raw_response_path).parent
            )
            candidate_type, sensitivity, confidence = self._parse_response(
                evidence.raw_path.read_text(encoding="utf-8")
            )
        except (
            ClassificationEvidenceConsistencyError,
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return self._consistency_failure(intake.capture_id)
        if (
            evidence.classification_id != record.classification_id
            or evidence.capture_id != record.capture_id
            or evidence.model_id != record.model_id
            or evidence.prompt_version != record.prompt_version
            or evidence.received_at != record.created_at
            or candidate_type != record.candidate_type
            or sensitivity != record.sensitivity
            or confidence != record.confidence
        ):
            return self._consistency_failure(intake.capture_id)
        return self._result(
            ClassificationStatus.DUPLICATE,
            intake.capture_id,
            classification_id=record.classification_id,
            candidate_type=record.candidate_type,
            sensitivity=record.sensitivity,
            confidence=float(record.confidence),
            routing=record.routing,
            raw_response_path=record.raw_response_path,
            reason="already_classified",
            message="capture is already classified",
        )

    def _failure_after_start(
        self,
        capture_id: str,
        classification_id: str,
        reason: str,
        *,
        raw_response_path: Optional[str] = None,
    ) -> ClassificationResult:
        try:
            self._state_store.record_classification_failure(
                capture_id,
                f"classification.{reason}",
                self._timestamp(),
            )
        except (StateStoreError, StateTransitionRefused):
            return self._result(
                ClassificationStatus.FAILED,
                capture_id,
                classification_id=classification_id,
                raw_response_path=raw_response_path,
                reason="classification_state_undetermined",
                message="classification failure state could not be determined",
            )
        return self._result(
            ClassificationStatus.FAILED,
            capture_id,
            classification_id=classification_id,
            raw_response_path=raw_response_path,
            reason=reason,
            message="classification failed",
        )

    def _consistency_failure(self, capture_id: str) -> ClassificationResult:
        return self._result(
            ClassificationStatus.FAILED,
            capture_id,
            reason="classification_consistency_failed",
            message="capture, evidence, or classification state does not agree",
        )

    def _parse_response(self, raw_text: str) -> tuple[str, str, float]:
        payload = json.loads(raw_text)
        if type(payload) is not dict or set(payload) != RESPONSE_KEYS:
            raise ValueError("model response keys are invalid")
        candidate_type = payload["candidate_type"]
        sensitivity = payload["sensitivity"]
        confidence = payload["confidence"]
        if candidate_type not in ROUTING:
            raise ValueError("candidate type is invalid")
        if sensitivity not in SENSITIVITIES:
            raise ValueError("sensitivity is invalid")
        if (
            type(confidence) not in (int, float)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidence is invalid")
        return candidate_type, sensitivity, float(confidence)

    def _row_matches_evidence(self, intake, evidence) -> bool:
        return (
            intake.capture_id == evidence.capture_id
            and intake.content_hash == evidence.content_hash
            and intake.captured_at == evidence.captured_at
            and intake.source_type == "cli-typed"
            and intake.evidence_path == evidence.evidence_path
            and intake.trace_id == intake.capture_id
        )

    def _timestamp(self) -> str:
        return (
            self._clock()
            .astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _result(
        self,
        status: ClassificationStatus,
        capture_id: str,
        *,
        classification_id: Optional[str] = None,
        candidate_type: Optional[str] = None,
        sensitivity: Optional[str] = None,
        confidence: Optional[float] = None,
        routing: Optional[str] = None,
        raw_response_path: Optional[str] = None,
        reason: Optional[str] = None,
        message: Optional[str] = None,
    ) -> ClassificationResult:
        return ClassificationResult(
            status=status,
            capture_id=capture_id,
            classification_id=classification_id,
            candidate_type=candidate_type,
            sensitivity=sensitivity,
            confidence=confidence,
            routing=routing,
            raw_response_path=raw_response_path,
            reason=reason,
            message=message,
        )

"""Deterministic orchestration for immutable typed capture."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from uuid import UUID, uuid4

from .audit import OUTCOMES, AuditTrail
from .data_access import (
    IntakeRecord,
    IntakeRegistrationStatus,
    StateStore,
    StateStoreError,
)
from .evidence import (
    PIN_TYPES,
    EvidenceCollision,
    EvidenceConsistencyError,
    EvidenceRecord,
    EvidenceStore,
    EvidenceWriteError,
)


class CaptureStatus(str, Enum):
    CAPTURED = "captured"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class CaptureResult:
    status: CaptureStatus
    capture_id: Optional[str]
    evidence_path: Optional[str]
    reason: Optional[str]
    message: Optional[str]


class CaptureService:
    def __init__(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._evidence_store = evidence_store
        self._id_factory = id_factory
        self._clock = clock
        self._audit = AuditTrail(state_store, clock=clock)

    def capture(
        self,
        text: str,
        *,
        type_pin: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
    ) -> CaptureResult:
        result = self._capture(text, type_pin, parent_goal_id)
        if result.status is not CaptureStatus.CAPTURED:
            # Nothing was registered, so no transition carried this outcome.
            self._audit.record(
                "command.capture",
                OUTCOMES[result.status.value],
                capture_id=result.capture_id,
                detail={"status": result.status.value, "reason": result.reason},
            )
        return result

    def _capture(
        self,
        text: str,
        type_pin: Optional[str],
        parent_goal_id: Optional[str],
    ) -> CaptureResult:
        # Refuse an incoherent pin before anything is written (ADR-021).
        if type_pin is not None and type_pin not in PIN_TYPES:
            return CaptureResult(
                CaptureStatus.REFUSED,
                None,
                None,
                "pin_invalid",
                f"type pin must be one of {sorted(PIN_TYPES)}",
            )
        if (type_pin == "project") != (parent_goal_id is not None):
            return CaptureResult(
                CaptureStatus.REFUSED,
                None,
                None,
                "pin_incomplete",
                "a project pin requires a parent goal, and only a project may carry one",
            )

        try:
            raw_bytes = text.encode("utf-8")
        except UnicodeEncodeError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "utf8_encoding_failed",
                str(error),
            )
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

        try:
            existing = self._state_store.find_intake_by_content_hash(content_hash)
        except StateStoreError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "state_lookup_failed",
                str(error),
            )

        try:
            matches = self._evidence_store.find_all_by_content_hash(content_hash)
            # ponytail: Task 4 replaces this with the composite pin decision, which
            # is what makes two parents legal. Until then one hash is one capture,
            # so a second match stays the fail-closed inconsistency it is today.
            if len(matches) > 1:
                raise EvidenceConsistencyError(
                    f"multiple evidence records match content hash: {content_hash}"
                )
            evidence = matches[0] if matches else None
        except EvidenceConsistencyError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                error.evidence_path,
                "evidence_inconsistent",
                str(error),
            )

        # Identical text under a different pin is a different intent, not a replay.
        # Both resolution paths below must refuse it, or one stays fail-open.
        if evidence is not None and not self._pin_matches(
            evidence, type_pin, parent_goal_id
        ):
            return CaptureResult(
                CaptureStatus.REFUSED,
                evidence.capture_id,
                evidence.evidence_path,
                "pin_conflict",
                "this text was already captured under a different planning pin",
            )

        if existing is not None:
            if evidence is not None and self._row_matches_evidence(existing, evidence):
                return CaptureResult(
                    CaptureStatus.DUPLICATE,
                    existing.capture_id,
                    existing.evidence_path,
                    "exact_replay",
                    None,
                )
            return CaptureResult(
                CaptureStatus.FAILED,
                existing.capture_id,
                existing.evidence_path,
                "state_evidence_mismatch",
                "state row and evidence do not agree",
            )

        if evidence is not None:
            return self._register_evidence(evidence, created_new=False)

        capture_uuid = self._id_factory()
        if not isinstance(capture_uuid, UUID) or capture_uuid.version != 4:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "invalid_capture_id",
                "id factory did not return a UUID4",
            )

        capture_id = str(capture_uuid)
        captured_at = (
            self._clock()
            .astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        try:
            evidence = self._evidence_store.create(
                capture_id,
                raw_bytes,
                content_hash,
                captured_at,
                type_pin,
                parent_goal_id,
            )
            evidence = self._evidence_store.validate_directory(evidence.directory)
        except EvidenceCollision as error:
            return CaptureResult(
                CaptureStatus.REFUSED,
                capture_id,
                error.evidence_path,
                "evidence_collision",
                str(error),
            )
        except EvidenceConsistencyError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                capture_id,
                error.evidence_path,
                "evidence_inconsistent",
                str(error),
            )
        except EvidenceWriteError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                capture_id,
                error.evidence_path,
                "evidence_write_failed",
                str(error),
            )

        return self._register_evidence(evidence, created_new=True)

    def _register_evidence(
        self,
        evidence: EvidenceRecord,
        *,
        created_new: bool,
    ) -> CaptureResult:
        record = IntakeRecord(
            capture_id=evidence.capture_id,
            content_hash=evidence.content_hash,
            captured_at=evidence.captured_at,
            source_type="cli-typed",
            evidence_path=evidence.evidence_path,
            state="captured",
            state_updated_at=evidence.captured_at,
            failure_reason=None,
            trace_id=evidence.capture_id,
        )
        try:
            registration = self._state_store.register_intake(
                record,
                audit=self._audit.event(
                    "capture.written",
                    "success",
                    capture_id=record.capture_id,
                    detail={"state": "captured", "content_hash": record.content_hash},
                ),
            )
        except StateStoreError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                evidence.capture_id,
                evidence.evidence_path,
                "state_registration_failed",
                str(error),
            )

        if registration.status is IntakeRegistrationStatus.REGISTERED:
            return CaptureResult(
                CaptureStatus.CAPTURED,
                evidence.capture_id,
                evidence.evidence_path,
                None,
                None,
            )
        if not created_new:
            if self._row_matches_evidence(registration.record, evidence):
                return CaptureResult(
                    CaptureStatus.DUPLICATE,
                    evidence.capture_id,
                    evidence.evidence_path,
                    "exact_replay",
                    None,
                )
            return CaptureResult(
                CaptureStatus.FAILED,
                evidence.capture_id,
                evidence.evidence_path,
                "state_evidence_mismatch",
                "state row and evidence do not agree",
            )
        return CaptureResult(
            CaptureStatus.FAILED,
            evidence.capture_id,
            evidence.evidence_path,
            "late_duplicate_registration",
            "state registration reported a duplicate after evidence creation",
        )

    @staticmethod
    def _pin_matches(
        evidence: EvidenceRecord,
        type_pin: Optional[str],
        parent_goal_id: Optional[str],
    ) -> bool:
        return (
            evidence.type_pin == type_pin
            and evidence.parent_id == parent_goal_id
        )

    def _row_matches_evidence(
        self,
        row: IntakeRecord,
        evidence: EvidenceRecord,
    ) -> bool:
        return (
            row.capture_id == evidence.capture_id
            and row.content_hash == evidence.content_hash
            and row.captured_at == evidence.captured_at
            and row.source_type == "cli-typed"
            and row.evidence_path == evidence.evidence_path
            and row.state == "captured"
            and row.state_updated_at == row.captured_at
            and row.failure_reason is None
            and row.trace_id == row.capture_id
        )

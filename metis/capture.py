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
    PARENT_REQUIRED,
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
        parent_id: Optional[str] = None,
    ) -> CaptureResult:
        result = self._capture(text, type_pin, parent_id)
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
        parent_id: Optional[str],
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
        if (type_pin in PARENT_REQUIRED) != (parent_id is not None):
            return CaptureResult(
                CaptureStatus.REFUSED,
                None,
                None,
                "pin_incomplete",
                "a project or task pin requires a parent, and only they may carry one",
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

        # Evidence resolves first: it is the immutable record, and which state
        # row to look up depends on whether it already named a capture id.
        try:
            candidates = self._evidence_store.find_all_by_content_hash(content_hash)
        except EvidenceConsistencyError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                error.evidence_path,
                "evidence_inconsistent",
                str(error),
            )

        # Identical text under a different parent is a different intent, not a
        # replay; identical text and parent under a different pin is a conflict.
        # The conflict check runs first so a differing pin is never read as one.
        conflicting = [
            record
            for record in candidates
            if record.parent_id == parent_id and record.type_pin != type_pin
        ]
        if conflicting:
            return CaptureResult(
                CaptureStatus.REFUSED,
                conflicting[0].capture_id,
                conflicting[0].evidence_path,
                "pin_conflict",
                "this text was already captured under a different planning pin",
            )
        # Two parents are legal, two records under the *same* key are not: the
        # intake unique key forbids the matching row, so picking one and
        # continuing would fail open on corruption the old single-row lookup
        # caught. Only an exact key collision is inconsistent now.
        matching = [
            record
            for record in candidates
            if record.type_pin == type_pin and record.parent_id == parent_id
        ]
        if len(matching) > 1:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "evidence_inconsistent",
                f"multiple evidence records match content hash: {content_hash}",
            )
        evidence = matching[0] if matching else None

        # By capture id when evidence resolved one: a row written before ADR-022
        # carries the sentinel while its evidence records a pin, so a pin-key
        # lookup would miss it, fall through to registration, and collide on the
        # primary key with no row to resolve against. The pin key still covers a
        # state row with no evidence behind it, which must stay a mismatch.
        try:
            existing = (
                self._state_store.find_intake_by_capture_id(evidence.capture_id)
                if evidence is not None
                else self._state_store.find_intake_by_pin_key(
                    content_hash, type_pin or "", parent_id or ""
                )
            )
        except StateStoreError as error:
            return CaptureResult(
                CaptureStatus.FAILED,
                None,
                None,
                "state_lookup_failed",
                str(error),
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
                self._mismatch_reason(existing, evidence),
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
                parent_id,
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
            # The projection: derived from evidence, carried only for the
            # uniqueness key and the consistency check (ADR-022 clause 9).
            type_pin=evidence.type_pin or "",
            parent_id=evidence.parent_id or "",
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
                self._mismatch_reason(registration.record, evidence),
                "state row and evidence do not agree",
            )
        return CaptureResult(
            CaptureStatus.FAILED,
            evidence.capture_id,
            evidence.evidence_path,
            "late_duplicate_registration",
            "state registration reported a duplicate after evidence creation",
        )

    @classmethod
    def _mismatch_reason(
        cls,
        row: IntakeRecord,
        evidence: Optional[EvidenceRecord],
    ) -> str:
        """Divergence has two causes, and they need different operator moves."""
        if evidence is not None and cls._pin_is_unprojected(row, evidence):
            return "intake_pin_unprojected"
        return "state_evidence_mismatch"

    @staticmethod
    def _pin_is_unprojected(row: IntakeRecord, evidence: EvidenceRecord) -> bool:
        """A pre-ADR-022 row the migration could not project, not tampering.

        Both columns must be empty. A half-projected row is deliberately left to
        `state_evidence_mismatch`: no migration or application path produces
        one, so the likeliest cause is a repair UPDATE run halfway, and that
        should be investigated rather than re-run (ADR-022 clause 9).
        """
        return (
            row.type_pin == ""
            and row.parent_id == ""
            and (evidence.type_pin is not None or evidence.parent_id is not None)
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
            # The projection must agree with the evidence it was derived from;
            # divergence fails closed rather than preferring either side.
            and row.type_pin == (evidence.type_pin or "")
            and row.parent_id == (evidence.parent_id or "")
        )

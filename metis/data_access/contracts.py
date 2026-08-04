"""Engine-agnostic operational-state contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple, runtime_checkable


class StateStoreError(RuntimeError):
    """Raised when operational-state persistence cannot be determined."""


class IntakeRegistrationStatus(str, Enum):
    REGISTERED = "registered"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class IntakeRecord:
    capture_id: str
    content_hash: str
    captured_at: str
    source_type: str
    evidence_path: str
    state: str
    state_updated_at: str
    failure_reason: Optional[str]
    trace_id: str
    # A derived projection of the capture pin, carried here for the uniqueness
    # key and consistency checks only. Evidence meta is the record of what was
    # captured (ADR-003); routing, rendering, and parent resolution read the pin
    # from there, never from these columns (ADR-022 clause 9). Never None: the
    # empty string is the sentinel for an unpinned capture.
    type_pin: str
    parent_id: str


@dataclass(frozen=True)
class ClassificationRecord:
    classification_id: str
    capture_id: str
    candidate_type: str
    sensitivity: str
    confidence: float
    routing: str
    model_id: str
    prompt_version: str
    raw_response_path: str
    created_at: str


@dataclass(frozen=True)
class ProposalReservationRecord:
    proposal_id: str
    capture_id: str
    classification_id: str
    lease_token: str
    reserved_at: str
    lease_expires_at: str


@dataclass(frozen=True)
class ProposalRecord:
    proposal_id: str
    capture_id: str
    classification_id: str
    note_type: str
    title: str
    body_path: str
    proposed_links: str
    evidence_refs: str
    confidence: float
    sensitivity: str
    risk_level: str
    reason: str
    uncertainties_json: str
    model_id: str
    prompt_version: str
    raw_response_path: str
    content_hash: str
    draft_note_path: Optional[str]
    state: str
    created_at: str


@dataclass(frozen=True)
class AuditEventRecord:
    """One append-only record of something the orchestrator did."""

    event_id: str
    trace_id: str
    capture_id: Optional[str]
    actor: str
    action: str
    outcome: str
    detail: str
    created_at: str


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    proposal_id: str
    decision: str
    approver: str
    observed_status: str
    detected_at: str
    committed_at: Optional[str]
    revoked_at: Optional[str]


class StateTransitionRefused(RuntimeError):
    """Raised when a known intake state does not allow a requested transition."""

    def __init__(self, message: str, record: IntakeRecord) -> None:
        super().__init__(message)
        self.record = record


@dataclass(frozen=True)
class IntakeRegistrationResult:
    status: IntakeRegistrationStatus
    record: IntakeRecord


@runtime_checkable
class StateStore(Protocol):
    """Lifecycle contract for an operational-state implementation."""

    @property
    def schema_version(self) -> int:
        """Return the applied schema version."""

    def initialize(self) -> None:
        """Apply all pending schema migrations."""

    def close(self) -> None:
        """Release resources held by the store."""

    def find_intake_by_pin_key(
        self,
        content_hash: str,
        type_pin: str,
        parent_id: str,
    ) -> Optional[IntakeRecord]:
        """Return the intake row registered for a uniqueness key, if one exists."""

    def find_intakes_by_content_hash(
        self,
        content_hash: str,
    ) -> Tuple[IntakeRecord, ...]:
        """Return every intake row sharing a content hash, ordered by capture ID."""

    def find_intake_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[IntakeRecord]:
        """Return the intake row registered for a capture ID, if one exists."""

    def find_classification_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ClassificationRecord]:
        """Return the classification for a capture ID, if one exists."""

    def register_intake(
        self,
        record: IntakeRecord,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRegistrationResult:
        """Register a captured intake row or return the exact existing duplicate."""

    def begin_classification(
        self,
        capture_id: str,
        started_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Move an eligible intake into the classifying state."""

    def complete_classification(
        self,
        record: ClassificationRecord,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> ClassificationRecord:
        """Atomically persist a classification and mark its intake classified."""

    def record_classification_failure(
        self,
        capture_id: str,
        reason: str,
        failed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Move a classifying intake into a known failed state."""

    def find_proposal_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ProposalRecord]:
        """Return the proposal for a capture ID, if one exists."""

    def find_proposal_reservation_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ProposalReservationRecord]:
        """Return the active or recoverable reservation for a capture."""

    def begin_proposal(
        self,
        reservation: ProposalReservationRecord,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> ProposalReservationRecord:
        """Atomically reserve an eligible classified intake."""

    def reclaim_proposal(
        self,
        expected: ProposalReservationRecord,
        replacement: ProposalReservationRecord,
        reclaimed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> ProposalReservationRecord:
        """Replace an expired reservation token and resume proposing."""

    def record_proposal_failure(
        self,
        capture_id: str,
        lease_token: str,
        reason: str,
        failed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Record a known reservation-stage proposal failure."""

    def complete_proposal(
        self,
        record: ProposalRecord,
        lease_token: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> ProposalRecord:
        """Atomically persist a proposal and consume its reservation."""

    def record_draft_failure(
        self,
        capture_id: str,
        proposal_id: str,
        reason: str,
        failed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Record a known failure after proposal persistence."""

    def resume_proposal_draft(
        self,
        capture_id: str,
        proposal_id: str,
        resumed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Restore a valid failed proposal to the proposed state."""

    def register_proposal_draft(
        self,
        capture_id: str,
        proposal_id: str,
        draft_note_path: str,
        registered_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> ProposalRecord:
        """Atomically register a draft and mark it awaiting approval."""

    def find_intakes_awaiting_approval(self) -> Tuple[IntakeRecord, ...]:
        """Return every intake whose draft is waiting for a human decision."""

    def record_approval(
        self,
        record: ApprovalRecord,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Atomically record one human decision and transition its intake."""

    def find_approval_by_proposal_id(
        self,
        proposal_id: str,
    ) -> Optional[ApprovalRecord]:
        """Return the recorded decision for a proposal, if one exists."""

    def record_filing(
        self,
        capture_id: str,
        proposal_id: str,
        approval_id: str,
        committed_at: str,
        *,
        audit: Optional[AuditEventRecord] = None,
    ) -> IntakeRecord:
        """Atomically commit an approved note and mark its intake filed."""

    def append_audit_event(self, record: AuditEventRecord) -> None:
        """Append one audit event that rides no transition of its own."""

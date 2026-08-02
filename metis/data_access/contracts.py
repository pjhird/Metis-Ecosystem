"""Engine-agnostic operational-state contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


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

    def find_intake_by_content_hash(
        self,
        content_hash: str,
    ) -> Optional[IntakeRecord]:
        """Return the intake row registered for a content hash, if one exists."""

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

    def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
        """Register a captured intake row or return the exact existing duplicate."""

    def begin_classification(
        self,
        capture_id: str,
        started_at: str,
    ) -> IntakeRecord:
        """Move an eligible intake into the classifying state."""

    def complete_classification(
        self,
        record: ClassificationRecord,
    ) -> ClassificationRecord:
        """Atomically persist a classification and mark its intake classified."""

    def record_classification_failure(
        self,
        capture_id: str,
        reason: str,
        failed_at: str,
    ) -> IntakeRecord:
        """Move a classifying intake into a known failed state."""

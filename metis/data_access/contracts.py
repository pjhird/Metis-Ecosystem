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

    def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
        """Register a captured intake row or return the exact existing duplicate."""

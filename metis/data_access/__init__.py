"""Operational-state interface and SQLite implementation."""

from .contracts import (
    ClassificationRecord,
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
    ProposalRecord,
    ProposalReservationRecord,
    StateStore,
    StateStoreError,
    StateTransitionRefused,
)
from .sqlite import MigrationError, SQLiteStateStore

__all__ = [
    "ClassificationRecord",
    "IntakeRecord",
    "IntakeRegistrationResult",
    "IntakeRegistrationStatus",
    "MigrationError",
    "ProposalRecord",
    "ProposalReservationRecord",
    "SQLiteStateStore",
    "StateStore",
    "StateStoreError",
    "StateTransitionRefused",
]

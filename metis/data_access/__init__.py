"""Operational-state interface and SQLite implementation."""

from .contracts import (
    ClassificationRecord,
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
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
    "SQLiteStateStore",
    "StateStore",
    "StateStoreError",
    "StateTransitionRefused",
]

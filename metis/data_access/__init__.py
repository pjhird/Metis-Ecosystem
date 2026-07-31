"""Operational-state interface and SQLite implementation."""

from .contracts import (
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
    StateStore,
    StateStoreError,
)
from .sqlite import MigrationError, SQLiteStateStore

__all__ = [
    "IntakeRecord",
    "IntakeRegistrationResult",
    "IntakeRegistrationStatus",
    "MigrationError",
    "SQLiteStateStore",
    "StateStore",
    "StateStoreError",
]

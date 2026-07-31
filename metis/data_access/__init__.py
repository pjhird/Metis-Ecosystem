"""Operational-state interface and SQLite implementation."""

from .contracts import StateStore
from .sqlite import MigrationError, SQLiteStateStore

__all__ = ["MigrationError", "SQLiteStateStore", "StateStore"]

"""Engine-agnostic operational-state contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


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

"""The orchestrator's append-only record of what it did (ADR-007, REQ-ORCH-004).

Only the orchestrating services build events. A transition carries its event
into the same transaction, so the two commit together or not at all; an action
that transitions nothing appends its event here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional

from .data_access import AuditEventRecord, StateStore
from .identifiers import new_ulid


ORCHESTRATOR = "orchestrator"

# A command's own result vocabulary, mapped onto the three audit outcomes.
# `duplicate` is a refused write, not an error, and neither is `refused`
# (schema §2.6): a blocked write is successful enforcement. `pending` is
# deliberately absent — a poll that finds an unchanged draft did nothing.
OUTCOMES = {
    "captured": "success",
    "classified": "success",
    "proposed": "success",
    "approved": "success",
    "rejected": "success",
    "filed": "success",
    "completed": "success",
    "duplicate": "refused",
    "refused": "refused",
    "failed": "failure",
}


class AuditTrail:
    def __init__(
        self,
        state_store: StateStore,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._id_factory = id_factory
        self._clock = clock

    def event(
        self,
        action: str,
        outcome: str,
        *,
        capture_id: Optional[str] = None,
        actor: str = ORCHESTRATOR,
        detail: Optional[Mapping[str, object]] = None,
    ) -> AuditEventRecord:
        """Build one event. A capture is its own trace for the whole loop."""
        capture_id = capture_id or None
        return AuditEventRecord(
            event_id=self._id_factory(),
            trace_id=capture_id or self._id_factory(),
            capture_id=capture_id,
            actor=actor,
            action=action,
            outcome=outcome,
            detail=json.dumps({} if detail is None else dict(detail), sort_keys=True),
            created_at=(
                self._clock()
                .astimezone(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            ),
        )

    def record(self, action: str, outcome: str, **event: object) -> None:
        """Append an event for an action that transitioned nothing.

        A failure to record propagates. Losing an event is a governance
        failure, not something to fold into a result field.
        """
        self._state_store.append_audit_event(self.event(action, outcome, **event))

"""Deterministic detection and recording of human approval decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Tuple

from .audit import OUTCOMES, AuditTrail
from .data_access import (
    ApprovalRecord,
    IntakeRecord,
    ProposalRecord,
    StateStore,
    StateStoreError,
    StateTransitionRefused,
)
from .draft_notes import DraftNoteError, DraftNoteStore, render_proposed_draft
from .evidence import EvidenceError, EvidenceStore
from .identifiers import is_ulid, new_ulid
from .proposal_content import ProposalContentError, ProposalContentStore


# ponytail: one local owner, no authentication anywhere in the system, and a
# human-typed approver in the vault would be self-certification, not identity
# (ADR-020 fixes the editable fields at status and links). Replace when
# authenticated or multi-user identity exists.
APPROVER = "human:owner"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"
    FAILED = "failed"


class ApprovalRunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ApprovalResult:
    status: ApprovalStatus
    capture_id: str
    proposal_id: Optional[str]
    approval_id: Optional[str]
    decision: Optional[str]
    observed_status: Optional[str]
    observed_links: Tuple[str, ...]
    approver: Optional[str]
    draft_path: Optional[str]
    intake_state: Optional[str]
    detected_at: Optional[str]
    reason: Optional[str]
    message: Optional[str]


@dataclass(frozen=True)
class ApprovalRunResult:
    status: ApprovalRunStatus
    decisions: Tuple[ApprovalResult, ...]
    reason: Optional[str]
    message: Optional[str]


class ApprovalService:
    """Read the vault's approval signal and record the decision it carries."""

    def __init__(
        self,
        state_store: StateStore,
        content_store: ProposalContentStore,
        draft_store: DraftNoteStore,
        evidence_store: EvidenceStore,
        runtime_root: Path,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._content_store = content_store
        self._draft_store = draft_store
        self._evidence_store = evidence_store
        self._runtime_root = Path(runtime_root)
        self._id_factory = id_factory
        self._clock = clock
        self._audit = AuditTrail(state_store, clock=clock)

    def review(self) -> ApprovalRunResult:
        try:
            queue = self._state_store.find_intakes_awaiting_approval()
        except StateStoreError:
            return ApprovalRunResult(
                status=ApprovalRunStatus.FAILED,
                decisions=(),
                reason="approval_state_undetermined",
                message="the approval queue could not be read",
            )
        decisions = tuple(self._review_one(intake) for intake in queue)
        failed = any(
            decision.status is ApprovalStatus.FAILED for decision in decisions
        )
        return ApprovalRunResult(
            status=(
                ApprovalRunStatus.FAILED if failed else ApprovalRunStatus.COMPLETED
            ),
            decisions=decisions,
            reason=None,
            message=None,
        )

    def _parent_goal_id(self, intake: IntakeRecord) -> Optional[str]:
        """Read the capture-time pin, the only place a project's parent lives.

        An unreadable pin returns `None`, which makes a project draft fail to
        render and be reported as inconsistent rather than approved.
        """
        try:
            evidence = self._evidence_store.validate_directory(
                self._runtime_root / intake.evidence_path
            )
        except (EvidenceError, OSError, TypeError, ValueError):
            return None
        return evidence.parent_goal_id

    def _review_one(self, intake: IntakeRecord) -> ApprovalResult:
        try:
            proposal = self._state_store.find_proposal_by_capture_id(
                intake.capture_id
            )
        except StateStoreError:
            return self._failed(
                intake,
                None,
                "approval_state_undetermined",
                "the proposal for this capture could not be read",
            )
        if not self._proposal_is_registered(intake, proposal):
            return self._failed(
                intake,
                proposal,
                "approval_proposal_inconsistent",
                "the proposal does not match its awaiting-approval intake",
            )
        body = self._validated_body(proposal)
        if body is None:
            return self._failed(
                intake,
                proposal,
                "approval_content_inconsistent",
                "the canonical proposal content does not match its proposal",
            )
        parent_goal_id = self._parent_goal_id(intake)
        try:
            draft = self._draft_store.validate(
                proposal.draft_note_path,
                render_proposed_draft(
                    proposal, body, parent_goal_id=parent_goal_id
                ),
            )
        except DraftNoteError:
            return self._failed(
                intake,
                proposal,
                "approval_draft_inconsistent",
                "the draft is missing, edited outside status and links, "
                "or carries a malformed link",
            )
        observed = draft.observed_status.value
        if observed == "proposed":
            return ApprovalResult(
                status=ApprovalStatus.PENDING,
                capture_id=intake.capture_id,
                proposal_id=proposal.proposal_id,
                approval_id=None,
                decision=None,
                observed_status=observed,
                observed_links=draft.observed_links,
                approver=None,
                draft_path=proposal.draft_note_path,
                intake_state=intake.state,
                detected_at=None,
                reason=None,
                message="the draft is still awaiting a human decision",
            )
        return self._record(intake, proposal, observed, draft.observed_links)

    def _record(
        self,
        intake: IntakeRecord,
        proposal: ProposalRecord,
        observed: str,
        links: Tuple[str, ...],
    ) -> ApprovalResult:
        try:
            approval_id = self._id_factory()
            detected_at = (
                self._clock()
                .astimezone(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._failed(
                intake,
                proposal,
                "approval_state_undetermined",
                "the decision could not be identified or timestamped",
            )
        if not is_ulid(approval_id):
            return self._failed(
                intake,
                proposal,
                "approval_state_undetermined",
                "the decision could not be identified or timestamped",
            )
        record = ApprovalRecord(
            approval_id=approval_id,
            proposal_id=proposal.proposal_id,
            decision=observed,
            approver=APPROVER,
            observed_status=observed,
            detected_at=detected_at,
            committed_at=None,
            revoked_at=None,
        )
        try:
            updated = self._state_store.record_approval(
                record,
                audit=self._audit.event(
                    "approval.detected",
                    "success",
                    capture_id=intake.capture_id,
                    actor=APPROVER,
                    detail={
                        "state": observed,
                        "decision": observed,
                        "approval_id": approval_id,
                    },
                ),
            )
        except StateTransitionRefused:
            # The transaction rolled back, so its event did not ride with it.
            # A refused transition is a refusal even where the command fails.
            self._audit.record(
                "approval.detected",
                "refused",
                capture_id=intake.capture_id,
                actor=APPROVER,
                detail={"reason": "approval_state_undetermined"},
            )
            return self._failed(
                intake,
                proposal,
                "approval_state_undetermined",
                "the decision could not be recorded",
                audited=True,
            )
        except StateStoreError:
            return self._failed(
                intake,
                proposal,
                "approval_state_undetermined",
                "the decision could not be recorded",
            )
        if updated.state != observed:
            return self._failed(
                updated,
                proposal,
                "approval_state_undetermined",
                "the decision could not be recorded",
                audited=True,
            )
        return ApprovalResult(
            status=ApprovalStatus(observed),
            capture_id=intake.capture_id,
            proposal_id=proposal.proposal_id,
            approval_id=approval_id,
            decision=observed,
            observed_status=observed,
            observed_links=links,
            approver=APPROVER,
            draft_path=proposal.draft_note_path,
            intake_state=updated.state,
            detected_at=detected_at,
            reason=None,
            message=None,
        )

    def _proposal_is_registered(
        self,
        intake: IntakeRecord,
        proposal: Optional[ProposalRecord],
    ) -> bool:
        return (
            proposal is not None
            and is_ulid(proposal.proposal_id)
            and proposal.capture_id == intake.capture_id
            and proposal.state == "pending"
            and proposal.draft_note_path
            == f"vault/notes/proposed/note.{intake.capture_id}.md"
            and proposal.body_path
            == f"proposal-content/{proposal.proposal_id}/body.md"
        )

    def _validated_body(self, proposal: ProposalRecord) -> Optional[bytes]:
        try:
            content = self._content_store.validate_directory(
                self._runtime_root / Path(proposal.body_path).parent
            )
            body = content.body_path.read_bytes()
        except (OSError, ProposalContentError, TypeError, ValueError):
            return None
        if (
            content.proposal_id != proposal.proposal_id
            or content.capture_id != proposal.capture_id
            or content.classification_id != proposal.classification_id
            or content.content_hash != proposal.content_hash
            or len(body) != content.byte_size
        ):
            return None
        return body

    def _failed(
        self,
        intake: IntakeRecord,
        proposal: Optional[ProposalRecord],
        reason: str,
        message: str,
        *,
        audited: bool = False,
    ) -> ApprovalResult:
        if not audited:
            # This outcome rode no transition, so it carries its own event.
            self._audit.record(
                "command.approvals",
                OUTCOMES[ApprovalStatus.FAILED.value],
                capture_id=intake.capture_id,
                detail={"status": ApprovalStatus.FAILED.value, "reason": reason},
            )
        return ApprovalResult(
            status=ApprovalStatus.FAILED,
            capture_id=intake.capture_id,
            proposal_id=None if proposal is None else proposal.proposal_id,
            approval_id=None,
            decision=None,
            observed_status=None,
            observed_links=(),
            approver=None,
            draft_path=None if proposal is None else proposal.draft_note_path,
            intake_state=intake.state,
            detected_at=None,
            reason=reason,
            message=message,
        )

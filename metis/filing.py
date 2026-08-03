"""Permanent filing of an approved proposal into the vault."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Tuple
from uuid import UUID

from .classification_evidence import ClassificationEvidenceStore
from .data_access import (
    ApprovalRecord,
    ClassificationRecord,
    IntakeRecord,
    ProposalRecord,
    StateStore,
    StateStoreError,
    StateTransitionRefused,
)
from .draft_notes import (
    DraftNoteError,
    DraftNoteStore,
    DraftStatus,
    render_note,
    render_proposed_draft,
)
from .evidence import EvidenceStore
from .identifiers import is_ulid
from .proposal import validated_prior_state
from .proposal_content import ProposalContentError, ProposalContentStore
from .proposal_evidence import ProposalEvidenceError, ProposalEvidenceStore

LINK_SOURCE_DIRECTORIES = ("goals", "projects")
NOTE_ID = re.compile(rb"^id: (.+)$", re.MULTILINE)


class FilingStatus(str, Enum):
    FILED = "filed"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class FilingResult:
    status: FilingStatus
    capture_id: str
    proposal_id: Optional[str]
    approval_id: Optional[str]
    filed_path: Optional[str]
    links: Tuple[str, ...]
    committed_at: Optional[str]
    intake_state: Optional[str]
    reason: Optional[str]
    message: Optional[str]


class FilingService:
    """Turn one recorded approval into one permanent, linked, typed note."""

    def __init__(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        classification_store: ClassificationEvidenceStore,
        proposal_evidence_store: ProposalEvidenceStore,
        content_store: ProposalContentStore,
        draft_store: DraftNoteStore,
        filed_store: DraftNoteStore,
        runtime_root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._evidence_store = evidence_store
        self._classification_store = classification_store
        self._proposal_evidence_store = proposal_evidence_store
        self._content_store = content_store
        self._draft_store = draft_store
        self._filed_store = filed_store
        self._runtime_root = Path(runtime_root)
        self._clock = clock

    def file(self, capture_id: str) -> FilingResult:
        if not _is_capture_id(capture_id):
            return self._failed(
                capture_id,
                "filing.capture_id_invalid",
                "the capture ID is not a canonical UUID4",
            )
        try:
            intake = self._state_store.find_intake_by_capture_id(capture_id)
            classification = self._state_store.find_classification_by_capture_id(
                capture_id
            )
            proposal = self._state_store.find_proposal_by_capture_id(capture_id)
        except StateStoreError:
            return self._failed(
                capture_id,
                "filing.state_undetermined",
                "the state of this capture could not be read",
            )
        if intake is None:
            return self._failed(
                capture_id,
                "filing.capture_unknown",
                "no capture is registered under this ID",
            )
        if intake.state not in ("approved", "filed"):
            return FilingResult(
                status=FilingStatus.REFUSED,
                capture_id=capture_id,
                proposal_id=None if proposal is None else proposal.proposal_id,
                approval_id=None,
                filed_path=None,
                links=(),
                committed_at=None,
                intake_state=intake.state,
                reason="filing.not_approved",
                message=(
                    "filing requires a recorded human approval; this capture is "
                    f"{intake.state}"
                ),
            )
        prepared = self._prepared(intake, classification, proposal)
        if isinstance(prepared, FilingResult):
            return prepared
        approval, filed_bytes, links = prepared
        filed_path = f"vault/notes/filed/note.{capture_id}.md"
        if intake.state == "filed":
            return self._replay(
                intake,
                proposal,
                approval,
                filed_path,
                filed_bytes,
                links,
            )
        return self._commit(
            intake,
            proposal,
            approval,
            filed_path,
            filed_bytes,
            links,
        )

    def _prepared(
        self,
        intake: IntakeRecord,
        classification: Optional[ClassificationRecord],
        proposal: Optional[ProposalRecord],
    ):
        """Revalidate everything the approval rests on. Writes nothing."""
        if classification is None or not self._proposal_is_consistent(
            intake,
            classification,
            proposal,
        ):
            return self._failed(
                intake.capture_id,
                "filing.state_inconsistent",
                "the proposal does not match its approved capture",
                intake_state=intake.state,
            )
        try:
            approval = self._state_store.find_approval_by_proposal_id(
                proposal.proposal_id
            )
        except StateStoreError:
            return self._failed(
                intake.capture_id,
                "filing.state_undetermined",
                "the approval for this proposal could not be read",
                intake_state=intake.state,
            )
        if not self._approval_authorizes_filing(approval, proposal, intake):
            return self._failed(
                intake.capture_id,
                "filing.approval_missing",
                "no uncommitted human approval authorizes this write",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        if (
            validated_prior_state(
                self._evidence_store,
                self._classification_store,
                self._runtime_root,
                intake,
                classification,
            )
            is None
            or not self._proposal_evidence_is_valid(proposal)
        ):
            return self._failed(
                intake.capture_id,
                "filing.evidence_chain_broken",
                "the evidence behind this approval no longer validates",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        body = self._validated_body(proposal)
        if body is None:
            return self._failed(
                intake.capture_id,
                "filing.content_inconsistent",
                "the canonical proposal content does not match its proposal",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        try:
            draft = self._draft_store.validate(
                proposal.draft_note_path,
                render_proposed_draft(proposal, body),
            )
        except DraftNoteError:
            return self._failed(
                intake.capture_id,
                "filing.draft_inconsistent",
                "the draft was edited outside status and links after approval",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        if draft.observed_status is not DraftStatus.APPROVED:
            return self._failed(
                intake.capture_id,
                "filing.draft_not_approved",
                "the draft no longer reads status: approved",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        links = draft.observed_links
        if not links:
            return self._failed(
                intake.capture_id,
                "filing.links_absent",
                "add a link to an existing goal or project note, then run this again",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        unresolved = self._unresolved(links)
        if unresolved:
            return self._failed(
                intake.capture_id,
                "filing.link_unresolvable",
                "no goal or project note carries the ID "
                + ", ".join(unresolved),
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        try:
            filed_bytes = render_note(
                proposal,
                body,
                status=DraftStatus.APPROVED,
                links=links,
                approved=approval.detected_at,
            )
        except DraftNoteError:
            return self._failed(
                intake.capture_id,
                "filing.render_failed",
                "the permanent note could not be rendered",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        return approval, filed_bytes, links

    def _commit(
        self,
        intake: IntakeRecord,
        proposal: ProposalRecord,
        approval: ApprovalRecord,
        filed_path: str,
        filed_bytes: bytes,
        links: Tuple[str, ...],
    ) -> FilingResult:
        try:
            self._filed_store.create(filed_path, filed_bytes)
        except DraftNoteError:
            # A crash between the write and the transition leaves the note on
            # disk with the intake still approved. Resume only on the identical
            # file; anything else is undetermined and repairs nothing.
            if self._on_disk(filed_path) != filed_bytes:
                return self._failed(
                    intake.capture_id,
                    "filing.note_write_failed",
                    "the permanent note could not be written, or an unexpected "
                    "file already occupies its path",
                    proposal_id=proposal.proposal_id,
                    intake_state=intake.state,
                )
        try:
            committed_at = _utc_second(self._clock())
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._failed(
                intake.capture_id,
                "filing.state_undetermined",
                "the filing could not be timestamped",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        try:
            updated = self._state_store.record_filing(
                intake.capture_id,
                proposal.proposal_id,
                approval.approval_id,
                committed_at,
            )
        except (StateStoreError, StateTransitionRefused):
            return self._failed(
                intake.capture_id,
                "filing.state_undetermined",
                "the permanent note is written but its transition was not "
                "recorded; re-run to complete it",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        return FilingResult(
            status=FilingStatus.FILED,
            capture_id=intake.capture_id,
            proposal_id=proposal.proposal_id,
            approval_id=approval.approval_id,
            filed_path=filed_path,
            links=links,
            committed_at=committed_at,
            intake_state=updated.state,
            reason=None,
            message=None,
        )

    def _replay(
        self,
        intake: IntakeRecord,
        proposal: ProposalRecord,
        approval: ApprovalRecord,
        filed_path: str,
        filed_bytes: bytes,
        links: Tuple[str, ...],
    ) -> FilingResult:
        """`duplicate` is a success claim, so the note is verified, not assumed."""
        if self._on_disk(filed_path) != filed_bytes:
            return self._failed(
                intake.capture_id,
                "filing.filed_note_missing",
                "the intake is filed but its permanent note is missing or altered",
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        return FilingResult(
            status=FilingStatus.DUPLICATE,
            capture_id=intake.capture_id,
            proposal_id=proposal.proposal_id,
            approval_id=approval.approval_id,
            filed_path=filed_path,
            links=links,
            committed_at=approval.committed_at,
            intake_state=intake.state,
            reason=None,
            message="this capture is already filed; no second note was written",
        )

    def _unresolved(self, links: Tuple[str, ...]) -> Tuple[str, ...]:
        """Resolve each link against an existing note's `id` (ADR-020)."""
        resolvable = set()
        for directory in LINK_SOURCE_DIRECTORIES:
            root = self._runtime_root / "vault" / directory
            try:
                candidates = sorted(root.glob("*.md"))
            except OSError:
                continue
            for candidate in candidates:
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    head = candidate.read_bytes().split(b"---\n\n", 1)[0]
                except OSError:
                    continue
                match = NOTE_ID.search(head)
                if match is not None:
                    resolvable.add(match.group(1).strip(b'"').decode("utf-8", "replace"))
        return tuple(link for link in links if link not in resolvable)

    def _on_disk(self, filed_path: str) -> Optional[bytes]:
        path = self._runtime_root / Path(filed_path)
        try:
            if path.is_symlink() or not path.is_file():
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _proposal_is_consistent(
        self,
        intake: IntakeRecord,
        classification: ClassificationRecord,
        proposal: Optional[ProposalRecord],
    ) -> bool:
        return (
            proposal is not None
            and is_ulid(proposal.proposal_id)
            and proposal.capture_id == intake.capture_id
            and proposal.classification_id == classification.classification_id
            and proposal.state == "approved"
            and proposal.draft_note_path
            == f"vault/notes/proposed/note.{intake.capture_id}.md"
            and proposal.body_path
            == f"proposal-content/{proposal.proposal_id}/body.md"
            and proposal.raw_response_path
            == f"proposal-evidence/{proposal.proposal_id}/raw-response.txt"
        )

    def _approval_authorizes_filing(
        self,
        approval: Optional[ApprovalRecord],
        proposal: ProposalRecord,
        intake: IntakeRecord,
    ) -> bool:
        if (
            approval is None
            or not is_ulid(approval.approval_id)
            or approval.proposal_id != proposal.proposal_id
            or approval.decision != "approved"
            or approval.observed_status != "approved"
            or not approval.approver.startswith("human:")
            or approval.revoked_at is not None
        ):
            return False
        # Committing twice is what `duplicate` exists to prevent, so a filed
        # intake is the only state in which a committed approval is legitimate.
        return (approval.committed_at is None) == (intake.state == "approved")

    def _proposal_evidence_is_valid(self, proposal: ProposalRecord) -> bool:
        try:
            evidence = self._proposal_evidence_store.validate_directory(
                self._runtime_root / Path(proposal.raw_response_path).parent
            )
        except (OSError, ProposalEvidenceError, TypeError, ValueError):
            return False
        return (
            evidence.proposal_id == proposal.proposal_id
            and evidence.capture_id == proposal.capture_id
            and evidence.classification_id == proposal.classification_id
            and evidence.model_id == proposal.model_id
            and evidence.prompt_version == proposal.prompt_version
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
        capture_id: str,
        reason: str,
        message: str,
        *,
        proposal_id: Optional[str] = None,
        intake_state: Optional[str] = None,
    ) -> FilingResult:
        return FilingResult(
            status=FilingStatus.FAILED,
            capture_id=capture_id,
            proposal_id=proposal_id,
            approval_id=None,
            filed_path=None,
            links=(),
            committed_at=None,
            intake_state=intake_state,
            reason=reason,
            message=message,
        )


def _is_capture_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _utc_second(moment: datetime) -> str:
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

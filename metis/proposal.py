"""Deterministic orchestration for proposal creation and draft registration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID, uuid4

from .classification import ROUTING, parse_classification_response
from .classification_evidence import (
    ClassificationEvidenceError,
    ClassificationEvidenceStore,
)
from .data_access import (
    ClassificationRecord,
    ProposalRecord,
    ProposalReservationRecord,
    StateStore,
    StateStoreError,
    StateTransitionRefused,
)
from .draft_notes import (
    DraftNoteCollision,
    DraftNoteError,
    DraftNoteStore,
    render_proposed_draft,
)
from .evidence import EvidenceError, EvidenceStore
from .identifiers import is_ulid, new_ulid
from .model_adapters import (
    ModelAdapter,
    ModelAdapterError,
    ModelConfigurationError,
    ModelRequestError,
    ModelResponseRefused,
    ModelResponseTruncated,
    UnsupportedModelResponse,
)
from .prompts import PROPOSAL_PROMPT_VERSION, load_proposal_prompt
from .proposal_content import ProposalContentCollision
from .proposal_content import ProposalContentError as ProposalContentStoreError
from .proposal_content import ProposalContentStore
from .proposal_contract import (
    ProposalContentError,
    ProposalContentPolicyRefusal,
    parse_proposal_response,
    render_proposal_body,
    risk_for_sensitivity,
)
from .proposal_evidence import (
    ProposalEvidenceCollision,
    ProposalEvidenceError,
    ProposalEvidenceStore,
)


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    DUPLICATE = "duplicate"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class ProposalResult:
    status: ProposalStatus
    capture_id: str
    classification_id: Optional[str]
    proposal_id: Optional[str]
    note_type: Optional[str]
    title: Optional[str]
    confidence: Optional[float]
    sensitivity: Optional[str]
    risk_level: Optional[str]
    raw_response_path: Optional[str]
    content_path: Optional[str]
    draft_path: Optional[str]
    intake_state: Optional[str]
    reason: Optional[str]
    message: Optional[str]


class ProposalService:
    def __init__(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore,
        classification_store: ClassificationEvidenceStore,
        proposal_evidence_store: ProposalEvidenceStore,
        content_store: ProposalContentStore,
        draft_store: DraftNoteStore,
        model_adapter: ModelAdapter,
        runtime_root: Path,
        *,
        id_factory: Callable[[], str] = new_ulid,
        lease_token_factory: Callable[[], str] = lambda: str(uuid4()),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._state_store = state_store
        self._evidence_store = evidence_store
        self._classification_store = classification_store
        self._proposal_evidence_store = proposal_evidence_store
        self._content_store = content_store
        self._draft_store = draft_store
        self._model_adapter = model_adapter
        self._runtime_root = Path(runtime_root)
        self._id_factory = id_factory
        self._lease_token_factory = lease_token_factory
        self._clock = clock

    def propose(self, capture_id: str) -> ProposalResult:
        if not self._is_uuid4(capture_id):
            return self._consistency_failure(capture_id)
        try:
            intake = self._state_store.find_intake_by_capture_id(capture_id)
            classification = self._state_store.find_classification_by_capture_id(
                capture_id
            )
            proposal = self._state_store.find_proposal_by_capture_id(capture_id)
            reservation = (
                self._state_store.find_proposal_reservation_by_capture_id(capture_id)
            )
        except StateStoreError:
            return self._state_undetermined(capture_id)
        if intake is not None and classification is not None:
            evidence = self._validated_prior_state(intake, classification)
            if evidence is None:
                return self._consistency_failure(
                    capture_id,
                    classification=classification,
                    intake_state=intake.state,
                )
            if intake.state == "awaiting_approval":
                return self._replay(
                    intake,
                    classification,
                    proposal,
                    reservation,
                )
            if proposal is not None and reservation is None and (
                intake.state == "proposed"
                or (
                    intake.state == "failed"
                    and intake.failure_reason is not None
                    and intake.failure_reason.startswith("proposal.")
                )
            ):
                return self._resume_draft(
                    intake,
                    classification,
                    proposal,
                )
            if proposal is None and reservation is not None and (
                intake.state == "proposing"
                or (
                    intake.state == "failed"
                    and intake.failure_reason is not None
                    and intake.failure_reason.startswith("proposal.")
                )
            ):
                return self._recover_reservation(
                    intake,
                    classification,
                    reservation,
                    evidence,
                )
        if (
            intake is None
            or classification is None
            or intake.state != "classified"
            or proposal is not None
            or reservation is not None
        ):
            return self._consistency_failure(
                capture_id,
                classification=classification,
                intake_state=None if intake is None else intake.state,
            )
        if intake.failure_reason is not None or (
            intake.state_updated_at != classification.created_at
        ):
            return self._consistency_failure(
                capture_id,
                classification=classification,
                intake_state=intake.state,
            )

        try:
            proposal_id = self._id_factory()
            lease_token = self._lease_token_factory()
        except Exception:
            return self._consistency_failure(
                capture_id,
                classification=classification,
                intake_state=intake.state,
            )
        if not is_ulid(proposal_id) or not self._is_uuid4(lease_token):
            return self._consistency_failure(
                capture_id,
                classification=classification,
                intake_state=intake.state,
            )
        try:
            now = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._consistency_failure(
                capture_id,
                classification=classification,
                intake_state=intake.state,
            )
        reserved_at = self._timestamp(now)
        reservation = ProposalReservationRecord(
            proposal_id=proposal_id,
            capture_id=capture_id,
            classification_id=classification.classification_id,
            lease_token=lease_token,
            reserved_at=reserved_at,
            lease_expires_at=self._timestamp(now + timedelta(minutes=15)),
        )
        try:
            self._state_store.begin_proposal(reservation)
        except StateTransitionRefused:
            return self._consistency_failure(
                capture_id,
                classification=classification,
                proposal_id=proposal_id,
                intake_state=intake.state,
            )
        except StateStoreError:
            return self._state_undetermined(
                capture_id,
                classification=classification,
                proposal_id=proposal_id,
                intake_state=intake.state,
            )

        try:
            captured_text = evidence.raw_path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError):
            return self._fail_reserved(
                capture_id,
                classification,
                proposal_id,
                lease_token,
                "proposal_consistency_failed",
                intake_state="proposing",
            )
        classification_json = json.dumps(
            {
                "candidate_type": classification.candidate_type,
                "sensitivity": classification.sensitivity,
                "confidence": classification.confidence,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt = (
            load_proposal_prompt()
            .replace(
                "{{CAPTURE_JSON}}",
                json.dumps(captured_text, ensure_ascii=False),
            )
            .replace("{{CLASSIFICATION_JSON}}", classification_json)
        )
        try:
            model_response = self._model_adapter.propose(prompt)
        except ModelConfigurationError as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_configuration_failed",
            )
        except ModelRequestError as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_request_failed",
            )
        except ModelResponseRefused as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_response_refused",
            )
        except ModelResponseTruncated as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_response_truncated",
            )
        except UnsupportedModelResponse as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_response_invalid",
            )
        except ModelAdapterError as error:
            return self._fail_from_model_error(
                capture_id, classification, reservation, error,
                "model_request_failed",
            )

        try:
            raw_text = model_response.raw_text
            model_id = model_response.model_id
        except AttributeError:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "model_response_invalid", intake_state="proposing",
            )
        response = self._preserve_response(
            reservation,
            raw_text,
            model_id,
        )
        if response is None:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_evidence_failed", intake_state="proposing",
            )
        raw_response_path = str(Path(response.evidence_path) / "raw-response.txt")
        try:
            semantic = parse_proposal_response(
                response.raw_path.read_text(encoding="utf-8")
            )
        except ProposalContentPolicyRefusal:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_content_failed",
                status=ProposalStatus.REFUSED,
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        except (OSError, UnicodeError, ProposalContentError):
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "model_response_invalid",
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        body_bytes = render_proposal_body(semantic)
        content, matches = self._preserve_content(
            reservation,
            response.raw_response_hash,
            body_bytes,
        )
        if content is None:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_content_failed",
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        if not matches:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_consistency_failed",
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        try:
            current_reservation = (
                self._state_store.find_proposal_reservation_by_capture_id(capture_id)
            )
        except StateStoreError:
            return self._state_undetermined(
                capture_id, classification=classification,
                proposal_id=proposal_id,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        if current_reservation != reservation:
            return self._state_undetermined(
                capture_id, classification=classification,
                proposal_id=proposal_id,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )

        evidence_refs = json.dumps(
            [
                f"evidence/{capture_id}/raw.txt",
                (
                    "classification-evidence/"
                    f"{classification.classification_id}/raw-response.txt"
                ),
                raw_response_path,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record = ProposalRecord(
            proposal_id=proposal_id,
            capture_id=capture_id,
            classification_id=classification.classification_id,
            note_type=classification.candidate_type,
            title=semantic.title,
            body_path=content.content_path,
            proposed_links="[]",
            evidence_refs=evidence_refs,
            confidence=classification.confidence,
            sensitivity=classification.sensitivity,
            risk_level=risk_for_sensitivity(classification.sensitivity),
            reason=semantic.reason,
            uncertainties_json=json.dumps(
                list(semantic.uncertainties),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            model_id=response.model_id,
            prompt_version=PROPOSAL_PROMPT_VERSION,
            raw_response_path=raw_response_path,
            content_hash=content.content_hash,
            draft_note_path=None,
            state="pending",
            created_at=reserved_at,
        )
        try:
            self._state_store.complete_proposal(record, lease_token)
        except (StateStoreError, StateTransitionRefused):
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_persistence_failed",
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        draft_path = f"vault/notes/proposed/note.{capture_id}.md"
        expected_draft = render_proposed_draft(record, body_bytes)
        try:
            draft = self._draft_store.create(draft_path, expected_draft)
            self._draft_store.validate(draft.draft_path, expected_draft)
        except DraftNoteCollision:
            return self._fail_draft(
                classification,
                record,
                "draft_collision",
                raw_response_path,
                content.content_path,
            )
        except DraftNoteError:
            return self._fail_draft(
                classification,
                record,
                "draft_write_failed",
                raw_response_path,
                content.content_path,
            )
        try:
            registered = self._state_store.register_proposal_draft(
                capture_id,
                proposal_id,
                draft.draft_path,
                reserved_at,
            )
        except (StateStoreError, StateTransitionRefused):
            return self._fail_draft(
                classification,
                record,
                "proposal_persistence_failed",
                raw_response_path,
                content.content_path,
                draft_path=draft.draft_path,
            )
        try:
            final_intake = self._state_store.find_intake_by_capture_id(capture_id)
            final_proposal = self._state_store.find_proposal_by_capture_id(capture_id)
        except StateStoreError:
            return self._state_undetermined(
                capture_id, classification=classification,
                proposal_id=proposal_id, title=semantic.title,
                risk_level=record.risk_level,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                draft_path=draft.draft_path,
            )
        if (
            final_intake is None
            or final_intake.state != "awaiting_approval"
            or final_proposal != registered
            or registered.draft_note_path != draft.draft_path
        ):
            return self._state_undetermined(
                capture_id, classification=classification,
                proposal_id=proposal_id, title=semantic.title,
                risk_level=record.risk_level,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                draft_path=draft.draft_path,
                intake_state=None if final_intake is None else final_intake.state,
            )
        try:
            self._proposal_evidence_store.validate_directory(response.directory)
            self._content_store.validate_directory(content.directory)
            self._draft_store.validate(draft.draft_path, expected_draft)
        except (ProposalEvidenceError, ProposalContentStoreError, DraftNoteError):
            return self._state_undetermined(
                capture_id, classification=classification,
                proposal_id=proposal_id, title=semantic.title,
                risk_level=record.risk_level,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                draft_path=draft.draft_path,
                intake_state=final_intake.state,
            )
        return self._result(
            ProposalStatus.PROPOSED,
            capture_id,
            classification=classification,
            proposal_id=proposal_id,
            title=semantic.title,
            risk_level=record.risk_level,
            raw_response_path=raw_response_path,
            content_path=content.content_path,
            draft_path=draft.draft_path,
            intake_state=final_intake.state,
        )

    def _replay(
        self,
        intake,
        classification: ClassificationRecord,
        proposal: Optional[ProposalRecord],
        reservation: Optional[ProposalReservationRecord],
    ) -> ProposalResult:
        if (
            intake.failure_reason is not None
            or proposal is None
            or reservation is not None
            or proposal.draft_note_path
            != f"vault/notes/proposed/note.{intake.capture_id}.md"
        ):
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=None if proposal is None else proposal.proposal_id,
                intake_state=intake.state,
            )
        validated = self._validated_existing_proposal(classification, proposal)
        if validated is None:
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        response, content, expected_draft = validated
        try:
            draft = self._draft_store.validate(
                proposal.draft_note_path,
                expected_draft,
            )
        except DraftNoteError:
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        if draft.observed_status.value != "proposed":
            return self._result(
                ProposalStatus.REFUSED,
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                title=proposal.title,
                risk_level=proposal.risk_level,
                raw_response_path=proposal.raw_response_path,
                content_path=proposal.body_path,
                draft_path=proposal.draft_note_path,
                intake_state=intake.state,
                reason="proposal_consistency_failed",
                message="draft status belongs to the approval step",
            )
        return self._result(
            ProposalStatus.DUPLICATE,
            intake.capture_id,
            classification=classification,
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            risk_level=proposal.risk_level,
            raw_response_path=str(
                Path(response.evidence_path) / "raw-response.txt"
            ),
            content_path=content.content_path,
            draft_path=draft.draft_path,
            intake_state=intake.state,
        )

    def _resume_draft(
        self,
        intake,
        classification: ClassificationRecord,
        proposal: ProposalRecord,
    ) -> ProposalResult:
        if proposal.draft_note_path is not None:
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        validated = self._validated_existing_proposal(classification, proposal)
        if validated is None:
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        response, content, expected_draft = validated
        try:
            now = self._now_timestamp()
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                intake_state=intake.state,
            )
        if intake.state == "failed":
            try:
                intake = self._state_store.resume_proposal_draft(
                    intake.capture_id,
                    proposal.proposal_id,
                    now,
                )
            except (StateStoreError, StateTransitionRefused):
                return self._state_undetermined(
                    intake.capture_id,
                    classification=classification,
                    proposal_id=proposal.proposal_id,
                    intake_state=intake.state,
                )
        draft_path = f"vault/notes/proposed/note.{intake.capture_id}.md"
        absolute_draft = self._runtime_root / draft_path
        if os.path.lexists(absolute_draft):
            try:
                draft = self._draft_store.validate(draft_path, expected_draft)
            except DraftNoteError:
                return self._fail_draft(
                    classification,
                    proposal,
                    "draft_collision",
                    proposal.raw_response_path,
                    proposal.body_path,
                )
            if draft.observed_status.value != "proposed":
                return self._result(
                    ProposalStatus.REFUSED,
                    intake.capture_id,
                    classification=classification,
                    proposal_id=proposal.proposal_id,
                    title=proposal.title,
                    risk_level=proposal.risk_level,
                    raw_response_path=proposal.raw_response_path,
                    content_path=proposal.body_path,
                    draft_path=draft_path,
                    intake_state=intake.state,
                    reason="proposal_consistency_failed",
                    message="draft status belongs to the approval step",
                )
        else:
            try:
                draft = self._draft_store.create(draft_path, expected_draft)
                self._draft_store.validate(draft.draft_path, expected_draft)
            except DraftNoteCollision:
                return self._fail_draft(
                    classification,
                    proposal,
                    "draft_collision",
                    proposal.raw_response_path,
                    proposal.body_path,
                )
            except DraftNoteError:
                return self._fail_draft(
                    classification,
                    proposal,
                    "draft_write_failed",
                    proposal.raw_response_path,
                    proposal.body_path,
                )
        try:
            registered = self._state_store.register_proposal_draft(
                intake.capture_id,
                proposal.proposal_id,
                draft.draft_path,
                now,
            )
            final_intake = self._state_store.find_intake_by_capture_id(
                intake.capture_id
            )
        except (StateStoreError, StateTransitionRefused):
            return self._fail_draft(
                classification,
                proposal,
                "proposal_persistence_failed",
                proposal.raw_response_path,
                proposal.body_path,
                draft_path=draft.draft_path,
            )
        if (
            final_intake is None
            or final_intake.state != "awaiting_approval"
            or registered.draft_note_path != draft.draft_path
        ):
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                title=proposal.title,
                risk_level=proposal.risk_level,
                raw_response_path=proposal.raw_response_path,
                content_path=proposal.body_path,
                draft_path=draft.draft_path,
                intake_state=None if final_intake is None else final_intake.state,
            )
        try:
            self._proposal_evidence_store.validate_directory(response.directory)
            self._content_store.validate_directory(content.directory)
            self._draft_store.validate(draft.draft_path, expected_draft)
        except (ProposalEvidenceError, ProposalContentStoreError, DraftNoteError):
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                title=proposal.title,
                risk_level=proposal.risk_level,
                raw_response_path=proposal.raw_response_path,
                content_path=proposal.body_path,
                draft_path=draft.draft_path,
                intake_state=final_intake.state,
            )
        return self._result(
            ProposalStatus.PROPOSED,
            intake.capture_id,
            classification=classification,
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            risk_level=proposal.risk_level,
            raw_response_path=proposal.raw_response_path,
            content_path=proposal.body_path,
            draft_path=draft.draft_path,
            intake_state=final_intake.state,
        )

    def _recover_reservation(
        self,
        intake,
        classification: ClassificationRecord,
        reservation: ProposalReservationRecord,
        evidence,
    ) -> ProposalResult:
        if (
            not is_ulid(reservation.proposal_id)
            or reservation.capture_id != intake.capture_id
            or reservation.classification_id != classification.classification_id
            or not self._is_uuid4(reservation.lease_token)
        ):
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )
        try:
            now = self._clock().astimezone(timezone.utc).replace(microsecond=0)
            self._parse_timestamp(reservation.reserved_at)
            expires = self._parse_timestamp(reservation.lease_expires_at)
        except (AttributeError, OverflowError, TypeError, ValueError):
            return self._consistency_failure(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )
        if expires > now:
            return self._result(
                ProposalStatus.REFUSED,
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                risk_level=risk_for_sensitivity(classification.sensitivity),
                intake_state=intake.state,
                reason="proposal_in_progress",
                message="proposal generation is already in progress",
            )
        try:
            lease_token = self._lease_token_factory()
        except Exception:
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )
        if not self._is_uuid4(lease_token) or lease_token == reservation.lease_token:
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )
        replacement = ProposalReservationRecord(
            proposal_id=reservation.proposal_id,
            capture_id=reservation.capture_id,
            classification_id=reservation.classification_id,
            lease_token=lease_token,
            reserved_at=self._timestamp(now),
            lease_expires_at=self._timestamp(now + timedelta(minutes=15)),
        )
        try:
            reservation = self._state_store.reclaim_proposal(
                reservation,
                replacement,
                self._timestamp(now),
            )
        except StateTransitionRefused:
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )
        except StateStoreError:
            return self._state_undetermined(
                intake.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                intake_state=intake.state,
            )

        response_directory = (
            self._runtime_root / "proposal-evidence" / reservation.proposal_id
        )
        content_directory = (
            self._runtime_root / "proposal-content" / reservation.proposal_id
        )
        if not os.path.lexists(response_directory) and os.path.lexists(
            content_directory
        ):
            return self._fail_reserved(
                intake.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "proposal_consistency_failed",
                intake_state="proposing",
            )
        if os.path.lexists(response_directory):
            try:
                response = self._proposal_evidence_store.validate_directory(
                    response_directory
                )
            except ProposalEvidenceError:
                return self._fail_reserved(
                    intake.capture_id,
                    classification,
                    reservation.proposal_id,
                    reservation.lease_token,
                    "proposal_evidence_failed",
                    intake_state="proposing",
                )
            if (
                response.proposal_id != reservation.proposal_id
                or response.capture_id != reservation.capture_id
                or response.classification_id != reservation.classification_id
                or response.prompt_version != PROPOSAL_PROMPT_VERSION
            ):
                return self._fail_reserved(
                    intake.capture_id,
                    classification,
                    reservation.proposal_id,
                    reservation.lease_token,
                    "proposal_consistency_failed",
                    intake_state="proposing",
                )
        else:
            try:
                prompt = self._proposal_prompt(evidence, classification)
                model_response = self._model_adapter.propose(prompt)
            except (OSError, UnicodeError):
                return self._fail_reserved(
                    intake.capture_id,
                    classification,
                    reservation.proposal_id,
                    reservation.lease_token,
                    "proposal_consistency_failed",
                    intake_state="proposing",
                )
            except ModelConfigurationError as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_configuration_failed",
                )
            except ModelRequestError as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_request_failed",
                )
            except ModelResponseRefused as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_response_refused",
                )
            except ModelResponseTruncated as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_response_truncated",
                )
            except UnsupportedModelResponse as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_response_invalid",
                )
            except ModelAdapterError as error:
                return self._fail_from_model_error(
                    intake.capture_id, classification, reservation, error,
                    "model_request_failed",
                )
            try:
                response = self._preserve_response(
                    reservation,
                    model_response.raw_text,
                    model_response.model_id,
                )
            except AttributeError:
                response = None
            if response is None:
                return self._fail_reserved(
                    intake.capture_id,
                    classification,
                    reservation.proposal_id,
                    reservation.lease_token,
                    "proposal_evidence_failed",
                    intake_state="proposing",
                )
        return self._complete_recovered_response(
            classification,
            reservation,
            response,
        )

    def _complete_recovered_response(
        self,
        classification: ClassificationRecord,
        reservation: ProposalReservationRecord,
        response,
    ) -> ProposalResult:
        raw_response_path = str(Path(response.evidence_path) / "raw-response.txt")
        try:
            semantic = parse_proposal_response(
                response.raw_path.read_text(encoding="utf-8")
            )
        except ProposalContentPolicyRefusal:
            return self._fail_reserved(
                reservation.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "proposal_content_failed",
                status=ProposalStatus.REFUSED,
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        except (OSError, UnicodeError, ProposalContentError):
            return self._fail_reserved(
                reservation.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "model_response_invalid",
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        body_bytes = render_proposal_body(semantic)
        content, matches = self._preserve_content(
            reservation,
            response.raw_response_hash,
            body_bytes,
        )
        if content is None:
            return self._fail_reserved(
                reservation.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "proposal_content_failed",
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        if not matches:
            return self._fail_reserved(
                reservation.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "proposal_consistency_failed",
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        try:
            current = self._state_store.find_proposal_reservation_by_capture_id(
                reservation.capture_id
            )
        except StateStoreError:
            return self._state_undetermined(
                reservation.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        if current != reservation:
            return self._state_undetermined(
                reservation.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        record = self._proposal_record(
            classification,
            reservation.proposal_id,
            semantic,
            response,
            content,
        )
        try:
            self._state_store.complete_proposal(record, reservation.lease_token)
            intake = self._state_store.find_intake_by_capture_id(
                reservation.capture_id
            )
        except (StateStoreError, StateTransitionRefused):
            return self._fail_reserved(
                reservation.capture_id,
                classification,
                reservation.proposal_id,
                reservation.lease_token,
                "proposal_persistence_failed",
                raw_response_path=raw_response_path,
                content_path=content.content_path,
                intake_state="proposing",
            )
        if intake is None or intake.state != "proposed":
            return self._state_undetermined(
                reservation.capture_id,
                classification=classification,
                proposal_id=reservation.proposal_id,
                raw_response_path=raw_response_path,
                content_path=content.content_path,
            )
        return self._resume_draft(intake, classification, record)

    def _validated_existing_proposal(
        self,
        classification: ClassificationRecord,
        proposal: ProposalRecord,
    ):
        expected_raw_path = (
            f"proposal-evidence/{proposal.proposal_id}/raw-response.txt"
        )
        expected_content_path = f"proposal-content/{proposal.proposal_id}/body.md"
        expected_refs = json.dumps(
            [
                f"evidence/{proposal.capture_id}/raw.txt",
                (
                    "classification-evidence/"
                    f"{proposal.classification_id}/raw-response.txt"
                ),
                expected_raw_path,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if (
            not is_ulid(proposal.proposal_id)
            or proposal.capture_id != classification.capture_id
            or proposal.classification_id != classification.classification_id
            or proposal.note_type != classification.candidate_type
            or proposal.confidence != classification.confidence
            or proposal.sensitivity != classification.sensitivity
            or proposal.risk_level
            != risk_for_sensitivity(classification.sensitivity)
            or proposal.proposed_links != "[]"
            or proposal.evidence_refs != expected_refs
            or proposal.prompt_version != PROPOSAL_PROMPT_VERSION
            or proposal.raw_response_path != expected_raw_path
            or proposal.body_path != expected_content_path
            or proposal.state != "pending"
        ):
            return None
        try:
            self._parse_timestamp(proposal.created_at)
            response = self._proposal_evidence_store.validate_directory(
                self._runtime_root / Path(proposal.raw_response_path).parent
            )
            content = self._content_store.validate_directory(
                self._runtime_root / Path(proposal.body_path).parent
            )
            semantic = parse_proposal_response(
                response.raw_path.read_text(encoding="utf-8")
            )
            body_bytes = render_proposal_body(semantic)
            observed_body = content.body_path.read_bytes()
        except (
            OSError,
            ProposalContentError,
            ProposalContentStoreError,
            ProposalEvidenceError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            return None
        if (
            response.proposal_id != proposal.proposal_id
            or response.capture_id != proposal.capture_id
            or response.classification_id != proposal.classification_id
            or response.model_id != proposal.model_id
            or response.prompt_version != proposal.prompt_version
            or response.received_at != proposal.created_at
            or content.proposal_id != proposal.proposal_id
            or content.capture_id != proposal.capture_id
            or content.classification_id != proposal.classification_id
            or content.raw_response_hash != response.raw_response_hash
            or content.content_hash != proposal.content_hash
            or observed_body != body_bytes
            or proposal.title != semantic.title
            or proposal.reason != semantic.reason
            or proposal.uncertainties_json
            != json.dumps(
                list(semantic.uncertainties),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ):
            return None
        try:
            expected_draft = render_proposed_draft(proposal, body_bytes)
        except DraftNoteError:
            return None
        return response, content, expected_draft

    def _proposal_record(
        self,
        classification: ClassificationRecord,
        proposal_id: str,
        semantic,
        response,
        content,
    ) -> ProposalRecord:
        raw_response_path = str(Path(response.evidence_path) / "raw-response.txt")
        return ProposalRecord(
            proposal_id=proposal_id,
            capture_id=classification.capture_id,
            classification_id=classification.classification_id,
            note_type=classification.candidate_type,
            title=semantic.title,
            body_path=content.content_path,
            proposed_links="[]",
            evidence_refs=json.dumps(
                [
                    f"evidence/{classification.capture_id}/raw.txt",
                    (
                        "classification-evidence/"
                        f"{classification.classification_id}/raw-response.txt"
                    ),
                    raw_response_path,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            confidence=classification.confidence,
            sensitivity=classification.sensitivity,
            risk_level=risk_for_sensitivity(classification.sensitivity),
            reason=semantic.reason,
            uncertainties_json=json.dumps(
                list(semantic.uncertainties),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            model_id=response.model_id,
            prompt_version=PROPOSAL_PROMPT_VERSION,
            raw_response_path=raw_response_path,
            content_hash=content.content_hash,
            draft_note_path=None,
            state="pending",
            created_at=response.received_at,
        )

    def _proposal_prompt(self, evidence, classification: ClassificationRecord) -> str:
        captured_text = evidence.raw_path.read_bytes().decode("utf-8")
        classification_json = json.dumps(
            {
                "candidate_type": classification.candidate_type,
                "sensitivity": classification.sensitivity,
                "confidence": classification.confidence,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            load_proposal_prompt()
            .replace("{{CAPTURE_JSON}}", json.dumps(captured_text, ensure_ascii=False))
            .replace("{{CLASSIFICATION_JSON}}", classification_json)
        )

    def _parse_timestamp(self, value: str) -> datetime:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if self._timestamp(parsed) != value:
            raise ValueError("timestamp is not canonical UTC")
        return parsed

    def _now_timestamp(self) -> str:
        return self._timestamp(
            self._clock().astimezone(timezone.utc).replace(microsecond=0)
        )

    def _fail_from_model_error(
        self,
        capture_id: str,
        classification: ClassificationRecord,
        reservation: ProposalReservationRecord,
        error: ModelAdapterError,
        reason: str,
    ) -> ProposalResult:
        raw_response_path = None
        if error.raw_text is not None:
            response = self._preserve_response(
                reservation,
                error.raw_text,
                error.model_id,
            )
            if response is None:
                reason = "proposal_evidence_failed"
            else:
                raw_response_path = str(
                    Path(response.evidence_path) / "raw-response.txt"
                )
        return self._fail_reserved(
            capture_id,
            classification,
            reservation.proposal_id,
            reservation.lease_token,
            reason,
            raw_response_path=raw_response_path,
            intake_state="proposing",
        )

    def _preserve_response(
        self,
        reservation: ProposalReservationRecord,
        raw_text,
        model_id,
    ):
        try:
            raw_bytes = raw_text.encode("utf-8")
        except (AttributeError, TypeError, UnicodeEncodeError):
            return None
        directory = (
            self._runtime_root / "proposal-evidence" / reservation.proposal_id
        )
        try:
            self._proposal_evidence_store.create(
                reservation.proposal_id,
                reservation.classification_id,
                reservation.capture_id,
                raw_text,
                model_id,
                PROPOSAL_PROMPT_VERSION,
                reservation.reserved_at,
            )
        except ProposalEvidenceCollision:
            pass
        except ProposalEvidenceError:
            return None
        try:
            response = self._proposal_evidence_store.validate_directory(directory)
            observed_bytes = response.raw_path.read_bytes()
        except (OSError, ProposalEvidenceError):
            return None
        if (
            response.proposal_id != reservation.proposal_id
            or response.classification_id != reservation.classification_id
            or response.capture_id != reservation.capture_id
            or response.model_id != model_id
            or response.prompt_version != PROPOSAL_PROMPT_VERSION
            or response.received_at != reservation.reserved_at
            or len(observed_bytes) != len(raw_bytes)
            or observed_bytes != raw_bytes
            or response.raw_response_hash != hashlib.sha256(raw_bytes).hexdigest()
        ):
            return None
        return response

    def _preserve_content(
        self,
        reservation: ProposalReservationRecord,
        raw_response_hash: str,
        body_bytes: bytes,
    ):
        directory = (
            self._runtime_root / "proposal-content" / reservation.proposal_id
        )
        try:
            expected_content_hash = hashlib.sha256(body_bytes).hexdigest()
            if os.path.lexists(directory):
                content = self._content_store.validate_directory(directory)
            else:
                try:
                    self._content_store.create(
                        reservation.proposal_id,
                        reservation.classification_id,
                        reservation.capture_id,
                        raw_response_hash,
                        body_bytes,
                    )
                except ProposalContentCollision:
                    pass
                content = self._content_store.validate_directory(directory)
            observed_bytes = content.body_path.read_bytes()
        except (OSError, ProposalContentStoreError, TypeError):
            return None, False
        matches = (
            content.proposal_id == reservation.proposal_id
            and content.classification_id == reservation.classification_id
            and content.capture_id == reservation.capture_id
            and content.raw_response_hash == raw_response_hash
            and content.content_hash == expected_content_hash
            and content.byte_size == len(body_bytes)
            and len(observed_bytes) == len(body_bytes)
            and observed_bytes == body_bytes
        )
        return content, matches

    def _fail_reserved(
        self,
        capture_id: str,
        classification: ClassificationRecord,
        proposal_id: str,
        lease_token: str,
        reason: str,
        *,
        status: ProposalStatus = ProposalStatus.FAILED,
        raw_response_path: Optional[str] = None,
        content_path: Optional[str] = None,
        intake_state: Optional[str] = None,
    ) -> ProposalResult:
        try:
            failed = self._state_store.record_proposal_failure(
                capture_id,
                lease_token,
                f"proposal.{reason}",
                self._timestamp(
                    self._clock().astimezone(timezone.utc).replace(microsecond=0)
                ),
            )
        except (
            AttributeError,
            OverflowError,
            StateStoreError,
            StateTransitionRefused,
            TypeError,
            ValueError,
        ):
            return self._state_undetermined(
                capture_id,
                classification=classification,
                proposal_id=proposal_id,
                raw_response_path=raw_response_path,
                content_path=content_path,
                intake_state=intake_state,
            )
        message = (
            "proposal content was refused"
            if status is ProposalStatus.REFUSED
            else "proposal failed"
        )
        return self._result(
            status,
            capture_id,
            classification=classification,
            proposal_id=proposal_id,
            risk_level=risk_for_sensitivity(classification.sensitivity),
            raw_response_path=raw_response_path,
            content_path=content_path,
            intake_state=failed.state,
            reason=reason,
            message=message,
        )

    def _fail_draft(
        self,
        classification: ClassificationRecord,
        proposal: ProposalRecord,
        reason: str,
        raw_response_path: str,
        content_path: str,
        *,
        draft_path: Optional[str] = None,
    ) -> ProposalResult:
        try:
            failed = self._state_store.record_draft_failure(
                proposal.capture_id,
                proposal.proposal_id,
                f"proposal.{reason}",
                self._timestamp(
                    self._clock().astimezone(timezone.utc).replace(microsecond=0)
                ),
            )
        except (
            AttributeError,
            OverflowError,
            StateStoreError,
            StateTransitionRefused,
            TypeError,
            ValueError,
        ):
            return self._state_undetermined(
                proposal.capture_id,
                classification=classification,
                proposal_id=proposal.proposal_id,
                title=proposal.title,
                risk_level=proposal.risk_level,
                raw_response_path=raw_response_path,
                content_path=content_path,
                draft_path=draft_path,
                intake_state="proposed",
            )
        return self._result(
            ProposalStatus.FAILED,
            proposal.capture_id,
            classification=classification,
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            risk_level=proposal.risk_level,
            raw_response_path=raw_response_path,
            content_path=content_path,
            draft_path=draft_path,
            intake_state=failed.state,
            reason=reason,
            message="proposal failed",
        )

    def _consistency_failure(
        self,
        capture_id: str,
        *,
        classification: Optional[ClassificationRecord] = None,
        proposal_id: Optional[str] = None,
        intake_state: Optional[str] = None,
    ) -> ProposalResult:
        return self._result(
            ProposalStatus.FAILED,
            capture_id,
            classification=classification,
            proposal_id=proposal_id,
            intake_state=intake_state,
            reason="proposal_consistency_failed",
            message="proposal state or evidence does not agree",
        )

    def _state_undetermined(
        self,
        capture_id: str,
        *,
        classification: Optional[ClassificationRecord] = None,
        proposal_id: Optional[str] = None,
        title: Optional[str] = None,
        risk_level: Optional[str] = None,
        raw_response_path: Optional[str] = None,
        content_path: Optional[str] = None,
        draft_path: Optional[str] = None,
        intake_state: Optional[str] = None,
    ) -> ProposalResult:
        return self._result(
            ProposalStatus.FAILED,
            capture_id,
            classification=classification,
            proposal_id=proposal_id,
            title=title,
            risk_level=risk_level,
            raw_response_path=raw_response_path,
            content_path=content_path,
            draft_path=draft_path,
            intake_state=intake_state,
            reason="proposal_state_undetermined",
            message="proposal state could not be determined",
        )

    def _validated_prior_state(
        self,
        intake,
        classification: ClassificationRecord,
    ):
        if (
            intake.capture_id != classification.capture_id
            or intake.source_type != "cli-typed"
            or intake.evidence_path != f"evidence/{intake.capture_id}"
            or intake.trace_id != intake.capture_id
            or not is_ulid(classification.classification_id)
            or classification.candidate_type not in ROUTING
            or classification.sensitivity not in {"normal", "sensitive"}
            or type(classification.confidence) not in (int, float)
            or not 0 <= classification.confidence <= 1
            or classification.routing
            != ROUTING[classification.candidate_type]
            or classification.prompt_version != "classify-v1"
            or classification.raw_response_path
            != (
                "classification-evidence/"
                f"{classification.classification_id}/raw-response.txt"
            )
        ):
            return None
        try:
            evidence = self._evidence_store.validate_directory(
                self._runtime_root / intake.evidence_path
            )
            response = self._classification_store.validate_directory(
                self._runtime_root / Path(classification.raw_response_path).parent
            )
            parsed = parse_classification_response(
                response.raw_path.read_text(encoding="utf-8")
            )
        except (
            ClassificationEvidenceError,
            EvidenceError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            return None
        if (
            evidence.capture_id != intake.capture_id
            or evidence.content_hash != intake.content_hash
            or evidence.captured_at != intake.captured_at
            or response.classification_id != classification.classification_id
            or response.capture_id != classification.capture_id
            or response.model_id != classification.model_id
            or response.prompt_version != classification.prompt_version
            or response.received_at != classification.created_at
            or parsed
            != (
                classification.candidate_type,
                classification.sensitivity,
                classification.confidence,
            )
        ):
            return None
        return evidence

    def _result(
        self,
        status: ProposalStatus,
        capture_id: str,
        *,
        classification: Optional[ClassificationRecord] = None,
        proposal_id: Optional[str] = None,
        title: Optional[str] = None,
        risk_level: Optional[str] = None,
        raw_response_path: Optional[str] = None,
        content_path: Optional[str] = None,
        draft_path: Optional[str] = None,
        intake_state: Optional[str] = None,
        reason: Optional[str] = None,
        message: Optional[str] = None,
    ) -> ProposalResult:
        return ProposalResult(
            status=status,
            capture_id=capture_id,
            classification_id=(
                None if classification is None else classification.classification_id
            ),
            proposal_id=proposal_id,
            note_type=(
                None if classification is None else classification.candidate_type
            ),
            title=title,
            confidence=(
                None if classification is None else float(classification.confidence)
            ),
            sensitivity=(
                None if classification is None else classification.sensitivity
            ),
            risk_level=risk_level,
            raw_response_path=raw_response_path,
            content_path=content_path,
            draft_path=draft_path,
            intake_state=intake_state,
            reason=reason,
            message=message,
        )

    def _is_uuid4(self, value: str) -> bool:
        try:
            parsed = UUID(value)
        except (AttributeError, TypeError, ValueError):
            return False
        return parsed.version == 4 and str(parsed) == value

    def _timestamp(self, value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

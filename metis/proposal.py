"""Deterministic orchestration for proposal creation and draft registration."""

from __future__ import annotations

import json
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
from .proposal_content import ProposalContentError as ProposalContentStoreError
from .proposal_content import ProposalContentStore
from .proposal_contract import (
    ProposalContentError,
    ProposalContentPolicyRefusal,
    parse_proposal_response,
    render_proposal_body,
    risk_for_sensitivity,
)
from .proposal_evidence import ProposalEvidenceError, ProposalEvidenceStore


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
        evidence = self._validated_prior_state(intake, classification)
        if evidence is None:
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
        try:
            content = self._content_store.create(
                proposal_id,
                classification.classification_id,
                capture_id,
                response.raw_response_hash,
                body_bytes,
            )
            content = self._content_store.validate_directory(content.directory)
        except ProposalContentStoreError:
            return self._fail_reserved(
                capture_id, classification, proposal_id, lease_token,
                "proposal_content_failed",
                raw_response_path=raw_response_path,
                intake_state="proposing",
            )
        if content.raw_response_hash != response.raw_response_hash:
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
            response = self._proposal_evidence_store.create(
                reservation.proposal_id,
                reservation.classification_id,
                reservation.capture_id,
                raw_text,
                model_id,
                PROPOSAL_PROMPT_VERSION,
                reservation.reserved_at,
            )
            return self._proposal_evidence_store.validate_directory(
                response.directory
            )
        except ProposalEvidenceError:
            return None

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
            or intake.failure_reason is not None
            or intake.state_updated_at != classification.created_at
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

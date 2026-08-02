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
from .classification_evidence import ClassificationEvidenceStore
from .data_access import (
    ClassificationRecord,
    ProposalRecord,
    ProposalReservationRecord,
    StateStore,
)
from .draft_notes import DraftNoteStore, render_proposed_draft
from .evidence import EvidenceStore
from .identifiers import is_ulid, new_ulid
from .model_adapters import ModelAdapter
from .prompts import PROPOSAL_PROMPT_VERSION, load_proposal_prompt
from .proposal_content import ProposalContentStore
from .proposal_contract import (
    parse_proposal_response,
    render_proposal_body,
    risk_for_sensitivity,
)
from .proposal_evidence import ProposalEvidenceStore


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
        intake = self._state_store.find_intake_by_capture_id(capture_id)
        classification = self._state_store.find_classification_by_capture_id(
            capture_id
        )
        if intake is None or classification is None or intake.state != "classified":
            return self._result(
                ProposalStatus.REFUSED,
                capture_id,
                reason="proposal_consistency_failed",
                message="classified capture is not available for proposal",
            )
        evidence = self._validated_prior_state(intake, classification)
        if evidence is None:
            return self._result(
                ProposalStatus.FAILED,
                capture_id,
                classification=classification,
                reason="proposal_consistency_failed",
                message="capture and classification state does not agree",
            )

        proposal_id = self._id_factory()
        lease_token = self._lease_token_factory()
        if not is_ulid(proposal_id) or not self._is_uuid4(lease_token):
            return self._result(
                ProposalStatus.FAILED,
                capture_id,
                classification=classification,
                reason="proposal_consistency_failed",
                message="proposal identity allocation failed",
            )
        now = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        reserved_at = self._timestamp(now)
        reservation = ProposalReservationRecord(
            proposal_id=proposal_id,
            capture_id=capture_id,
            classification_id=classification.classification_id,
            lease_token=lease_token,
            reserved_at=reserved_at,
            lease_expires_at=self._timestamp(now + timedelta(minutes=15)),
        )
        self._state_store.begin_proposal(reservation)

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
        prompt = (
            load_proposal_prompt()
            .replace(
                "{{CAPTURE_JSON}}",
                json.dumps(captured_text, ensure_ascii=False),
            )
            .replace("{{CLASSIFICATION_JSON}}", classification_json)
        )
        model_response = self._model_adapter.propose(prompt)
        response = self._proposal_evidence_store.create(
            proposal_id,
            classification.classification_id,
            capture_id,
            model_response.raw_text,
            model_response.model_id,
            PROPOSAL_PROMPT_VERSION,
            reserved_at,
        )
        response = self._proposal_evidence_store.validate_directory(
            response.directory
        )
        semantic = parse_proposal_response(
            response.raw_path.read_text(encoding="utf-8")
        )
        body_bytes = render_proposal_body(semantic)
        content = self._content_store.create(
            proposal_id,
            classification.classification_id,
            capture_id,
            response.raw_response_hash,
            body_bytes,
        )
        content = self._content_store.validate_directory(content.directory)
        if content.raw_response_hash != response.raw_response_hash:
            raise ValueError("proposal content lineage disagrees")
        if (
            self._state_store.find_proposal_reservation_by_capture_id(capture_id)
            != reservation
        ):
            raise ValueError("proposal lease is no longer owned")

        raw_response_path = str(Path(response.evidence_path) / "raw-response.txt")
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
        self._state_store.complete_proposal(record, lease_token)
        draft_path = f"vault/notes/proposed/note.{capture_id}.md"
        expected_draft = render_proposed_draft(record, body_bytes)
        draft = self._draft_store.create(draft_path, expected_draft)
        self._draft_store.validate(draft.draft_path, expected_draft)
        registered = self._state_store.register_proposal_draft(
            capture_id,
            proposal_id,
            draft.draft_path,
            reserved_at,
        )
        final_intake = self._state_store.find_intake_by_capture_id(capture_id)
        final_proposal = self._state_store.find_proposal_by_capture_id(capture_id)
        if (
            final_intake is None
            or final_intake.state != "awaiting_approval"
            or final_proposal != registered
            or registered.draft_note_path != draft.draft_path
        ):
            raise ValueError("proposal final state disagrees")
        self._proposal_evidence_store.validate_directory(response.directory)
        self._content_store.validate_directory(content.directory)
        self._draft_store.validate(draft.draft_path, expected_draft)
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
            or self._state_store.find_proposal_by_capture_id(intake.capture_id)
            is not None
            or self._state_store.find_proposal_reservation_by_capture_id(
                intake.capture_id
            )
            is not None
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
        except Exception:
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

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import (
    ClassificationRecord,
    IntakeRecord,
    SQLiteStateStore,
)
from metis.draft_notes import DraftNoteStore, DraftStatus, render_proposed_draft
from metis.evidence import EvidenceStore
from metis.model_adapters import ModelResponse
from metis.proposal import ProposalService, ProposalStatus
from metis.proposal_content import ProposalContentStore
from metis.proposal_evidence import ProposalEvidenceStore


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
PROPOSAL_ID = "01K1D5Q5M00000000000000001"
LEASE_TOKEN = "f52c0470-93d3-4a47-a864-9e504cf1bfcd"
CAPTURED_AT = "2026-08-02T19:00:00Z"
CLASSIFIED_AT = "2026-08-02T19:01:00Z"
PROPOSED_AT = "2026-08-02T20:00:00Z"
CAPTURE_TEXT = "Develop a small review workflow."
PROPOSAL_RAW = json.dumps(
    {
        "title": "Develop a review workflow",
        "body": "Define a small, reviewable workflow before implementation.",
        "reason": "The captured idea explicitly requests a review workflow.",
        "uncertainties": ["The first review cadence is not specified."],
    },
    separators=(",", ":"),
)


class InspectingAdapter:
    def __init__(self, store: SQLiteStateStore, raw_text: str = PROPOSAL_RAW) -> None:
        self.store = store
        self.raw_text = raw_text
        self.proposal_prompts: list[str] = []

    def classify(self, prompt: str) -> ModelResponse:
        raise AssertionError("proposal service must not classify")

    def propose(self, prompt: str) -> ModelResponse:
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        reservation = self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        if intake is None or intake.state != "proposing" or reservation is None:
            raise AssertionError("model call occurred before durable reservation")
        self.proposal_prompts.append(prompt)
        return ModelResponse("claude-proposal-returned-model", self.raw_text)


class ProposalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.state_store = SQLiteStateStore(self.runtime_root / "state.db")
        self.addCleanup(self.state_store.close)
        self.state_store.initialize()
        self.evidence_store = EvidenceStore(self.runtime_root)
        self.classification_store = ClassificationEvidenceStore(self.runtime_root)
        self.proposal_evidence_store = ProposalEvidenceStore(self.runtime_root)
        self.content_store = ProposalContentStore(self.runtime_root)
        self.draft_store = DraftNoteStore(self.runtime_root)

    def _classified(
        self,
        *,
        candidate_type: str = "idea",
        sensitivity: str = "normal",
        confidence: float = 0.82,
    ) -> None:
        raw_bytes = CAPTURE_TEXT.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        evidence = self.evidence_store.create(
            CAPTURE_ID,
            raw_bytes,
            content_hash,
            CAPTURED_AT,
        )
        self.state_store.register_intake(
            IntakeRecord(
                capture_id=CAPTURE_ID,
                content_hash=content_hash,
                captured_at=CAPTURED_AT,
                source_type="cli-typed",
                evidence_path=evidence.evidence_path,
                state="captured",
                state_updated_at=CAPTURED_AT,
                failure_reason=None,
                trace_id=CAPTURE_ID,
            )
        )
        self.state_store.begin_classification(CAPTURE_ID, CLASSIFIED_AT)
        classification_raw = json.dumps(
            {
                "candidate_type": candidate_type,
                "sensitivity": sensitivity,
                "confidence": confidence,
            },
            separators=(",", ":"),
        )
        response = self.classification_store.create(
            CLASSIFICATION_ID,
            CAPTURE_ID,
            classification_raw,
            "claude-classification-model",
            "classify-v1",
            CLASSIFIED_AT,
        )
        self.state_store.complete_classification(
            ClassificationRecord(
                classification_id=CLASSIFICATION_ID,
                capture_id=CAPTURE_ID,
                candidate_type=candidate_type,
                sensitivity=sensitivity,
                confidence=confidence,
                routing=f"proposal:{candidate_type}",
                model_id="claude-classification-model",
                prompt_version="classify-v1",
                raw_response_path=str(
                    Path(response.evidence_path) / "raw-response.txt"
                ),
                created_at=CLASSIFIED_AT,
            )
        )

    def _service(self, adapter: InspectingAdapter) -> ProposalService:
        return ProposalService(
            self.state_store,
            self.evidence_store,
            self.classification_store,
            self.proposal_evidence_store,
            self.content_store,
            self.draft_store,
            adapter,
            self.runtime_root,
            id_factory=lambda: PROPOSAL_ID,
            lease_token_factory=lambda: LEASE_TOKEN,
            clock=lambda: datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc),
        )

    def test_valid_classified_capture_creates_proposal_and_draft(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        result = self._service(adapter).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.capture_id, CAPTURE_ID)
        self.assertEqual(result.classification_id, CLASSIFICATION_ID)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(result.note_type, "idea")
        self.assertEqual(result.title, "Develop a review workflow")
        self.assertEqual(result.confidence, 0.82)
        self.assertEqual(result.sensitivity, "normal")
        self.assertEqual(result.risk_level, "low")
        self.assertEqual(
            result.raw_response_path,
            f"proposal-evidence/{PROPOSAL_ID}/raw-response.txt",
        )
        self.assertEqual(
            result.content_path,
            f"proposal-content/{PROPOSAL_ID}/body.md",
        )
        self.assertEqual(
            result.draft_path,
            f"vault/notes/proposed/note.{CAPTURE_ID}.md",
        )
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertIsNone(result.reason)
        self.assertIsNone(result.message)
        self.assertEqual(len(adapter.proposal_prompts), 1)

        intake = self.state_store.find_intake_by_capture_id(CAPTURE_ID)
        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "awaiting_approval")
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.state, "pending")
        self.assertEqual(proposal.proposed_links, "[]")
        self.assertEqual(proposal.draft_note_path, result.draft_path)
        self.assertIsNone(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )
        draft = self.draft_store.validate(
            result.draft_path,
            render_proposed_draft(
                proposal,
                (self.runtime_root / result.content_path).read_bytes(),
            ),
        )
        self.assertEqual(draft.observed_status, DraftStatus.PROPOSED)

    def test_classification_owns_type_sensitivity_confidence_and_risk(self) -> None:
        self._classified(
            candidate_type="decision",
            sensitivity="sensitive",
            confidence=0.99,
        )
        adapter = InspectingAdapter(self.state_store)

        result = self._service(adapter).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.note_type, "decision")
        self.assertEqual(result.sensitivity, "sensitive")
        self.assertEqual(result.confidence, 0.99)
        self.assertEqual(result.risk_level, "high")
        self.assertEqual(result.intake_state, "awaiting_approval")

    def test_prompt_contains_delimited_capture_and_classification_data(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        self._service(adapter).propose(CAPTURE_ID)

        prompt = adapter.proposal_prompts[0]
        self.assertIn(json.dumps(CAPTURE_TEXT, ensure_ascii=False), prompt)
        self.assertIn('"candidate_type":"idea"', prompt)
        self.assertIn('"sensitivity":"normal"', prompt)
        self.assertIn('"confidence":0.82', prompt)
        self.assertNotIn(PROPOSAL_ID, prompt)
        self.assertNotIn(LEASE_TOKEN, prompt)


if __name__ == "__main__":
    unittest.main()

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
    StateStoreError,
)
from metis.draft_notes import (
    DraftNoteStore,
    DraftNoteWriteError,
    DraftStatus,
    render_proposed_draft,
)
from metis.evidence import EvidenceStore
from metis.model_adapters import (
    ModelConfigurationError,
    ModelRequestError,
    ModelResponse,
    ModelResponseRefused,
    ModelResponseTruncated,
    UnsupportedModelResponse,
)
from metis.proposal import ProposalService, ProposalStatus
from metis.proposal_content import (
    ProposalContentCollision,
    ProposalContentStore,
)
from metis.proposal_evidence import (
    ProposalEvidenceCollision,
    ProposalEvidenceStore,
)


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
MISMATCHED_PROPOSAL_RAW = (
    '{"title":"Different proposal","body":"Do not reuse this response.",'
    '"reason":"A complete but different winner.","uncertainties":[]}'
)
MISMATCHED_BODY_BYTES = b"Do not reuse this canonical proposal body.\n"


class InspectingAdapter:
    def __init__(self, store: SQLiteStateStore, raw_text: str = PROPOSAL_RAW) -> None:
        self.store = store
        self.raw_text = raw_text
        self.error = None
        self.proposal_prompts: list[str] = []

    def classify(self, prompt: str) -> ModelResponse:
        raise AssertionError("proposal service must not classify")

    def propose(self, prompt: str) -> ModelResponse:
        intake = self.store.find_intake_by_capture_id(CAPTURE_ID)
        reservation = self.store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        if intake is None or intake.state != "proposing" or reservation is None:
            raise AssertionError("model call occurred before durable reservation")
        self.proposal_prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return ModelResponse("claude-proposal-returned-model", self.raw_text)


class StateStoreProxy:
    def __init__(self, store: SQLiteStateStore) -> None:
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)


class FailingStateStore(StateStoreProxy):
    def __init__(self, store: SQLiteStateStore, failing_method: str) -> None:
        super().__init__(store)
        self.failing_method = failing_method

    def __getattr__(self, name):
        if name == self.failing_method:
            def fail(*args, **kwargs):
                raise StateStoreError("unsafe database detail")

            return fail
        return super().__getattr__(name)


class FailingDraftStore:
    def create(self, relative_path, expected_bytes):
        raise DraftNoteWriteError("unsafe filesystem detail", relative_path)

    def validate(self, relative_path, expected_bytes):
        raise AssertionError("failed draft must not be validated")


class RaceWinningProposalEvidenceStore:
    def __init__(self, store: ProposalEvidenceStore) -> None:
        self.store = store

    def create(self, *args, **kwargs):
        record = self.store.create(*args, **kwargs)
        raise ProposalEvidenceCollision(
            "simulated exclusive-create race",
            record.evidence_path,
        )

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class MismatchedProposalEvidenceStore:
    def __init__(self, store: ProposalEvidenceStore) -> None:
        self.store = store

    def create(
        self,
        proposal_id,
        classification_id,
        capture_id,
        raw_text,
        model_id,
        prompt_version,
        received_at,
    ):
        record = self.store.create(
            proposal_id,
            classification_id,
            capture_id,
            MISMATCHED_PROPOSAL_RAW,
            model_id,
            prompt_version,
            received_at,
        )
        raise ProposalEvidenceCollision(
            "simulated exclusive-create race",
            record.evidence_path,
        )

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class RaceWinningProposalContentStore:
    def __init__(self, store: ProposalContentStore) -> None:
        self.store = store

    def create(self, *args, **kwargs):
        record = self.store.create(*args, **kwargs)
        raise ProposalContentCollision(
            "simulated exclusive-create race",
            record.content_path,
        )

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class MismatchedProposalContentStore:
    def __init__(self, store: ProposalContentStore) -> None:
        self.store = store

    def create(
        self,
        proposal_id,
        classification_id,
        capture_id,
        raw_response_hash,
        body_bytes,
    ):
        record = self.store.create(
            proposal_id,
            classification_id,
            capture_id,
            raw_response_hash,
            MISMATCHED_BODY_BYTES,
        )
        raise ProposalContentCollision(
            "simulated exclusive-create race",
            record.content_path,
        )

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


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

    def _service(
        self,
        adapter: InspectingAdapter,
        *,
        state_store=None,
        draft_store=None,
        proposal_evidence_store=None,
        content_store=None,
    ) -> ProposalService:
        return ProposalService(
            self.state_store if state_store is None else state_store,
            self.evidence_store,
            self.classification_store,
            (
                self.proposal_evidence_store
                if proposal_evidence_store is None
                else proposal_evidence_store
            ),
            self.content_store if content_store is None else content_store,
            self.draft_store if draft_store is None else draft_store,
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

    def test_missing_or_noncanonical_capture_fails_before_model_call(self) -> None:
        adapter = InspectingAdapter(self.state_store)

        for capture_id in (CAPTURE_ID, "not-a-uuid"):
            with self.subTest(capture_id=capture_id):
                result = self._service(adapter).propose(capture_id)
                self.assertEqual(result.status, ProposalStatus.FAILED)
                self.assertEqual(result.reason, "proposal_consistency_failed")

        self.assertEqual(adapter.proposal_prompts, [])
        self.assertFalse((self.runtime_root / "proposal-evidence").exists())

    def test_corrupt_classification_evidence_fails_before_model_call(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        raw_path = (
            self.runtime_root
            / "classification-evidence"
            / CLASSIFICATION_ID
            / "raw-response.txt"
        )
        raw_path.write_text("corrupt", encoding="utf-8")

        result = self._service(adapter).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(adapter.proposal_prompts, [])
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "classified",
        )

    def test_model_configuration_failure_is_recorded_safely(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        adapter.error = ModelConfigurationError(
            "model_configuration_failed",
            "unsafe provider configuration detail",
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_configuration_failed")
        self.assertIsNone(result.raw_response_path)
        self.assertNotIn("unsafe", result.message)

    def test_model_request_failure_is_recorded_safely(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        adapter.error = ModelRequestError(
            "model_request_failed",
            "unsafe provider request detail",
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_request_failed")
        self.assertIsNone(result.raw_response_path)

    def test_refused_response_is_preserved_before_failure_recording(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        raw_text = '{"refusal":"unsafe provider text"}'
        adapter.error = ModelResponseRefused(
            "model_response_refused",
            "unsafe provider refusal detail",
            model_id="claude-returned-model",
            raw_text=raw_text,
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_response_refused")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_text(encoding="utf-8"),
            raw_text,
        )
        self.assertNotIn("unsafe", result.message)

    def test_truncated_response_uses_stable_reason(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        adapter.error = ModelResponseTruncated(
            "model_response_truncated",
            "unsafe truncated detail",
            model_id="claude-returned-model",
            raw_text='{"title":"partial',
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_response_truncated")
        self.assertIsNotNone(result.raw_response_path)

    def test_unsupported_response_uses_invalid_reason(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)
        adapter.error = UnsupportedModelResponse(
            "model_response_invalid",
            "unsafe shape detail",
            model_id="claude-returned-model",
            raw_text='{"unexpected":true}',
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_response_invalid")
        self.assertIsNotNone(result.raw_response_path)

    def test_invalid_response_is_preserved_then_recorded_failed(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store, "not json")

        result = self._service(adapter).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "model_response_invalid")
        self.assertEqual(
            (self.runtime_root / result.raw_response_path).read_text(encoding="utf-8"),
            "not json",
        )

    def test_unsafe_content_is_refused_without_echo_or_draft(self) -> None:
        self._classified()
        secret = "sk-abcdefghijklmnop"
        adapter = InspectingAdapter(
            self.state_store,
            json.dumps(
                {
                    "title": "Unsafe",
                    "body": f"Do not write {secret}",
                    "reason": "Provider supplied unsafe content.",
                    "uncertainties": [],
                }
            ),
        )

        result = self._service(adapter).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.REFUSED)
        self.assertEqual(result.reason, "proposal_content_failed")
        self.assertNotIn(secret, result.message)
        self.assertFalse((self.runtime_root / "vault").exists())
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).failure_reason,
            "proposal.proposal_content_failed",
        )

    def test_partial_proposal_evidence_is_preserved_and_fails_closed(self) -> None:
        self._classified()
        partial = self.runtime_root / "proposal-evidence" / PROPOSAL_ID
        partial.mkdir(parents=True)
        (partial / "partial.txt").write_text("preserve", encoding="utf-8")

        result = self._service(InspectingAdapter(self.state_store)).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "proposal_evidence_failed")
        self.assertEqual((partial / "partial.txt").read_text(), "preserve")
        self.assertIsNone(result.raw_response_path)

    # Mutation caught: treating every ProposalEvidenceCollision as proposal_evidence_failed.
    def test_matching_response_race_winner_is_reused(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        result = self._service(
            adapter,
            proposal_evidence_store=RaceWinningProposalEvidenceStore(
                self.proposal_evidence_store
            ),
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(len(adapter.proposal_prompts), 1)
        response_directory = self.runtime_root / "proposal-evidence" / PROPOSAL_ID
        self.assertEqual(
            {path.name for path in response_directory.iterdir()},
            {"raw-response.txt", "meta.json"},
        )
        self.assertEqual(
            (response_directory / "raw-response.txt").read_text(encoding="utf-8"),
            PROPOSAL_RAW,
        )

    # Mutation caught: treating every complete ProposalEvidenceCollision winner as reusable.
    def test_disagreeing_response_race_winner_fails_closed(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        result = self._service(
            adapter,
            proposal_evidence_store=MismatchedProposalEvidenceStore(
                self.proposal_evidence_store
            ),
        ).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "proposal_evidence_failed")
        self.assertEqual(len(adapter.proposal_prompts), 1)
        self.assertEqual(
            (
                self.runtime_root
                / "proposal-evidence"
                / PROPOSAL_ID
                / "raw-response.txt"
            ).read_text(encoding="utf-8"),
            MISMATCHED_PROPOSAL_RAW,
        )
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))

    # Mutation caught: treating every ProposalContentCollision as proposal_content_failed.
    def test_matching_content_race_winner_is_reused(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        result = self._service(
            adapter,
            content_store=RaceWinningProposalContentStore(self.content_store),
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(len(adapter.proposal_prompts), 1)
        content_directory = self.runtime_root / "proposal-content" / PROPOSAL_ID
        self.assertEqual(
            {path.name for path in content_directory.iterdir()},
            {"body.md", "meta.json"},
        )
        self.assertEqual(
            result.content_path,
            f"proposal-content/{PROPOSAL_ID}/body.md",
        )

    # Mutation caught: reusing a complete ProposalContentCollision winner with different bytes.
    def test_disagreeing_content_race_winner_fails_closed(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store)

        result = self._service(
            adapter,
            content_store=MismatchedProposalContentStore(self.content_store),
        ).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "proposal_content_failed")
        self.assertEqual(len(adapter.proposal_prompts), 1)
        self.assertEqual(
            (
                self.runtime_root
                / "proposal-content"
                / PROPOSAL_ID
                / "body.md"
            ).read_bytes(),
            MISMATCHED_BODY_BYTES,
        )
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))

    def test_partial_proposal_content_is_preserved_and_fails_closed(self) -> None:
        self._classified()
        partial = self.runtime_root / "proposal-content" / PROPOSAL_ID
        partial.mkdir(parents=True)
        (partial / "partial.txt").write_text("preserve", encoding="utf-8")

        result = self._service(InspectingAdapter(self.state_store)).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "proposal_content_failed")
        self.assertEqual((partial / "partial.txt").read_text(), "preserve")
        self.assertIsNotNone(result.raw_response_path)
        self.assertIsNone(result.content_path)

    def test_proposal_persistence_failure_is_recorded(self) -> None:
        self._classified()
        failing = FailingStateStore(self.state_store, "complete_proposal")

        result = self._service(
            InspectingAdapter(self.state_store),
            state_store=failing,
        ).propose(CAPTURE_ID)

        self._assert_recorded_attempt_failure(result, "proposal_persistence_failed")
        self.assertIsNotNone(result.raw_response_path)
        self.assertIsNotNone(result.content_path)

    def test_existing_draft_is_a_collision_and_records_draft_failure(self) -> None:
        self._classified()
        draft = self.runtime_root / "vault" / "notes" / "proposed"
        draft.mkdir(parents=True)
        path = draft / f"note.{CAPTURE_ID}.md"
        path.write_text("preexisting", encoding="utf-8")

        result = self._service(InspectingAdapter(self.state_store)).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "draft_collision")
        self.assertEqual(path.read_text(encoding="utf-8"), "preexisting")
        intake = self.state_store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(intake.failure_reason, "proposal.draft_collision")
        self.assertIsNotNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))

    def test_draft_write_failure_is_recorded_without_unsafe_detail(self) -> None:
        self._classified()

        result = self._service(
            InspectingAdapter(self.state_store),
            draft_store=FailingDraftStore(),
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "draft_write_failed")
        self.assertNotIn("unsafe", result.message)
        intake = self.state_store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(intake.failure_reason, "proposal.draft_write_failed")

    def test_failure_recording_failure_reports_state_undetermined(self) -> None:
        self._classified()
        adapter = InspectingAdapter(self.state_store, "not json")
        failing = FailingStateStore(self.state_store, "record_proposal_failure")

        result = self._service(adapter, state_store=failing).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_state_undetermined")
        self.assertNotIn("unsafe", result.message)

    def test_state_load_failure_reports_state_undetermined(self) -> None:
        failing = FailingStateStore(
            self.state_store,
            "find_intake_by_capture_id",
        )

        result = self._service(
            InspectingAdapter(self.state_store),
            state_store=failing,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_state_undetermined")

    def _assert_recorded_attempt_failure(self, result, reason: str) -> None:
        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, reason)
        intake = self.state_store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(intake.failure_reason, f"proposal.{reason}")
        reservation = self.state_store.find_proposal_reservation_by_capture_id(
            CAPTURE_ID
        )
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.proposal_id, PROPOSAL_ID)
        self.assertEqual(reservation.lease_expires_at, PROPOSED_AT)


if __name__ == "__main__":
    unittest.main()

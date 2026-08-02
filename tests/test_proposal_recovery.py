from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from metis.classification_evidence import ClassificationEvidenceStore
from metis.data_access import (
    ClassificationRecord,
    IntakeRecord,
    ProposalReservationRecord,
    SQLiteStateStore,
    StateTransitionRefused,
)
from metis.draft_notes import DraftNoteStore, DraftStatus, render_proposed_draft
from metis.evidence import EvidenceStore
from metis.model_adapters import ModelResponse
from metis.proposal import ProposalService, ProposalStatus
from metis.proposal_content import ProposalContentStore
from metis.proposal_contract import parse_proposal_response, render_proposal_body
from metis.proposal_evidence import ProposalEvidenceStore

from tests.test_proposal import (
    CAPTURE_ID,
    CLASSIFICATION_ID,
    LEASE_TOKEN,
    PROPOSAL_ID,
    PROPOSAL_RAW,
)


RECLAIMED_TOKEN = "584d1e60-5217-4c83-8f08-2b9f15bd977a"


class RecoveryAdapter:
    def __init__(self, raw_text: str = PROPOSAL_RAW, *, allow_call: bool = True):
        self.raw_text = raw_text
        self.allow_call = allow_call
        self.calls = 0

    def classify(self, prompt: str) -> ModelResponse:
        raise AssertionError("proposal recovery must not classify")

    def propose(self, prompt: str) -> ModelResponse:
        self.calls += 1
        if not self.allow_call:
            raise AssertionError("recovery must not call the model")
        return ModelResponse("claude-proposal-returned-model", self.raw_text)


class InjectedCrash(BaseException):
    pass


class CrashingDraftStore:
    def create(self, relative_path, expected_bytes):
        raise InjectedCrash("crash after proposal persistence")

    def validate(self, relative_path, expected_bytes):
        raise AssertionError("crashing draft store must not validate")


class RegistrationCrashStore:
    def __init__(self, store: SQLiteStateStore) -> None:
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    def register_proposal_draft(self, *args, **kwargs):
        raise InjectedCrash("crash after exact draft write")


class ReclaimingLeaseStore:
    def __init__(self, store: SQLiteStateStore) -> None:
        self.store = store
        self.reads = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    def find_proposal_reservation_by_capture_id(self, capture_id):
        self.reads += 1
        current = self.store.find_proposal_reservation_by_capture_id(capture_id)
        if self.reads == 2 and current is not None:
            replacement = ProposalReservationRecord(
                proposal_id=current.proposal_id,
                capture_id=current.capture_id,
                classification_id=current.classification_id,
                lease_token=RECLAIMED_TOKEN,
                reserved_at="2026-08-02T20:16:00Z",
                lease_expires_at="2026-08-02T20:31:00Z",
            )
            return self.store.reclaim_proposal(
                current,
                replacement,
                "2026-08-02T20:16:00Z",
            )
        return current


class ProposalRecoveryTests(unittest.TestCase):
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
        self._classified()

    def _classified(self) -> None:
        raw_bytes = b"Develop a small review workflow."
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        evidence = self.evidence_store.create(
            CAPTURE_ID,
            raw_bytes,
            content_hash,
            "2026-08-02T19:00:00Z",
        )
        self.state_store.register_intake(
            IntakeRecord(
                capture_id=CAPTURE_ID,
                content_hash=content_hash,
                captured_at="2026-08-02T19:00:00Z",
                source_type="cli-typed",
                evidence_path=evidence.evidence_path,
                state="captured",
                state_updated_at="2026-08-02T19:00:00Z",
                failure_reason=None,
                trace_id=CAPTURE_ID,
            )
        )
        self.state_store.begin_classification(
            CAPTURE_ID,
            "2026-08-02T19:01:00Z",
        )
        response = self.classification_store.create(
            CLASSIFICATION_ID,
            CAPTURE_ID,
            '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}',
            "claude-classification-model",
            "classify-v1",
            "2026-08-02T19:01:00Z",
        )
        self.state_store.complete_classification(
            ClassificationRecord(
                classification_id=CLASSIFICATION_ID,
                capture_id=CAPTURE_ID,
                candidate_type="idea",
                sensitivity="normal",
                confidence=0.82,
                routing="proposal:idea",
                model_id="claude-classification-model",
                prompt_version="classify-v1",
                raw_response_path=(
                    f"{response.evidence_path}/raw-response.txt"
                ),
                created_at="2026-08-02T19:01:00Z",
            )
        )

    def _service(
        self,
        adapter: RecoveryAdapter,
        *,
        state_store=None,
        draft_store=None,
        proposal_id_factory=lambda: PROPOSAL_ID,
        lease_token_factory=lambda: LEASE_TOKEN,
        at_hour: int = 20,
        at_minute: int = 0,
    ) -> ProposalService:
        return ProposalService(
            self.state_store if state_store is None else state_store,
            self.evidence_store,
            self.classification_store,
            self.proposal_evidence_store,
            self.content_store,
            self.draft_store if draft_store is None else draft_store,
            adapter,
            self.runtime_root,
            id_factory=proposal_id_factory,
            lease_token_factory=lease_token_factory,
            clock=lambda: datetime(
                2026,
                8,
                2,
                at_hour,
                at_minute,
                tzinfo=timezone.utc,
            ),
        )

    def _reserve(self, *, expires_at: str) -> ProposalReservationRecord:
        reservation = ProposalReservationRecord(
            proposal_id=PROPOSAL_ID,
            capture_id=CAPTURE_ID,
            classification_id=CLASSIFICATION_ID,
            lease_token=LEASE_TOKEN,
            reserved_at="2026-08-02T20:00:00Z",
            lease_expires_at=expires_at,
        )
        self.state_store.begin_proposal(reservation)
        return reservation

    def _create_response_and_content(self) -> None:
        response = self._create_response()
        semantic = parse_proposal_response(PROPOSAL_RAW)
        self.content_store.create(
            PROPOSAL_ID,
            CLASSIFICATION_ID,
            CAPTURE_ID,
            response.raw_response_hash,
            render_proposal_body(semantic),
        )

    def _create_response(self):
        return self.proposal_evidence_store.create(
            PROPOSAL_ID,
            CLASSIFICATION_ID,
            CAPTURE_ID,
            PROPOSAL_RAW,
            "claude-proposal-returned-model",
            "propose-v1",
            "2026-08-02T20:00:00Z",
        )

    def test_exact_replay_returns_duplicate_without_model_or_second_artifact(self):
        first_adapter = RecoveryAdapter()
        first = self._service(first_adapter).propose(CAPTURE_ID)
        proposal_before = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        draft_before = (self.runtime_root / first.draft_path).read_bytes()
        replay_adapter = RecoveryAdapter(allow_call=False)

        replay = self._service(replay_adapter).propose(CAPTURE_ID)

        self.assertEqual(replay.status, ProposalStatus.DUPLICATE)
        self.assertEqual(replay.proposal_id, first.proposal_id)
        self.assertEqual(replay.draft_path, first.draft_path)
        self.assertEqual(replay.intake_state, "awaiting_approval")
        self.assertEqual(replay_adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_by_capture_id(CAPTURE_ID),
            proposal_before,
        )
        self.assertEqual((self.runtime_root / first.draft_path).read_bytes(), draft_before)

    def test_active_reservation_refuses_without_identity_or_model_change(self):
        original = self._reserve(expires_at="2026-08-02T20:15:00Z")
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=5,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.REFUSED)
        self.assertEqual(result.reason, "proposal_in_progress")
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            original,
        )

    def test_expired_reservation_reclaims_same_proposal_id(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(adapter.calls, 1)
        self.assertIsNone(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )

    def test_complete_response_and_content_resume_without_model_call(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        self._create_response_and_content()
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(adapter.calls, 0)

    def test_complete_response_alone_is_reparsed_without_model_call(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        self._create_response()
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(adapter.calls, 0)
        self.assertTrue((self.runtime_root / result.content_path).is_file())

    def test_proposed_row_after_crash_resumes_only_draft_stage(self):
        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                draft_store=CrashingDraftStore(),
            ).propose(CAPTURE_ID)
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "proposed",
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(adapter, at_minute=1).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(adapter.calls, 0)

    def test_recorded_draft_failure_resumes_without_model_call(self):
        from tests.test_proposal import FailingDraftStore

        first = self._service(
            RecoveryAdapter(),
            draft_store=FailingDraftStore(),
        ).propose(CAPTURE_ID)
        self.assertEqual(first.reason, "draft_write_failed")
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "failed",
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(adapter, at_minute=1).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(adapter.calls, 0)

    def test_exact_unregistered_draft_is_registered_not_rewritten(self):
        crashing_state = RegistrationCrashStore(self.state_store)
        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                state_store=crashing_state,
            ).propose(CAPTURE_ID)
        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        expected_path = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
        before = (self.runtime_root / expected_path).read_bytes()
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(adapter, at_minute=1).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.draft_path, expected_path)
        self.assertEqual((self.runtime_root / expected_path).read_bytes(), before)
        self.assertEqual(adapter.calls, 0)
        registered = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertEqual(registered.draft_note_path, expected_path)
        self.assertEqual(proposal.proposal_id, registered.proposal_id)

    def test_human_approval_status_is_preserved_and_not_interpreted(self):
        first = self._service(RecoveryAdapter()).propose(CAPTURE_ID)
        path = self.runtime_root / first.draft_path
        proposed = path.read_bytes()
        for status in (DraftStatus.APPROVED, DraftStatus.REJECTED):
            with self.subTest(status=status):
                changed = proposed.replace(
                    b"status: proposed\n",
                    f"status: {status.value}\n".encode("utf-8"),
                    1,
                )
                path.write_bytes(changed)
                adapter = RecoveryAdapter(allow_call=False)

                result = self._service(adapter).propose(CAPTURE_ID)

                self.assertEqual(result.status, ProposalStatus.REFUSED)
                self.assertEqual(result.reason, "proposal_consistency_failed")
                self.assertEqual(path.read_bytes(), changed)
                proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
                draft = self.draft_store.validate(
                    first.draft_path,
                    render_proposed_draft(
                        proposal,
                        (self.runtime_root / proposal.body_path).read_bytes(),
                    ),
                )
                self.assertEqual(draft.observed_status, status)
                self.assertEqual(adapter.calls, 0)
                self.assertEqual(
                    self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
                    "awaiting_approval",
                )

    def test_reclaimed_token_fences_stale_worker_completion(self):
        state_store = ReclaimingLeaseStore(self.state_store)

        result = self._service(
            RecoveryAdapter(),
            state_store=state_store,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_state_undetermined")
        current = self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        self.assertEqual(current.lease_token, RECLAIMED_TOKEN)
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))
        with self.assertRaises(StateTransitionRefused):
            self.state_store.record_proposal_failure(
                CAPTURE_ID,
                LEASE_TOKEN,
                "proposal.model_request_failed",
                "2026-08-02T20:16:00Z",
            )

    def test_corrupt_replay_fails_closed_without_rewrite(self):
        first = self._service(RecoveryAdapter()).propose(CAPTURE_ID)
        path = self.runtime_root / first.draft_path
        path.write_text("corrupt", encoding="utf-8")
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(adapter).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(path.read_text(encoding="utf-8"), "corrupt")
        self.assertEqual(adapter.calls, 0)

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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

from tests.data_access.inspection import (
    audit_event_rows,
    force_intake_state,
    force_proposal_reservation_timestamps,
    table_row_count,
)
from tests.test_proposal import (
    CAPTURE_ID,
    CLASSIFICATION_ID,
    LEASE_TOKEN,
    PROPOSAL_ID,
    PROPOSAL_RAW,
    RaceWinningProposalContentStore,
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


class CrashAfterStateMutationStore:
    def __init__(self, store: SQLiteStateStore, mutation: str) -> None:
        self.store = store
        self.mutation = mutation

    def __getattr__(self, name):
        attribute = getattr(self.store, name)
        if name != self.mutation:
            return attribute

        def mutate_then_crash(*args, **kwargs):
            attribute(*args, **kwargs)
            raise InjectedCrash(f"crash after {name}")

        return mutate_then_crash


class _CrashAfterCloseStream:
    def __init__(self, stream, message: str) -> None:
        self._stream = stream
        self._message = message

    def __enter__(self):
        self._stream.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stream.__exit__(exc_type, exc_value, traceback)
        raise InjectedCrash(self._message)

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _CrashAfterEnterStream:
    def __init__(self, stream, message: str) -> None:
        self._stream = stream
        self._message = message

    def __enter__(self):
        self._stream.__enter__()
        self._stream.__exit__(None, None, None)
        raise InjectedCrash(self._message)

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


class ProposalEvidenceCrashStore:
    BEFORE_WRITE = "before_write"
    AFTER_RAW_BYTES = "after_raw_bytes"
    AFTER_METADATA = "after_metadata"

    def __init__(
        self,
        store: ProposalEvidenceStore,
        runtime_root: Path,
        boundary: str,
    ) -> None:
        self.store = store
        self.runtime_root = runtime_root
        self.boundary = boundary

    def create(self, *args, **kwargs):
        if self.boundary == self.BEFORE_WRITE:
            raise InjectedCrash("crash before proposal evidence write")
        if self.boundary == self.AFTER_METADATA:
            self.store.create(*args, **kwargs)
            raise InjectedCrash("crash after proposal evidence metadata")
        proposal_id = args[0]
        raw_path = (
            self.runtime_root
            / "proposal-evidence"
            / proposal_id
            / "raw-response.txt"
        )
        original_open = Path.open

        def crashing_open(path, *open_args, **open_kwargs):
            stream = original_open(path, *open_args, **open_kwargs)
            if path == raw_path and open_args == ("xb",):
                return _CrashAfterCloseStream(
                    stream,
                    "crash after proposal response bytes",
                )
            return stream

        with patch.object(Path, "open", crashing_open):
            return self.store.create(*args, **kwargs)

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class ReadOnlyProposalEvidenceStore:
    def __init__(self, store: ProposalEvidenceStore) -> None:
        self.store = store

    def create(self, *args, **kwargs):
        raise AssertionError("restart must not rewrite proposal response evidence")

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class ProposalContentCrashStore:
    BEFORE_WRITE = "before_write"
    AFTER_BODY_BYTES = "after_body_bytes"
    AFTER_METADATA = "after_metadata"

    def __init__(
        self,
        store: ProposalContentStore,
        runtime_root: Path,
        boundary: str,
    ) -> None:
        self.store = store
        self.runtime_root = runtime_root
        self.boundary = boundary

    def create(self, *args, **kwargs):
        if self.boundary == self.BEFORE_WRITE:
            raise InjectedCrash("crash before proposal content write")
        if self.boundary == self.AFTER_METADATA:
            self.store.create(*args, **kwargs)
            raise InjectedCrash("crash after proposal content metadata")
        proposal_id = args[0]
        body_path = self.runtime_root / "proposal-content" / proposal_id / "body.md"
        original_open = Path.open

        def crashing_open(path, *open_args, **open_kwargs):
            stream = original_open(path, *open_args, **open_kwargs)
            if path == body_path and open_args == ("xb",):
                return _CrashAfterCloseStream(
                    stream,
                    "crash after proposal content bytes",
                )
            return stream

        with patch.object(Path, "open", crashing_open):
            return self.store.create(*args, **kwargs)

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class ReadOnlyProposalContentStore:
    def __init__(self, store: ProposalContentStore) -> None:
        self.store = store

    def create(self, *args, **kwargs):
        raise AssertionError("restart must not rewrite proposal content")

    def validate_directory(self, directory):
        return self.store.validate_directory(directory)


class DraftCrashStore:
    AFTER_EXCLUSIVE_CREATE = "after_exclusive_create"
    AFTER_EXACT_CLOSE = "after_exact_close"
    AFTER_VALIDATION = "after_validation"

    def __init__(
        self,
        store: DraftNoteStore,
        runtime_root: Path,
        boundary: str,
    ) -> None:
        self.store = store
        self.runtime_root = runtime_root
        self.boundary = boundary

    def create(self, relative_path, expected_bytes):
        if self.boundary == self.AFTER_VALIDATION:
            return self.store.create(relative_path, expected_bytes)
        draft_path = self.runtime_root / relative_path
        original_open = Path.open

        def crashing_open(path, *open_args, **open_kwargs):
            stream = original_open(path, *open_args, **open_kwargs)
            if path != draft_path or open_args != ("xb",):
                return stream
            if self.boundary == self.AFTER_EXCLUSIVE_CREATE:
                return _CrashAfterEnterStream(
                    stream,
                    "crash after exclusive draft creation",
                )
            return _CrashAfterCloseStream(
                stream,
                "crash after exact draft close",
            )

        with patch.object(Path, "open", crashing_open):
            return self.store.create(relative_path, expected_bytes)

    def validate(self, relative_path, expected_bytes):
        record = self.store.validate(relative_path, expected_bytes)
        if self.boundary == self.AFTER_VALIDATION:
            raise InjectedCrash("crash after exact draft validation")
        return record


class ReadOnlyDraftStore:
    def __init__(self, store: DraftNoteStore) -> None:
        self.store = store

    def create(self, relative_path, expected_bytes):
        raise AssertionError("restart must not rewrite an existing draft")

    def validate(self, relative_path, expected_bytes):
        return self.store.validate(relative_path, expected_bytes)


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
                type_pin="",
                parent_id="",
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
        proposal_evidence_store=None,
        content_store=None,
        proposal_id_factory=lambda: PROPOSAL_ID,
        lease_token_factory=lambda: LEASE_TOKEN,
        at_hour: int = 20,
        at_minute: int = 0,
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

    def _crash_after_proposal_completion(self) -> None:
        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                state_store=CrashAfterStateMutationStore(
                    self.state_store,
                    "complete_proposal",
                ),
            ).propose(CAPTURE_ID)

    def test_proposing_with_failure_reason_is_refused_before_reclaim(self):
        reservation = self._reserve(expires_at="2026-08-02T20:15:00Z")
        force_intake_state(
            self.state_store,
            CAPTURE_ID,
            state="proposing",
            state_updated_at=reservation.reserved_at,
            failure_reason="proposal.model_request_failed",
        )
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            reservation,
        )
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).failure_reason,
            "proposal.model_request_failed",
        )

    def test_proposing_requires_exact_fifteen_minute_lease(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        force_proposal_reservation_timestamps(
            self.state_store,
            CAPTURE_ID,
            reserved_at="2026-08-02T20:00:00Z",
            lease_expires_at="2026-08-02T20:16:00Z",
        )
        reservation = self.state_store.find_proposal_reservation_by_capture_id(
            CAPTURE_ID
        )
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=17,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            reservation,
        )

    def test_proposing_requires_state_and_reservation_timestamp_agreement(self):
        reservation = self._reserve(expires_at="2026-08-02T20:15:00Z")
        force_intake_state(
            self.state_store,
            CAPTURE_ID,
            state="proposing",
            state_updated_at="2026-08-02T20:01:00Z",
            failure_reason=None,
        )
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            reservation,
        )

    def test_failed_reservation_requires_failure_timestamp_agreement(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        self.state_store.record_proposal_failure(
            CAPTURE_ID,
            LEASE_TOKEN,
            "proposal.model_request_failed",
            "2026-08-02T20:01:00Z",
        )
        force_intake_state(
            self.state_store,
            CAPTURE_ID,
            state="failed",
            state_updated_at="2026-08-02T20:02:00Z",
            failure_reason="proposal.model_request_failed",
        )
        reservation = self.state_store.find_proposal_reservation_by_capture_id(
            CAPTURE_ID
        )
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID),
            reservation,
        )

    def test_proposed_with_failure_reason_does_not_write_draft(self):
        self._crash_after_proposal_completion()
        force_intake_state(
            self.state_store,
            CAPTURE_ID,
            state="proposed",
            state_updated_at="2026-08-02T20:01:00Z",
            failure_reason="proposal.draft_write_failed",
        )
        draft_path = (
            self.runtime_root
            / "vault"
            / "notes"
            / "proposed"
            / f"note.{CAPTURE_ID}.md"
        )

        result = self._service(RecoveryAdapter(allow_call=False)).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertFalse(draft_path.exists())
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).failure_reason,
            "proposal.draft_write_failed",
        )

    def test_failed_proposal_requires_draft_stage_failure_reason(self):
        self._crash_after_proposal_completion()
        force_intake_state(
            self.state_store,
            CAPTURE_ID,
            state="failed",
            state_updated_at="2026-08-02T20:01:00Z",
            failure_reason="proposal.model_request_failed",
        )
        draft_path = (
            self.runtime_root
            / "vault"
            / "notes"
            / "proposed"
            / f"note.{CAPTURE_ID}.md"
        )

        result = self._service(RecoveryAdapter(allow_call=False)).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_consistency_failed")
        self.assertFalse(draft_path.exists())
        intake = self.state_store.find_intake_by_capture_id(CAPTURE_ID)
        self.assertEqual(intake.state, "failed")
        self.assertEqual(intake.failure_reason, "proposal.model_request_failed")

    # Mutation caught: moving the provider call before the durable reservation.
    def test_crash_after_reservation_reclaims_one_stable_proposal_id(self):
        crashing_state = CrashAfterStateMutationStore(
            self.state_store,
            "begin_proposal",
        )
        first_adapter = RecoveryAdapter()

        with self.assertRaises(InjectedCrash):
            self._service(
                first_adapter,
                state_store=crashing_state,
            ).propose(CAPTURE_ID)

        reservation = self.state_store.find_proposal_reservation_by_capture_id(
            CAPTURE_ID
        )
        self.assertEqual(reservation.proposal_id, PROPOSAL_ID)
        self.assertEqual(first_adapter.calls, 0)
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "proposing",
        )
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))

        active = self._service(
            RecoveryAdapter(allow_call=False),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=14,
        ).propose(CAPTURE_ID)
        self.assertEqual(active.status, ProposalStatus.REFUSED)
        self.assertEqual(active.reason, "proposal_in_progress")
        self.assertEqual(active.proposal_id, PROPOSAL_ID)

        recovered_adapter = RecoveryAdapter()
        recovered = self._service(
            recovered_adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(recovered.status, ProposalStatus.PROPOSED)
        self.assertEqual(recovered.proposal_id, PROPOSAL_ID)
        self.assertEqual(recovered_adapter.calls, 1)
        self.assertEqual(table_row_count(self.state_store, "proposal"), 1)
        self.assertEqual(
            table_row_count(self.state_store, "proposal_reservation"),
            0,
        )

    # Mutation caught: reporting success after provider return before response
    # evidence exists.
    def test_crash_after_provider_return_repeats_once_only_after_reclaim(self):
        first_adapter = RecoveryAdapter()
        crashing_evidence = ProposalEvidenceCrashStore(
            self.proposal_evidence_store,
            self.runtime_root,
            ProposalEvidenceCrashStore.BEFORE_WRITE,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                first_adapter,
                proposal_evidence_store=crashing_evidence,
            ).propose(CAPTURE_ID)

        self.assertEqual(first_adapter.calls, 1)
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertFalse((self.runtime_root / "vault").exists())
        self.assertFalse(
            (self.runtime_root / "proposal-evidence" / PROPOSAL_ID).exists()
        )

        second_adapter = RecoveryAdapter()
        recovered = self._service(
            second_adapter,
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(recovered.status, ProposalStatus.PROPOSED)
        self.assertEqual(recovered.proposal_id, PROPOSAL_ID)
        self.assertEqual(second_adapter.calls, 1)
        self.assertEqual(table_row_count(self.state_store, "proposal"), 1)
        self.assertEqual(
            len(list((self.runtime_root / "vault/notes/proposed").iterdir())),
            1,
        )

    # Mutation caught: repairing or overwriting raw-only response evidence on restart.
    def test_crash_after_response_bytes_preserves_partial_evidence_and_fails_closed(
        self,
    ):
        crashing_evidence = ProposalEvidenceCrashStore(
            self.proposal_evidence_store,
            self.runtime_root,
            ProposalEvidenceCrashStore.AFTER_RAW_BYTES,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                proposal_evidence_store=crashing_evidence,
            ).propose(CAPTURE_ID)

        response_directory = self.runtime_root / "proposal-evidence" / PROPOSAL_ID
        raw_path = response_directory / "raw-response.txt"
        before = raw_path.read_bytes()
        self.assertEqual(before, PROPOSAL_RAW.encode("utf-8"))
        self.assertEqual(
            {path.name for path in response_directory.iterdir()},
            {"raw-response.txt"},
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_evidence_failed")
        self.assertEqual(result.intake_state, "failed")
        self.assertEqual(raw_path.read_bytes(), before)
        self.assertEqual(
            {path.name for path in response_directory.iterdir()},
            {"raw-response.txt"},
        )
        self.assertEqual(adapter.calls, 0)
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertFalse((self.runtime_root / "vault").exists())

    # Mutation caught: calling the provider again when complete response metadata
    # is recoverable.
    def test_crash_after_response_metadata_reuses_response_without_provider_call(self):
        crashing_evidence = ProposalEvidenceCrashStore(
            self.proposal_evidence_store,
            self.runtime_root,
            ProposalEvidenceCrashStore.AFTER_METADATA,
        )
        first_adapter = RecoveryAdapter()

        with self.assertRaises(InjectedCrash):
            self._service(
                first_adapter,
                proposal_evidence_store=crashing_evidence,
            ).propose(CAPTURE_ID)

        response_directory = self.runtime_root / "proposal-evidence" / PROPOSAL_ID
        before = {
            path.name: path.read_bytes()
            for path in response_directory.iterdir()
        }
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            {path.name: path.read_bytes() for path in response_directory.iterdir()},
            before,
        )

    # Mutation caught: requiring another provider call instead of reparsing
    # canonical response evidence.
    def test_crash_after_parse_reparses_canonical_response_without_provider_call(self):
        crashing_content = ProposalContentCrashStore(
            self.content_store,
            self.runtime_root,
            ProposalContentCrashStore.BEFORE_WRITE,
        )
        first_adapter = RecoveryAdapter()

        with self.assertRaises(InjectedCrash):
            self._service(
                first_adapter,
                content_store=crashing_content,
            ).propose(CAPTURE_ID)

        response_path = (
            self.runtime_root
            / "proposal-evidence"
            / PROPOSAL_ID
            / "raw-response.txt"
        )
        response_before = response_path.read_bytes()
        self.assertFalse(
            (self.runtime_root / "proposal-content" / PROPOSAL_ID).exists()
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        semantic = parse_proposal_response(PROPOSAL_RAW)
        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(response_path.read_bytes(), response_before)
        self.assertEqual(
            (self.runtime_root / result.content_path).read_bytes(),
            render_proposal_body(semantic),
        )

    # Mutation caught: repairing or overwriting body-only proposal content on restart.
    def test_crash_after_content_bytes_preserves_partial_content_and_fails_closed(self):
        crashing_content = ProposalContentCrashStore(
            self.content_store,
            self.runtime_root,
            ProposalContentCrashStore.AFTER_BODY_BYTES,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                content_store=crashing_content,
            ).propose(CAPTURE_ID)

        content_directory = self.runtime_root / "proposal-content" / PROPOSAL_ID
        body_path = content_directory / "body.md"
        before = body_path.read_bytes()
        self.assertEqual(
            before,
            render_proposal_body(parse_proposal_response(PROPOSAL_RAW)),
        )
        self.assertEqual(
            {path.name for path in content_directory.iterdir()},
            {"body.md"},
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            content_store=ReadOnlyProposalContentStore(self.content_store),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "proposal_content_failed")
        self.assertEqual(result.intake_state, "failed")
        self.assertEqual(body_path.read_bytes(), before)
        self.assertEqual(
            {path.name for path in content_directory.iterdir()},
            {"body.md"},
        )
        self.assertEqual(adapter.calls, 0)
        self.assertIsNone(self.state_store.find_proposal_by_capture_id(CAPTURE_ID))
        self.assertFalse((self.runtime_root / "vault").exists())

    # Mutation caught: regenerating canonical content when complete metadata is
    # recoverable.
    def test_crash_after_content_metadata_revalidates_without_provider_call(self):
        crashing_content = ProposalContentCrashStore(
            self.content_store,
            self.runtime_root,
            ProposalContentCrashStore.AFTER_METADATA,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                content_store=crashing_content,
            ).propose(CAPTURE_ID)

        content_directory = self.runtime_root / "proposal-content" / PROPOSAL_ID
        before = {
            path.name: path.read_bytes()
            for path in content_directory.iterdir()
        }
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            content_store=ReadOnlyProposalContentStore(self.content_store),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(
            {path.name: path.read_bytes() for path in content_directory.iterdir()},
            before,
        )

    # Mutation caught: repeating provider or artifact stages after proposal commit.
    def test_crash_after_proposal_transaction_resumes_only_draft_stage(self):
        crashing_state = CrashAfterStateMutationStore(
            self.state_store,
            "complete_proposal",
        )
        first_adapter = RecoveryAdapter()

        with self.assertRaises(InjectedCrash):
            self._service(
                first_adapter,
                state_store=crashing_state,
            ).propose(CAPTURE_ID)

        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertIsNotNone(proposal)
        self.assertIsNone(proposal.draft_note_path)
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "proposed",
        )
        self.assertIsNone(
            self.state_store.find_proposal_reservation_by_capture_id(CAPTURE_ID)
        )
        self.assertFalse((self.runtime_root / "vault").exists())
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            proposal_evidence_store=ReadOnlyProposalEvidenceStore(
                self.proposal_evidence_store
            ),
            content_store=ReadOnlyProposalContentStore(self.content_store),
            at_minute=1,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(table_row_count(self.state_store, "proposal"), 1)

    # Mutation caught: repairing an empty exclusively created draft during restart.
    def test_crash_after_exclusive_draft_create_preserves_empty_file_and_fails_closed(
        self,
    ):
        crashing_draft = DraftCrashStore(
            self.draft_store,
            self.runtime_root,
            DraftCrashStore.AFTER_EXCLUSIVE_CREATE,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                draft_store=crashing_draft,
            ).propose(CAPTURE_ID)

        draft_path = self.runtime_root / f"vault/notes/proposed/note.{CAPTURE_ID}.md"
        self.assertTrue(draft_path.is_file())
        self.assertEqual(draft_path.read_bytes(), b"")
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            draft_store=ReadOnlyDraftStore(self.draft_store),
            at_minute=1,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.FAILED)
        self.assertEqual(result.reason, "draft_collision")
        self.assertEqual(result.intake_state, "failed")
        self.assertEqual(draft_path.read_bytes(), b"")
        self.assertEqual(adapter.calls, 0)
        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertIsNone(proposal.draft_note_path)

    # Mutation caught: rewriting an exact closed draft instead of validating and
    # registering it.
    def test_crash_after_exact_draft_close_registers_without_rewrite(self):
        crashing_draft = DraftCrashStore(
            self.draft_store,
            self.runtime_root,
            DraftCrashStore.AFTER_EXACT_CLOSE,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                draft_store=crashing_draft,
            ).propose(CAPTURE_ID)

        draft_path = self.runtime_root / f"vault/notes/proposed/note.{CAPTURE_ID}.md"
        before = draft_path.read_bytes()
        self.assertTrue(before)
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            draft_store=ReadOnlyDraftStore(self.draft_store),
            at_minute=1,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(draft_path.read_bytes(), before)
        self.assertEqual(adapter.calls, 0)
        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertEqual(proposal.draft_note_path, result.draft_path)

    # Mutation caught: rewriting an exact validated draft instead of registering it.
    def test_crash_after_draft_validation_registers_without_rewrite(self):
        crashing_draft = DraftCrashStore(
            self.draft_store,
            self.runtime_root,
            DraftCrashStore.AFTER_VALIDATION,
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                draft_store=crashing_draft,
            ).propose(CAPTURE_ID)

        draft_path = self.runtime_root / f"vault/notes/proposed/note.{CAPTURE_ID}.md"
        before = draft_path.read_bytes()
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            draft_store=ReadOnlyDraftStore(self.draft_store),
            at_minute=1,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(draft_path.read_bytes(), before)
        self.assertEqual(adapter.calls, 0)
        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        self.assertEqual(proposal.draft_note_path, result.draft_path)

    # Mutation caught: treating a committed draft registration as new work on restart.
    def test_crash_after_draft_registration_returns_duplicate_on_restart(self):
        crashing_state = CrashAfterStateMutationStore(
            self.state_store,
            "register_proposal_draft",
        )

        with self.assertRaises(InjectedCrash):
            self._service(
                RecoveryAdapter(),
                state_store=crashing_state,
            ).propose(CAPTURE_ID)

        proposal = self.state_store.find_proposal_by_capture_id(CAPTURE_ID)
        draft_path = self.runtime_root / proposal.draft_note_path
        before = draft_path.read_bytes()
        self.assertEqual(
            self.state_store.find_intake_by_capture_id(CAPTURE_ID).state,
            "awaiting_approval",
        )
        adapter = RecoveryAdapter(allow_call=False)

        result = self._service(
            adapter,
            draft_store=ReadOnlyDraftStore(self.draft_store),
            at_minute=1,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.DUPLICATE)
        self.assertEqual(result.proposal_id, PROPOSAL_ID)
        self.assertEqual(result.intake_state, "awaiting_approval")
        self.assertEqual(draft_path.read_bytes(), before)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(table_row_count(self.state_store, "proposal"), 1)

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
        actions = [event.action for event in audit_event_rows(self.state_store)]
        self.assertEqual(actions.count("proposal.reclaimed"), 1)

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

    # Mutation caught: treating a matching ProposalContentCollision after recovery as fatal.
    def test_expired_reservation_reuses_matching_content_race_winner(self):
        self._reserve(expires_at="2026-08-02T20:15:00Z")
        adapter = RecoveryAdapter()

        result = self._service(
            adapter,
            content_store=RaceWinningProposalContentStore(self.content_store),
            proposal_id_factory=lambda: (_ for _ in ()).throw(AssertionError()),
            lease_token_factory=lambda: RECLAIMED_TOKEN,
            at_minute=16,
        ).propose(CAPTURE_ID)

        self.assertEqual(result.status, ProposalStatus.PROPOSED)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(
            {
                path.name
                for path in (
                    self.runtime_root / "proposal-content" / PROPOSAL_ID
                ).iterdir()
            },
            {"body.md", "meta.json"},
        )

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
        actions = [event.action for event in audit_event_rows(self.state_store)]
        self.assertEqual(actions.count("draft.failed"), 1)
        self.assertEqual(actions.count("proposal.resumed"), 1)

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

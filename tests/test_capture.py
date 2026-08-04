from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from unittest.mock import patch
from uuid import UUID

from metis.capture import CaptureResult, CaptureService, CaptureStatus
from metis.data_access import (
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
    SQLiteStateStore,
    StateStore,
    StateStoreError,
)
from metis.evidence import (
    EvidenceConsistencyError,
    EvidenceRecord,
    EvidenceStore,
    EvidenceWriteError,
)


CAPTURE_ID = UUID("8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70")
OTHER_CAPTURE_ID = UUID("6ba7b810-9dad-41d1-80b4-00c04fd430c8")
CAPTURED_AT = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
CAPTURED_AT_TEXT = "2026-07-31T20:00:00Z"


def intake_record(expected_hash: str, **changes: object) -> IntakeRecord:
    values = {
        "capture_id": str(CAPTURE_ID),
        "content_hash": expected_hash,
        "captured_at": CAPTURED_AT_TEXT,
        "source_type": "cli-typed",
        "evidence_path": f"evidence/{CAPTURE_ID}",
        "state": "captured",
        "state_updated_at": CAPTURED_AT_TEXT,
        "failure_reason": None,
        "trace_id": str(CAPTURE_ID),
        "type_pin": "",
        "parent_id": "",
    }
    values.update(changes)
    return IntakeRecord(**values)


class InMemoryStateStore:
    def __init__(self) -> None:
        self.record: Optional[IntakeRecord] = None

    def find_intake_by_capture_id(self, capture_id: str) -> Optional[IntakeRecord]:
        if self.record is None or self.record.capture_id != capture_id:
            return None
        return self.record

    def find_intake_by_pin_key(
        self, content_hash: str, type_pin: str, parent_id: str
    ) -> Optional[IntakeRecord]:
        if self.record is None or self.record.content_hash != content_hash:
            return None
        if (self.record.type_pin, self.record.parent_id) != (type_pin, parent_id):
            return None
        return self.record

    def append_audit_event(self, record) -> None:
        """Emission is asserted against the real store, not this fake."""

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        self.record = record
        return IntakeRegistrationResult(IntakeRegistrationStatus.REGISTERED, record)


class ReturningStateStore(InMemoryStateStore):
    def find_intake_by_capture_id(self, capture_id: str) -> Optional[IntakeRecord]:
        return self.record

    def find_intake_by_pin_key(
        self, content_hash: str, type_pin: str, parent_id: str
    ) -> Optional[IntakeRecord]:
        return self.record


class LookupFailingStateStore(InMemoryStateStore):
    def find_intake_by_capture_id(self, capture_id: str) -> Optional[IntakeRecord]:
        raise StateStoreError("lookup unavailable")

    def find_intake_by_pin_key(
        self, content_hash: str, type_pin: str, parent_id: str
    ) -> Optional[IntakeRecord]:
        raise StateStoreError("lookup unavailable")


class RegistrationFailingStateStore(InMemoryStateStore):
    def __init__(self, runtime_root: Path) -> None:
        super().__init__()
        self._runtime_root = runtime_root
        self.evidence_bytes_at_registration: Optional[dict[str, bytes]] = None

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        directory = self._runtime_root / record.evidence_path
        self.evidence_bytes_at_registration = {
            "raw.txt": (directory / "raw.txt").read_bytes(),
            "meta.json": (directory / "meta.json").read_bytes(),
        }
        raise StateStoreError("registration unavailable")


class LateDuplicateStateStore(InMemoryStateStore):
    def __init__(self, runtime_root: Path) -> None:
        super().__init__()
        self._runtime_root = runtime_root
        self.evidence_bytes_at_registration: Optional[dict[str, bytes]] = None

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        directory = self._runtime_root / record.evidence_path
        self.evidence_bytes_at_registration = {
            "raw.txt": (directory / "raw.txt").read_bytes(),
            "meta.json": (directory / "meta.json").read_bytes(),
        }
        return IntakeRegistrationResult(IntakeRegistrationStatus.DUPLICATE, record)


class OrphanMismatchStateStore(InMemoryStateStore):
    def __init__(self, existing: IntakeRecord) -> None:
        super().__init__()
        self._existing = existing

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        return IntakeRegistrationResult(
            IntakeRegistrationStatus.DUPLICATE,
            self._existing,
        )


class FailOnceRegistrationStore:
    def __init__(self, store: SQLiteStateStore) -> None:
        self._store = store
        self._should_fail = True

    def find_intake_by_capture_id(self, capture_id: str) -> Optional[IntakeRecord]:
        return self._store.find_intake_by_capture_id(capture_id)

    def find_intake_by_pin_key(
        self, content_hash: str, type_pin: str, parent_id: str
    ) -> Optional[IntakeRecord]:
        return self._store.find_intake_by_pin_key(content_hash, type_pin, parent_id)

    def append_audit_event(self, record) -> None:
        """Emission is asserted against the real store, not this fake."""

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        if self._should_fail:
            self._should_fail = False
            raise StateStoreError("simulated first registration failure")
        return self._store.register_intake(record)


class WriteFailingEvidenceStore(EvidenceStore):
    def create(
        self,
        capture_id: str,
        raw_bytes: bytes,
        content_hash: str,
        captured_at: str,
        type_pin: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> EvidenceRecord:
        raise EvidenceWriteError(
            "simulated evidence write failure",
            f"evidence/{capture_id}",
        )


class ValidationFailingEvidenceStore(EvidenceStore):
    def validate_directory(self, directory: Path) -> EvidenceRecord:
        raise EvidenceConsistencyError(
            "simulated finalized-evidence validation failure",
            f"evidence/{directory.name}",
        )


class FinalizationCheckingStateStore(InMemoryStateStore):
    def __init__(
        self,
        test_case: unittest.TestCase,
        runtime_root: Path,
        evidence_store: EvidenceStore,
    ) -> None:
        super().__init__()
        self._test_case = test_case
        self._runtime_root = runtime_root
        self._evidence_store = evidence_store

    def register_intake(
        self, record: IntakeRecord, *, audit=None
    ) -> IntakeRegistrationResult:
        directory = self._runtime_root / record.evidence_path
        self._test_case.assertTrue((directory / "raw.txt").is_file())
        self._test_case.assertTrue((directory / "meta.json").is_file())
        self._evidence_store.validate_directory(directory)
        return super().register_intake(record)


class CaptureServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.evidence_store = EvidenceStore(self.runtime_root)
        self.state_store = InMemoryStateStore()

    def _service(
        self,
        state_store: StateStore,
        evidence_store: EvidenceStore | None = None,
        *,
        id_factory: Callable[[], UUID] = lambda: CAPTURE_ID,
        clock: Callable[[], datetime] = lambda: CAPTURED_AT,
    ) -> CaptureService:
        return CaptureService(
            state_store,
            evidence_store or self.evidence_store,
            id_factory=id_factory,
            clock=clock,
        )

    def _capture(self, service: CaptureService, text: str) -> CaptureResult:
        try:
            return service.capture(text)
        except Exception as error:
            self.fail(f"capture raised instead of returning a result: {error!r}")

    def _snapshot(self, record: EvidenceRecord) -> dict[str, bytes]:
        return {
            "raw.txt": record.raw_path.read_bytes(),
            "meta.json": record.meta_path.read_bytes(),
        }

    def test_capture_encodes_text_without_modification(self) -> None:
        text = "  café\nsecond line\t  "
        expected_bytes = text.encode("utf-8")
        expected_hash = "sha256:" + hashlib.sha256(expected_bytes).hexdigest()
        service = CaptureService(
            self.state_store,
            self.evidence_store,
            id_factory=lambda: CAPTURE_ID,
            clock=lambda: CAPTURED_AT,
        )

        result = service.capture(text)

        self.assertEqual(result.status, CaptureStatus.CAPTURED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertIsNone(result.reason)
        self.assertIsNone(result.message)
        self.assertEqual(
            (self.runtime_root / result.evidence_path / "raw.txt").read_bytes(),
            expected_bytes,
        )
        self.assertEqual(self.state_store.record.content_hash, expected_hash)

    def test_unencodable_text_returns_failed_before_evidence_access(self) -> None:
        result = self._capture(
            self._service(LookupFailingStateStore()),
            "invalid surrogate: \ud800",
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertIsNone(result.capture_id)
        self.assertIsNone(result.evidence_path)
        self.assertEqual(result.reason, "utf8_encoding_failed")
        self.assertIsInstance(result.message, str)
        self.assertTrue(result.message)
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_evidence_is_finalized_before_registration(self) -> None:
        state_store = FinalizationCheckingStateStore(
            self,
            self.runtime_root,
            self.evidence_store,
        )
        service = CaptureService(
            state_store,
            self.evidence_store,
            id_factory=lambda: CAPTURE_ID,
            clock=lambda: CAPTURED_AT,
        )

        result = service.capture("ordering matters")

        self.assertEqual(result.status, CaptureStatus.CAPTURED)

    def test_exact_replay_returns_duplicate_for_matching_state_and_evidence(
        self,
    ) -> None:
        text = "same input"
        raw_bytes = text.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        evidence = self.evidence_store.create(
            str(CAPTURE_ID), raw_bytes, content_hash, CAPTURED_AT_TEXT
        )
        before = self._snapshot(evidence)
        self.state_store.record = intake_record(content_hash)

        result = self._capture(self._service(self.state_store), text)

        self.assertEqual(result.status, CaptureStatus.DUPLICATE)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, evidence.evidence_path)
        self.assertEqual(result.reason, "exact_replay")
        self.assertIsNone(result.message)
        self.assertEqual(self._snapshot(evidence), before)

    def test_state_row_without_evidence_fails_closed(self) -> None:
        text = "missing evidence"
        content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.state_store.record = intake_record(content_hash)

        result = self._capture(self._service(self.state_store), text)

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "state_evidence_mismatch")
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_each_row_field_must_agree_with_valid_evidence(self) -> None:
        text = "matching evidence"
        raw_bytes = text.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        evidence = self.evidence_store.create(
            str(CAPTURE_ID), raw_bytes, content_hash, CAPTURED_AT_TEXT
        )
        before = self._snapshot(evidence)
        mismatches = {
            "capture_id": str(OTHER_CAPTURE_ID),
            "content_hash": "sha256:" + "0" * 64,
            "captured_at": "2026-07-31T20:00:01Z",
            "source_type": "file-import",
            "evidence_path": f"evidence/{OTHER_CAPTURE_ID}",
            "state": "failed",
            "state_updated_at": "2026-07-31T20:00:01Z",
            "failure_reason": "unexpected",
            "trace_id": str(OTHER_CAPTURE_ID),
        }

        for field, wrong_value in mismatches.items():
            with self.subTest(field=field):
                state_store = ReturningStateStore()
                state_store.record = intake_record(content_hash, **{field: wrong_value})

                result = self._capture(self._service(state_store), text)

                self.assertEqual(result.status, CaptureStatus.FAILED)
                self.assertEqual(result.reason, "state_evidence_mismatch")
                self.assertEqual(self._snapshot(evidence), before)

    def test_corrupt_evidence_scan_fails_closed_without_mutation(self) -> None:
        text = "corrupt orphan"
        raw_bytes = text.encode("utf-8")
        partial = self.runtime_root / "evidence" / str(CAPTURE_ID)
        partial.mkdir(parents=True)
        raw_path = partial / "raw.txt"
        raw_path.write_bytes(raw_bytes)
        before = raw_path.read_bytes()

        result = self._capture(self._service(self.state_store), text)

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.reason, "evidence_inconsistent")
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(raw_path.read_bytes(), before)

    def test_multiple_evidence_matches_fail_closed_without_mutation(self) -> None:
        text = "duplicate orphan evidence"
        raw_bytes = text.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        records = (
            self.evidence_store.create(
                str(CAPTURE_ID), raw_bytes, content_hash, CAPTURED_AT_TEXT
            ),
            self.evidence_store.create(
                str(OTHER_CAPTURE_ID),
                raw_bytes,
                content_hash,
                "2026-07-31T20:00:01Z",
            ),
        )
        before = [self._snapshot(record) for record in records]

        result = self._capture(self._service(self.state_store), text)

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.reason, "evidence_inconsistent")
        self.assertEqual(
            [self._snapshot(record) for record in records],
            before,
        )

    def test_generated_uuid_collision_is_refused_without_mutation(self) -> None:
        existing_bytes = b"different content"
        existing_hash = "sha256:" + hashlib.sha256(existing_bytes).hexdigest()
        existing = self.evidence_store.create(
            str(CAPTURE_ID), existing_bytes, existing_hash, CAPTURED_AT_TEXT
        )
        before = self._snapshot(existing)

        result = self._capture(self._service(self.state_store), "new content")

        self.assertEqual(result.status, CaptureStatus.REFUSED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, existing.evidence_path)
        self.assertEqual(result.reason, "evidence_collision")
        self.assertEqual(self._snapshot(existing), before)

    def test_raw_collision_after_directory_creation_is_failed_and_preserved(
        self,
    ) -> None:
        directory = self.runtime_root / "evidence" / str(CAPTURE_ID)
        raw_path = directory / "raw.txt"
        original_mkdir = Path.mkdir

        def mkdir_then_create_raw(path: Path, *args: object, **kwargs: object) -> None:
            original_mkdir(path, *args, **kwargs)
            if path == directory:
                raw_path.write_bytes(b"raced raw evidence")

        with patch.object(Path, "mkdir", new=mkdir_then_create_raw):
            result = self._capture(self._service(self.state_store), "new content")

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "evidence_write_failed")
        self.assertEqual(raw_path.read_bytes(), b"raced raw evidence")
        self.assertFalse((directory / "meta.json").exists())
        self.assertIsNone(self.state_store.record)

    def test_evidence_write_failure_returns_failed_without_mutation(self) -> None:
        evidence_store = WriteFailingEvidenceStore(self.runtime_root)

        result = self._capture(
            self._service(self.state_store, evidence_store),
            "write failure",
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "evidence_write_failed")
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_validation_failure_after_create_preserves_generated_capture_id(
        self,
    ) -> None:
        evidence_store = ValidationFailingEvidenceStore(self.runtime_root)

        result = self._capture(
            self._service(self.state_store, evidence_store),
            "validation failure",
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "evidence_inconsistent")
        directory = self.runtime_root / result.evidence_path
        self.assertEqual(
            (directory / "raw.txt").read_bytes(),
            b"validation failure",
        )
        self.assertTrue((directory / "meta.json").is_file())
        self.assertIsNone(self.state_store.record)

    def test_state_lookup_failure_precedes_minting_and_any_write(self) -> None:
        """ADR-022 moves the evidence scan ahead of the state lookup.

        Which state row to read depends on whether evidence already named a
        capture id, so evidence must resolve first. The guarantee this test
        exists for is unchanged: a state-store failure reports
        `state_lookup_failed`, mints no id, and writes nothing. A corrupt
        evidence directory is now reported first, as `evidence_inconsistent` —
        also fail-closed — so this case keeps the evidence store clean to reach
        the state lookup at all.
        """
        evidence_store = EvidenceStore(self.runtime_root)
        factory_calls = 0

        def id_factory() -> UUID:
            nonlocal factory_calls
            factory_calls += 1
            return CAPTURE_ID

        result = self._capture(
            self._service(
                LookupFailingStateStore(),
                evidence_store,
                id_factory=id_factory,
            ),
            "lookup failure",
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.reason, "state_lookup_failed")
        self.assertIsNone(result.evidence_path)
        self.assertEqual(factory_calls, 0)
        # No evidence root at all: the previous assertion read a file capture
        # never touches, so it could not have failed.
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_registration_failure_preserves_finalized_evidence(self) -> None:
        text = "registration failure"
        expected_bytes = text.encode("utf-8")
        state_store = RegistrationFailingStateStore(self.runtime_root)

        result = self._capture(
            self._service(state_store),
            text,
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "state_registration_failed")
        directory = self.runtime_root / result.evidence_path
        after = {
            "raw.txt": (directory / "raw.txt").read_bytes(),
            "meta.json": (directory / "meta.json").read_bytes(),
        }
        self.assertEqual(after["raw.txt"], expected_bytes)
        self.evidence_store.validate_directory(directory)
        self.assertEqual(after, state_store.evidence_bytes_at_registration)

    def test_late_duplicate_registration_fails_closed_and_preserves_evidence(
        self,
    ) -> None:
        text = "late duplicate"
        state_store = LateDuplicateStateStore(self.runtime_root)

        result = self._capture(self._service(state_store), text)

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(result.reason, "late_duplicate_registration")
        evidence = self.evidence_store.validate_directory(
            self.runtime_root / result.evidence_path
        )
        self.assertEqual(
            self._snapshot(evidence),
            state_store.evidence_bytes_at_registration,
        )

    def test_non_uuid4_factory_result_fails_before_filesystem_mutation(self) -> None:
        result = self._capture(
            self._service(
                self.state_store,
                id_factory=lambda: UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
            ),
            "invalid generated id",
        )

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertIsNone(result.capture_id)
        self.assertIsNone(result.evidence_path)
        self.assertEqual(result.reason, "invalid_capture_id")
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_orphan_registration_duplicate_mismatch_fails_as_state_mismatch(
        self,
    ) -> None:
        text = "orphan race"
        raw_bytes = text.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        orphan = self.evidence_store.create(
            str(CAPTURE_ID), raw_bytes, content_hash, CAPTURED_AT_TEXT
        )
        before = self._snapshot(orphan)
        state_store = OrphanMismatchStateStore(
            intake_record(content_hash, state="failed", failure_reason="unknown")
        )

        result = self._capture(self._service(state_store), text)

        self.assertEqual(result.status, CaptureStatus.FAILED)
        self.assertEqual(result.reason, "state_evidence_mismatch")
        self.assertEqual(result.capture_id, str(CAPTURE_ID))
        self.assertEqual(result.evidence_path, orphan.evidence_path)
        self.assertEqual(self._snapshot(orphan), before)

    def test_clock_is_normalized_to_whole_second_utc_z(self) -> None:
        clock_value = datetime.fromisoformat("2026-07-31T15:00:00.987654-05:00")

        result = self._capture(
            self._service(self.state_store, clock=lambda: clock_value),
            "timestamp",
        )

        self.assertEqual(result.status, CaptureStatus.CAPTURED)
        self.assertEqual(self.state_store.record.captured_at, CAPTURED_AT_TEXT)
        self.assertEqual(
            self.state_store.record.state_updated_at,
            self.state_store.record.captured_at,
        )

    def test_complete_orphan_is_reused_after_registration_failure(self) -> None:
        sqlite_store = SQLiteStateStore(self.runtime_root / "state.db")
        self.addCleanup(sqlite_store.close)
        sqlite_store.initialize()
        state_store = FailOnceRegistrationStore(sqlite_store)
        id_calls = 0
        clock_calls = 0

        def id_factory() -> UUID:
            nonlocal id_calls
            id_calls += 1
            return CAPTURE_ID

        def clock() -> datetime:
            nonlocal clock_calls
            clock_calls += 1
            return CAPTURED_AT

        service = self._service(
            state_store,
            id_factory=id_factory,
            clock=clock,
        )

        first = self._capture(service, "same input")
        orphan = self.evidence_store.validate_directory(
            self.runtime_root / first.evidence_path
        )
        before_retry = self._snapshot(orphan)
        second = self._capture(service, "same input")

        self.assertEqual(first.status, CaptureStatus.FAILED)
        self.assertEqual(first.reason, "state_registration_failed")
        self.assertEqual(second.status, CaptureStatus.CAPTURED)
        self.assertEqual(second.capture_id, first.capture_id)
        self.assertEqual(second.evidence_path, first.evidence_path)
        self.assertEqual(id_calls, 1)
        # The retry reuses the orphan's own timestamp rather than reading the
        # clock again; audit emission also reads this clock, so the proof is
        # the recorded value below, not the number of reads.
        self.assertEqual(
            sqlite_store.find_intake_by_capture_id(orphan.capture_id).captured_at,
            CAPTURED_AT_TEXT,
        )
        self.assertEqual(len(list((self.runtime_root / "evidence").iterdir())), 1)
        self.assertEqual(self._snapshot(orphan), before_retry)


if __name__ == "__main__":
    unittest.main()

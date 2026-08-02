from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metis.proposal_evidence import (
    ProposalEvidenceCollision,
    ProposalEvidenceConsistencyError,
    ProposalEvidenceStore,
    ProposalEvidenceWriteError,
)


PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
MODEL_ID = "claude-sonnet-4-6"
PROMPT_VERSION = "propose-v1"
RECEIVED_AT = "2026-08-02T20:00:00Z"
RAW_TEXT = (
    ' {"title":"Review me","body":"Body","reason":"Reason",'
    '"uncertainties":[]}\n'
)
RAW_HASH = hashlib.sha256(RAW_TEXT.encode("utf-8")).hexdigest()


class ProposalEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.store = ProposalEvidenceStore(self.runtime_root)

    def _create(self):
        return self.store.create(
            PROPOSAL_ID,
            CLASSIFICATION_ID,
            CAPTURE_ID,
            RAW_TEXT,
            MODEL_ID,
            PROMPT_VERSION,
            RECEIVED_AT,
        )

    def test_create_preserves_exact_bytes_metadata_and_hash(self) -> None:
        record = self._create()

        self.assertEqual(record.raw_path.read_bytes(), RAW_TEXT.encode("utf-8"))
        self.assertEqual(record.raw_response_hash, RAW_HASH)
        self.assertEqual(
            json.loads(record.meta_path.read_text(encoding="utf-8")),
            {
                "byte_size": len(RAW_TEXT.encode("utf-8")),
                "capture_id": CAPTURE_ID,
                "classification_id": CLASSIFICATION_ID,
                "model_id": MODEL_ID,
                "prompt_version": PROMPT_VERSION,
                "proposal_id": PROPOSAL_ID,
                "received_at": RECEIVED_AT,
                "schema_version": 1,
            },
        )
        self.assertEqual(record.evidence_path, f"proposal-evidence/{PROPOSAL_ID}")

    def test_validate_directory_returns_the_same_record(self) -> None:
        expected = self._create()

        self.assertEqual(self.store.validate_directory(expected.directory), expected)

    def test_create_refuses_symlinked_store_root(self) -> None:
        outside = self.runtime_root / "outside"
        outside.mkdir()
        evidence_root = self.runtime_root / "proposal-evidence"
        evidence_root.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ProposalEvidenceWriteError):
            self._create()

        self.assertEqual(list(outside.iterdir()), [])

    def test_validate_refuses_store_root_replaced_by_symlink(self) -> None:
        self._create()
        evidence_root = self.runtime_root / "proposal-evidence"
        relocated = self.runtime_root / "relocated-evidence"
        evidence_root.rename(relocated)
        evidence_root.symlink_to(relocated, target_is_directory=True)

        with self.assertRaises(ProposalEvidenceConsistencyError):
            self.store.validate_directory(evidence_root / PROPOSAL_ID)

    def test_existing_directory_is_never_overwritten(self) -> None:
        record = self._create()
        before = {path.name: path.read_bytes() for path in record.directory.iterdir()}

        with self.assertRaises(ProposalEvidenceCollision):
            self._create()

        self.assertEqual(
            {path.name: path.read_bytes() for path in record.directory.iterdir()},
            before,
        )

    def test_raw_file_race_is_a_write_failure_without_cleanup(self) -> None:
        directory = self.runtime_root / "proposal-evidence" / PROPOSAL_ID
        raw_path = directory / "raw-response.txt"
        original_mkdir = Path.mkdir

        def mkdir_then_create_raw(path: Path, *args: object, **kwargs: object) -> None:
            original_mkdir(path, *args, **kwargs)
            if path == directory:
                raw_path.write_bytes(b"raced proposal response")

        with patch.object(Path, "mkdir", new=mkdir_then_create_raw):
            with self.assertRaises(ProposalEvidenceWriteError):
                self._create()

        self.assertEqual(raw_path.read_bytes(), b"raced proposal response")
        self.assertFalse((directory / "meta.json").exists())

    def test_partial_extra_and_non_regular_files_fail_closed(self) -> None:
        cases = ("partial", "extra", "raw-symlink", "meta-directory")
        for case in cases:
            with self.subTest(case=case):
                root = self.runtime_root / case
                store = ProposalEvidenceStore(root)
                record = store.create(
                    PROPOSAL_ID,
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_TEXT,
                    MODEL_ID,
                    PROMPT_VERSION,
                    RECEIVED_AT,
                )
                if case == "partial":
                    record.meta_path.unlink()
                elif case == "extra":
                    (record.directory / "extra").write_text("extra")
                elif case == "raw-symlink":
                    record.raw_path.unlink()
                    record.raw_path.symlink_to(record.meta_path)
                else:
                    record.meta_path.unlink()
                    record.meta_path.mkdir()

                with self.assertRaises(ProposalEvidenceConsistencyError):
                    store.validate_directory(record.directory)

    def test_metadata_and_raw_disagreements_fail_closed(self) -> None:
        changes = (
            {"proposal_id": "00000000000000000000000000"},
            {"classification_id": "not-a-ulid"},
            {"capture_id": "not-a-uuid"},
            {"model_id": ""},
            {"prompt_version": 1},
            {"received_at": "not-a-timestamp"},
            {"byte_size": True},
            {"schema_version": 2},
            {"extra": "value"},
        )
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                root = self.runtime_root / f"metadata-{index}"
                store = ProposalEvidenceStore(root)
                record = store.create(
                    PROPOSAL_ID,
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_TEXT,
                    MODEL_ID,
                    PROMPT_VERSION,
                    RECEIVED_AT,
                )
                metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
                metadata.update(change)
                record.meta_path.write_text(json.dumps(metadata), encoding="utf-8")

                with self.assertRaises(ProposalEvidenceConsistencyError):
                    store.validate_directory(record.directory)

    def test_invalid_input_is_rejected_before_root_creation(self) -> None:
        cases = (
            {"proposal_id": "not-a-ulid"},
            {"classification_id": "not-a-ulid"},
            {"capture_id": "not-a-uuid"},
            {"received_at": "2026-08-02T20:00:00+00:00"},
            {"raw_text": "invalid surrogate: \ud800"},
        )
        for change in cases:
            with self.subTest(change=change):
                values = {
                    "proposal_id": PROPOSAL_ID,
                    "classification_id": CLASSIFICATION_ID,
                    "capture_id": CAPTURE_ID,
                    "raw_text": RAW_TEXT,
                    "model_id": MODEL_ID,
                    "prompt_version": PROMPT_VERSION,
                    "received_at": RECEIVED_AT,
                }
                values.update(change)
                with self.assertRaises(ProposalEvidenceWriteError):
                    self.store.create(**values)

        self.assertFalse((self.runtime_root / "proposal-evidence").exists())


if __name__ == "__main__":
    unittest.main()

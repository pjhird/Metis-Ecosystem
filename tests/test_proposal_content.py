from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from metis.proposal_content import (
    ProposalContentCollision,
    ProposalContentConsistencyError,
    ProposalContentStore,
    ProposalContentWriteError,
)


PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
RAW_RESPONSE_HASH = "a" * 64
BODY_BYTES = (
    b"A reviewable proposal.\n\n## Proposal rationale\nReason.\n\n"
    b"## Uncertainties\nNone identified by the proposal model.\n"
)
CONTENT_HASH = hashlib.sha256(BODY_BYTES).hexdigest()


class ProposalContentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.store = ProposalContentStore(self.runtime_root)

    def _create(self):
        return self.store.create(
            PROPOSAL_ID,
            CLASSIFICATION_ID,
            CAPTURE_ID,
            RAW_RESPONSE_HASH,
            BODY_BYTES,
        )

    def test_create_preserves_exact_body_metadata_and_hash(self) -> None:
        record = self._create()

        self.assertEqual(record.body_path.read_bytes(), BODY_BYTES)
        self.assertEqual(record.content_hash, CONTENT_HASH)
        self.assertEqual(record.raw_response_hash, RAW_RESPONSE_HASH)
        self.assertEqual(record.content_path, f"proposal-content/{PROPOSAL_ID}/body.md")
        self.assertEqual(
            json.loads(record.meta_path.read_text(encoding="utf-8")),
            {
                "byte_size": len(BODY_BYTES),
                "capture_id": CAPTURE_ID,
                "classification_id": CLASSIFICATION_ID,
                "content_hash": CONTENT_HASH,
                "proposal_id": PROPOSAL_ID,
                "raw_response_hash": RAW_RESPONSE_HASH,
                "schema_version": 1,
            },
        )

    def test_validate_directory_returns_the_same_record(self) -> None:
        expected = self._create()

        self.assertEqual(self.store.validate_directory(expected.directory), expected)

    def test_existing_directory_is_never_overwritten(self) -> None:
        record = self._create()
        before = {path.name: path.read_bytes() for path in record.directory.iterdir()}

        with self.assertRaises(ProposalContentCollision):
            self._create()

        self.assertEqual(
            {path.name: path.read_bytes() for path in record.directory.iterdir()},
            before,
        )

    def test_partial_extra_symlink_and_hash_disagreement_fail_closed(self) -> None:
        cases = ("partial", "extra", "symlink", "content-hash", "raw-hash")
        for case in cases:
            with self.subTest(case=case):
                root = self.runtime_root / case
                store = ProposalContentStore(root)
                record = store.create(
                    PROPOSAL_ID,
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_RESPONSE_HASH,
                    BODY_BYTES,
                )
                if case == "partial":
                    record.meta_path.unlink()
                elif case == "extra":
                    (record.directory / "extra").write_text("extra")
                elif case == "symlink":
                    record.body_path.unlink()
                    record.body_path.symlink_to(record.meta_path)
                else:
                    metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
                    key = "content_hash" if case == "content-hash" else "raw_response_hash"
                    metadata[key] = (
                        "b" * 64 if case == "content-hash" else "not-a-hash"
                    )
                    record.meta_path.write_text(json.dumps(metadata), encoding="utf-8")

                with self.assertRaises(ProposalContentConsistencyError):
                    store.validate_directory(record.directory)

    def test_invalid_inputs_are_rejected_before_root_creation(self) -> None:
        cases = (
            {"proposal_id": "not-a-ulid"},
            {"classification_id": "not-a-ulid"},
            {"capture_id": "not-a-uuid"},
            {"raw_response_hash": "not-a-hash"},
            {"body_bytes": "not-bytes"},
            {"body_bytes": b"invalid utf8: \xff"},
            {"body_bytes": b""},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                values = {
                    "proposal_id": PROPOSAL_ID,
                    "classification_id": CLASSIFICATION_ID,
                    "capture_id": CAPTURE_ID,
                    "raw_response_hash": RAW_RESPONSE_HASH,
                    "body_bytes": BODY_BYTES,
                }
                values.update(changes)
                with self.assertRaises(ProposalContentWriteError):
                    self.store.create(**values)

        self.assertFalse((self.runtime_root / "proposal-content").exists())


if __name__ == "__main__":
    unittest.main()

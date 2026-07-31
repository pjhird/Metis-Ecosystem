from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from metis.evidence import (
    EvidenceCollision,
    EvidenceConsistencyError,
    EvidenceRecord,
    EvidenceStore,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
CAPTURED_AT = "2026-07-31T20:00:00Z"
RAW_BYTES = "  café\nsecond line\t  ".encode("utf-8")
CONTENT_HASH = "sha256:" + hashlib.sha256(RAW_BYTES).hexdigest()


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.store = EvidenceStore(self.runtime_root)

    def test_create_preserves_raw_bytes_exactly(self) -> None:
        record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(record.raw_path.read_bytes(), RAW_BYTES)

    def test_create_writes_exact_metadata_contract(self) -> None:
        record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))

        self.assertEqual(
            metadata,
            {
                "capture_id": CAPTURE_ID,
                "content_hash": CONTENT_HASH,
                "captured_at": CAPTURED_AT,
                "source_type": "cli-typed",
                "source_detail": "metis capture",
                "byte_size": len(RAW_BYTES),
                "mime_type": "text/plain",
                "schema_version": 1,
            },
        )

    def test_content_hash_matches_fresh_sha256_of_raw(self) -> None:
        record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        fresh = "sha256:" + hashlib.sha256(record.raw_path.read_bytes()).hexdigest()

        self.assertEqual(record.content_hash, fresh)

    def test_validate_directory_returns_the_same_record(self) -> None:
        created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(self.store.validate_directory(created.directory), created)

    def test_find_by_content_hash_returns_none_without_evidence_root(self) -> None:
        self.assertIsNone(self.store.find_by_content_hash(CONTENT_HASH))

    def test_find_by_content_hash_fails_closed_on_dangling_evidence_root_symlink(
        self,
    ) -> None:
        evidence_root = self.runtime_root / "evidence"
        evidence_root.symlink_to(self.runtime_root / "missing-evidence-root")

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_by_content_hash(CONTENT_HASH)

    def test_find_by_content_hash_returns_none_without_a_match(self) -> None:
        self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertIsNone(self.store.find_by_content_hash("sha256:" + "0" * 64))

    def test_find_by_content_hash_returns_one_valid_match(self) -> None:
        expected = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(self.store.find_by_content_hash(CONTENT_HASH), expected)

    def test_find_by_content_hash_rejects_multiple_matches(self) -> None:
        self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        self.store.create(
            "6ba7b810-9dad-41d1-80b4-00c04fd430c8",
            RAW_BYTES,
            CONTENT_HASH,
            "2026-07-31T20:01:00Z",
        )

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_by_content_hash(CONTENT_HASH)

    def test_find_by_content_hash_fails_closed_on_partial_directory(self) -> None:
        partial = self.runtime_root / "evidence" / CAPTURE_ID
        partial.mkdir(parents=True)
        (partial / "raw.txt").write_bytes(RAW_BYTES)

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_by_content_hash(CONTENT_HASH)

    def test_find_by_content_hash_fails_closed_on_non_directory_child(self) -> None:
        evidence_root = self.runtime_root / "evidence"
        evidence_root.mkdir()
        (evidence_root / "unexpected-file").write_text("unexpected", encoding="utf-8")

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_by_content_hash(CONTENT_HASH)

    def test_existing_capture_directory_is_never_overwritten(self) -> None:
        directory = self.runtime_root / "evidence" / CAPTURE_ID
        directory.mkdir(parents=True)
        original = directory / "raw.txt"
        original.write_bytes(b"original")

        with self.assertRaises(EvidenceCollision):
            self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(original.read_bytes(), b"original")
        self.assertFalse((directory / "meta.json").exists())

    def test_validation_rejects_invalid_directory_names(self) -> None:
        created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        for invalid_name in (
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            CAPTURE_ID.upper(),
        ):
            with self.subTest(invalid_name=invalid_name):
                directory = created.directory.with_name(invalid_name)
                created.directory.rename(directory)
                try:
                    self._assert_invalid_without_mutation(directory)
                finally:
                    directory.rename(created.directory)

    def test_validation_rejects_invalid_metadata_key_set(self) -> None:
        for index, (key, value) in enumerate(
            (("mime_type", None), ("unexpected", "value"))
        ):
            with self.subTest(key=key):
                store = EvidenceStore(self.runtime_root / f"key-set-{index}")
                created = store.create(
                    CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT
                )
                metadata = self._metadata(created)
                if key == "mime_type":
                    del metadata[key]
                else:
                    metadata[key] = value
                self._write_metadata(created, metadata)

                self._assert_invalid_without_mutation(created.directory, store)

    def test_validation_rejects_invalid_metadata_types(self) -> None:
        invalid_values = {
            "capture_id": 1,
            "content_hash": 1,
            "captured_at": 1,
            "source_type": 1,
            "source_detail": 1,
            "byte_size": True,
            "mime_type": 1,
            "schema_version": True,
        }

        for index, (key, value) in enumerate(invalid_values.items()):
            with self.subTest(key=key):
                store = EvidenceStore(self.runtime_root / f"metadata-types-{index}")
                created = store.create(
                    CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT
                )
                metadata = self._metadata(created)
                metadata[key] = value
                self._write_metadata(created, metadata)

                self._assert_invalid_without_mutation(created.directory, store)

    def test_validation_rejects_metadata_values_that_disagree_with_evidence(self) -> None:
        invalid_values = {
            "capture_id": "6ba7b810-9dad-41d1-80b4-00c04fd430c8",
            "content_hash": "sha256:" + "0" * 64,
            "byte_size": len(RAW_BYTES) - 1,
            "captured_at": "2026-07-31T20:00:00+00:00",
            "source_type": "file-import",
            "source_detail": "not metis capture",
            "mime_type": "application/json",
            "schema_version": 2,
        }

        for index, (key, value) in enumerate(invalid_values.items()):
            with self.subTest(key=key):
                store = EvidenceStore(self.runtime_root / f"metadata-values-{index}")
                created = store.create(
                    CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT
                )
                metadata = self._metadata(created)
                metadata[key] = value
                self._write_metadata(created, metadata)

                self._assert_invalid_without_mutation(created.directory, store)

    def test_validation_rejects_missing_or_non_regular_evidence_files(self) -> None:
        for index, (name, replacement) in enumerate((
            ("raw.txt", "missing"),
            ("meta.json", "missing"),
            ("raw.txt", "directory"),
            ("meta.json", "directory"),
        )):
            with self.subTest(name=name, replacement=replacement):
                store = EvidenceStore(self.runtime_root / f"file-shape-{index}")
                created = store.create(
                    CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT
                )
                path = created.directory / name
                path.unlink()
                if replacement == "directory":
                    path.mkdir()

                self._assert_invalid_without_mutation(created.directory, store)

    def test_validation_rejects_non_object_or_unreadable_metadata(self) -> None:
        for index, content in enumerate(("[]\n", "{\n", b"\xff")):
            with self.subTest(content=content):
                store = EvidenceStore(self.runtime_root / f"metadata-format-{index}")
                created = store.create(
                    CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT
                )
                if isinstance(content, bytes):
                    created.meta_path.write_bytes(content)
                else:
                    created.meta_path.write_text(content, encoding="utf-8")

                self._assert_invalid_without_mutation(created.directory, store)

    def _metadata(self, record: EvidenceRecord) -> dict[str, object]:
        return json.loads(record.meta_path.read_text(encoding="utf-8"))

    def _write_metadata(
        self, record: EvidenceRecord, metadata: dict[str, object]
    ) -> None:
        record.meta_path.write_text(json.dumps(metadata), encoding="utf-8")

    def _assert_invalid_without_mutation(
        self, directory: Path, store: EvidenceStore | None = None
    ) -> None:
        raw_path = directory / "raw.txt"
        meta_path = directory / "meta.json"
        before = {
            path.name: path.read_bytes()
            for path in (raw_path, meta_path)
            if path.is_file()
        }

        with self.assertRaises(EvidenceConsistencyError):
            (store or self.store).validate_directory(directory)

        after = {
            path.name: path.read_bytes()
            for path in (raw_path, meta_path)
            if path.is_file()
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metis.evidence import (
    EvidenceCollision,
    EvidenceConsistencyError,
    EvidenceError,
    EvidenceRecord,
    EvidenceStore,
    EvidenceWriteError,
)


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
OTHER_CAPTURE_ID = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
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
                "type_pin": None,
                "parent_id": None,
                "schema_version": 3,
            },
        )

    def test_content_hash_matches_fresh_sha256_of_raw(self) -> None:
        record = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        fresh = "sha256:" + hashlib.sha256(record.raw_path.read_bytes()).hexdigest()

        self.assertEqual(record.content_hash, fresh)

    def test_validate_directory_returns_the_same_record(self) -> None:
        created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(self.store.validate_directory(created.directory), created)

    def test_find_all_by_content_hash_is_empty_without_evidence_root(self) -> None:
        self.assertEqual(self.store.find_all_by_content_hash(CONTENT_HASH), ())

    def test_find_all_by_content_hash_fails_closed_on_dangling_evidence_root_symlink(
        self,
    ) -> None:
        evidence_root = self.runtime_root / "evidence"
        evidence_root.symlink_to(self.runtime_root / "missing-evidence-root")

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_all_by_content_hash(CONTENT_HASH)

    def test_find_all_by_content_hash_is_empty_without_a_match(self) -> None:
        self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(
            self.store.find_all_by_content_hash("sha256:" + "0" * 64), ()
        )

    def test_find_all_by_content_hash_returns_one_valid_match(self) -> None:
        expected = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(
            self.store.find_all_by_content_hash(CONTENT_HASH), (expected,)
        )

    def test_find_all_by_content_hash_returns_every_parent(self) -> None:
        """Two parents are legal under ADR-022, so this is no longer a conflict.

        Before ADR-022 a second match was an inconsistency and raised; identical
        text under two different projects is now two captures, not corruption.
        """
        self.store.create(
            CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT, "task", "proj.one"
        )
        self.store.create(
            OTHER_CAPTURE_ID,
            RAW_BYTES,
            CONTENT_HASH,
            "2026-07-31T20:01:00Z",
            "task",
            "proj.two",
        )

        records = self.store.find_all_by_content_hash(CONTENT_HASH)

        self.assertEqual({record.parent_id for record in records}, {"proj.one", "proj.two"})
        self.assertEqual(
            [record.capture_id for record in records], sorted(record.capture_id for record in records)
        )

    def test_find_all_by_content_hash_fails_closed_on_partial_directory(self) -> None:
        partial = self.runtime_root / "evidence" / CAPTURE_ID
        partial.mkdir(parents=True)
        (partial / "raw.txt").write_bytes(RAW_BYTES)

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_all_by_content_hash(CONTENT_HASH)

    def test_find_all_by_content_hash_fails_closed_on_non_directory_child(self) -> None:
        evidence_root = self.runtime_root / "evidence"
        evidence_root.mkdir()
        (evidence_root / "unexpected-file").write_text("unexpected", encoding="utf-8")

        with self.assertRaises(EvidenceConsistencyError):
            self.store.find_all_by_content_hash(CONTENT_HASH)

    def test_existing_capture_directory_is_never_overwritten(self) -> None:
        directory = self.runtime_root / "evidence" / CAPTURE_ID
        directory.mkdir(parents=True)
        original = directory / "raw.txt"
        original.write_bytes(b"original")

        with self.assertRaises(EvidenceCollision):
            self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertEqual(original.read_bytes(), b"original")
        self.assertFalse((directory / "meta.json").exists())

    def test_raw_file_collision_after_directory_creation_is_write_failure(
        self,
    ) -> None:
        directory = self.runtime_root / "evidence" / CAPTURE_ID
        raw_path = directory / "raw.txt"
        original_mkdir = Path.mkdir

        def mkdir_then_create_raw(path: Path, *args: object, **kwargs: object) -> None:
            original_mkdir(path, *args, **kwargs)
            if path == directory:
                raw_path.write_bytes(b"raced raw evidence")

        with patch.object(Path, "mkdir", new=mkdir_then_create_raw):
            with self.assertRaises(EvidenceError) as raised:
                self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertIsInstance(raised.exception, EvidenceWriteError)
        self.assertEqual(raised.exception.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(raw_path.read_bytes(), b"raced raw evidence")
        self.assertFalse((directory / "meta.json").exists())

    def test_metadata_file_collision_after_raw_write_is_write_failure(self) -> None:
        directory = self.runtime_root / "evidence" / CAPTURE_ID
        raw_path = directory / "raw.txt"
        meta_path = directory / "meta.json"
        original_open = Path.open

        def open_with_meta_collision(
            path: Path, *args: object, **kwargs: object
        ) -> object:
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == meta_path and mode == "x" and not meta_path.exists():
                with original_open(meta_path, "w", encoding="utf-8") as stream:
                    stream.write("raced metadata")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", new=open_with_meta_collision):
            with self.assertRaises(EvidenceError) as raised:
                self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)

        self.assertIsInstance(raised.exception, EvidenceWriteError)
        self.assertEqual(raised.exception.evidence_path, f"evidence/{CAPTURE_ID}")
        self.assertEqual(raw_path.read_bytes(), RAW_BYTES)
        self.assertEqual(meta_path.read_text(encoding="utf-8"), "raced metadata")

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
            # 2 and 3 are both readable now, so 4 is the nearest invalid version.
            "schema_version": 4,
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

    def test_a_task_pin_records_its_parent_project(self) -> None:
        record = self.store.create(
            CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT, "task", "proj.weekly-7d4e8eb8"
        )

        self.assertEqual(record.type_pin, "task")
        self.assertEqual(record.parent_id, "proj.weekly-7d4e8eb8")
        metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 3)
        self.assertEqual(metadata["parent_id"], "proj.weekly-7d4e8eb8")

    def test_a_task_pin_without_a_parent_is_refused(self) -> None:
        with self.assertRaises(EvidenceWriteError):
            self.store.create(
                CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT, "task", None
            )

        self.assertFalse((self.runtime_root / "evidence" / CAPTURE_ID).exists())

    def test_v2_evidence_reads_its_parent_goal_as_parent_id(self) -> None:
        """Evidence is immutable (ADR-003), so v2 directories are read, not rewritten.

        This is the only read-time mapping site; no other module parses meta.json.
        """
        directory = self._write_v2_directory(
            type_pin="project", parent_goal_id="goal.abc"
        )

        record = self.store.validate_directory(directory)

        self.assertEqual(record.type_pin, "project")
        self.assertEqual(record.parent_id, "goal.abc")
        self.assertEqual(
            json.loads((directory / "meta.json").read_text(encoding="utf-8"))[
                "parent_goal_id"
            ],
            "goal.abc",
        )

    def test_metadata_without_a_schema_version_fails_closed(self) -> None:
        """The version selects the key set, so it is read before the key set.

        `validate_directory` does not catch `KeyError`; reading the version with
        a subscript would crash here instead of refusing (rule 3).
        """
        created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        metadata = self._metadata(created)
        del metadata["schema_version"]
        self._write_metadata(created, metadata)

        self._assert_invalid_without_mutation(created.directory)

    def test_an_unhashable_schema_version_fails_closed(self) -> None:
        """The version is looked up in a dict, so it must be typed before it is.

        `[]` is valid JSON. Testing membership first raises `TypeError`, which
        `validate_directory` does not catch — a crash where rule 3 requires a
        refusal, the same class as a missing version.
        """
        created = self.store.create(CAPTURE_ID, RAW_BYTES, CONTENT_HASH, CAPTURED_AT)
        metadata = self._metadata(created)
        metadata["schema_version"] = []
        self._write_metadata(created, metadata)

        self._assert_invalid_without_mutation(created.directory)

    def _write_v2_directory(
        self, type_pin: str | None, parent_goal_id: str | None
    ) -> Path:
        """Write immutable pre-ADR-022 evidence exactly as v2 recorded it."""
        directory = self.runtime_root / "evidence" / CAPTURE_ID
        directory.mkdir(parents=True)
        (directory / "raw.txt").write_bytes(RAW_BYTES)
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "capture_id": CAPTURE_ID,
                    "content_hash": CONTENT_HASH,
                    "captured_at": CAPTURED_AT,
                    "source_type": "cli-typed",
                    "source_detail": "metis capture",
                    "byte_size": len(RAW_BYTES),
                    "mime_type": "text/plain",
                    "type_pin": type_pin,
                    "parent_goal_id": parent_goal_id,
                    "schema_version": 2,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return directory

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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metis.classification_evidence import (
    ClassificationEvidenceCollision,
    ClassificationEvidenceConsistencyError,
    ClassificationEvidenceStore,
    ClassificationEvidenceWriteError,
)


CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
MODEL_ID = "claude-sonnet-4-6"
PROMPT_VERSION = "classify-v1"
RECEIVED_AT = "2026-08-01T20:00:00Z"
RAW_TEXT = (
    ' {"candidate_type":"idea","sensitivity":"normal",'
    '"confidence":0.82}\n'
)


class ClassificationEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.store = ClassificationEvidenceStore(self.runtime_root)

    def _create(self):
        return self.store.create(
            CLASSIFICATION_ID,
            CAPTURE_ID,
            RAW_TEXT,
            MODEL_ID,
            PROMPT_VERSION,
            RECEIVED_AT,
        )

    def test_create_preserves_response_bytes_and_exact_metadata(self) -> None:
        record = self._create()

        self.assertEqual(record.raw_path.read_bytes(), RAW_TEXT.encode("utf-8"))
        self.assertEqual(
            json.loads(record.meta_path.read_text(encoding="utf-8")),
            {
                "byte_size": len(RAW_TEXT.encode("utf-8")),
                "capture_id": CAPTURE_ID,
                "classification_id": CLASSIFICATION_ID,
                "model_id": MODEL_ID,
                "prompt_version": PROMPT_VERSION,
                "received_at": RECEIVED_AT,
                "schema_version": 1,
            },
        )
        self.assertEqual(
            record.evidence_path,
            f"classification-evidence/{CLASSIFICATION_ID}",
        )

    def test_validate_directory_returns_the_same_record(self) -> None:
        expected = self._create()

        self.assertEqual(self.store.validate_directory(expected.directory), expected)

    def test_existing_directory_is_never_overwritten(self) -> None:
        record = self._create()
        before = {
            path.name: path.read_bytes() for path in record.directory.iterdir()
        }

        with self.assertRaises(ClassificationEvidenceCollision):
            self._create()

        self.assertEqual(
            {path.name: path.read_bytes() for path in record.directory.iterdir()},
            before,
        )

    def test_raw_file_collision_after_directory_creation_is_write_failure(
        self,
    ) -> None:
        directory = (
            self.runtime_root / "classification-evidence" / CLASSIFICATION_ID
        )
        raw_path = directory / "raw-response.txt"
        original_mkdir = Path.mkdir

        def mkdir_then_create_raw(path: Path, *args: object, **kwargs: object) -> None:
            original_mkdir(path, *args, **kwargs)
            if path == directory:
                raw_path.write_bytes(b"raced response evidence")

        with patch.object(Path, "mkdir", new=mkdir_then_create_raw):
            with self.assertRaises(ClassificationEvidenceWriteError) as raised:
                self._create()

        self.assertEqual(
            raised.exception.evidence_path,
            f"classification-evidence/{CLASSIFICATION_ID}",
        )
        self.assertEqual(raw_path.read_bytes(), b"raced response evidence")
        self.assertFalse((directory / "meta.json").exists())

    def test_metadata_file_collision_after_raw_write_is_write_failure(self) -> None:
        directory = (
            self.runtime_root / "classification-evidence" / CLASSIFICATION_ID
        )
        raw_path = directory / "raw-response.txt"
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
            with self.assertRaises(ClassificationEvidenceWriteError) as raised:
                self._create()

        self.assertEqual(
            raised.exception.evidence_path,
            f"classification-evidence/{CLASSIFICATION_ID}",
        )
        self.assertEqual(raw_path.read_bytes(), RAW_TEXT.encode("utf-8"))
        self.assertEqual(meta_path.read_text(encoding="utf-8"), "raced metadata")

    def test_partial_directory_fails_closed(self) -> None:
        directory = self.runtime_root / "classification-evidence" / CLASSIFICATION_ID
        directory.mkdir(parents=True)
        (directory / "raw-response.txt").write_text(RAW_TEXT, encoding="utf-8")

        with self.assertRaises(ClassificationEvidenceConsistencyError):
            self.store.validate_directory(directory)

    def test_extra_file_fails_closed(self) -> None:
        record = self._create()
        (record.directory / "unexpected.txt").write_text("unexpected")

        with self.assertRaises(ClassificationEvidenceConsistencyError):
            self.store.validate_directory(record.directory)

    def test_validation_rejects_non_regular_response_evidence_files(self) -> None:
        cases = (
            ("raw-response.txt", "directory"),
            ("meta.json", "directory"),
            ("raw-response.txt", "symlink"),
            ("meta.json", "symlink"),
        )
        for index, (name, replacement) in enumerate(cases):
            with self.subTest(name=name, replacement=replacement):
                root = self.runtime_root / f"file-shape-{index}"
                store = ClassificationEvidenceStore(root)
                record = store.create(
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_TEXT,
                    MODEL_ID,
                    PROMPT_VERSION,
                    RECEIVED_AT,
                )
                path = record.directory / name
                path.unlink()
                if replacement == "directory":
                    path.mkdir()
                else:
                    target_name = (
                        "meta.json" if name == "raw-response.txt" else "raw-response.txt"
                    )
                    path.symlink_to(record.directory / target_name)

                with self.assertRaises(ClassificationEvidenceConsistencyError):
                    store.validate_directory(record.directory)

    def test_detectable_metadata_or_raw_disagreement_fails_closed(self) -> None:
        for field, value in (
            ("classification_id", "00000000000000000000000000"),
            ("byte_size", 1),
            ("schema_version", 2),
        ):
            with self.subTest(field=field):
                root = self.runtime_root / field
                store = ClassificationEvidenceStore(root)
                record = store.create(
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_TEXT,
                    MODEL_ID,
                    PROMPT_VERSION,
                    RECEIVED_AT,
                )
                metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
                metadata[field] = value
                record.meta_path.write_text(
                    json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaises(ClassificationEvidenceConsistencyError):
                    store.validate_directory(record.directory)

    def test_invalid_metadata_key_set_and_types_fail_closed(self) -> None:
        cases = (
            {"extra": "value"},
            {"capture_id": "not-a-uuid"},
            {"model_id": ""},
            {"prompt_version": 1},
            {"received_at": "not-a-timestamp"},
            {"byte_size": True},
        )

        for changes in cases:
            with self.subTest(changes=changes):
                root = self.runtime_root / str(len(list(self.runtime_root.iterdir())))
                store = ClassificationEvidenceStore(root)
                record = store.create(
                    CLASSIFICATION_ID,
                    CAPTURE_ID,
                    RAW_TEXT,
                    MODEL_ID,
                    PROMPT_VERSION,
                    RECEIVED_AT,
                )
                metadata = json.loads(record.meta_path.read_text(encoding="utf-8"))
                metadata.update(changes)
                record.meta_path.write_text(json.dumps(metadata), encoding="utf-8")

                with self.assertRaises(ClassificationEvidenceConsistencyError):
                    store.validate_directory(record.directory)

    def test_invalid_identifiers_and_timestamp_are_rejected_before_creation(self) -> None:
        cases = (
            {"classification_id": "not-a-ulid"},
            {"capture_id": "not-a-uuid"},
            {"received_at": "2026-08-01T20:00:00+00:00"},
        )

        for changes in cases:
            with self.subTest(changes=changes):
                values = {
                    "classification_id": CLASSIFICATION_ID,
                    "capture_id": CAPTURE_ID,
                    "raw_text": RAW_TEXT,
                    "model_id": MODEL_ID,
                    "prompt_version": PROMPT_VERSION,
                    "received_at": RECEIVED_AT,
                }
                values.update(changes)
                with self.assertRaises(ClassificationEvidenceWriteError):
                    self.store.create(**values)

        self.assertFalse((self.runtime_root / "classification-evidence").exists())

    def test_unencodable_response_is_rejected_before_creation(self) -> None:
        with self.assertRaises(ClassificationEvidenceWriteError):
            self.store.create(
                CLASSIFICATION_ID,
                CAPTURE_ID,
                "invalid surrogate: \ud800",
                MODEL_ID,
                PROMPT_VERSION,
                RECEIVED_AT,
            )

        self.assertFalse((self.runtime_root / "classification-evidence").exists())


if __name__ == "__main__":
    unittest.main()

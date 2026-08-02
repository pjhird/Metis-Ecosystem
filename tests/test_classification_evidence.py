from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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

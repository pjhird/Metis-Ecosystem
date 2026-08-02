"""Append-only storage for raw model classification responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from .identifiers import is_ulid


METADATA_KEYS = {
    "classification_id",
    "capture_id",
    "model_id",
    "prompt_version",
    "received_at",
    "byte_size",
    "schema_version",
}


@dataclass(frozen=True)
class ClassificationEvidenceRecord:
    classification_id: str
    capture_id: str
    model_id: str
    prompt_version: str
    received_at: str
    evidence_path: str
    directory: Path
    raw_path: Path
    meta_path: Path


class ClassificationEvidenceError(RuntimeError):
    def __init__(self, message: str, evidence_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path


class ClassificationEvidenceCollision(ClassificationEvidenceError):
    """Raised when an exclusive response-evidence target already exists."""


class ClassificationEvidenceConsistencyError(ClassificationEvidenceError):
    """Raised when existing response evidence cannot be trusted."""


class ClassificationEvidenceWriteError(ClassificationEvidenceError):
    """Raised when response evidence cannot be finalized."""


class ClassificationEvidenceStore:
    def __init__(self, runtime_root: Path) -> None:
        self._evidence_root = Path(runtime_root) / "classification-evidence"

    def create(
        self,
        classification_id: str,
        capture_id: str,
        raw_text: str,
        model_id: str,
        prompt_version: str,
        received_at: str,
    ) -> ClassificationEvidenceRecord:
        evidence_path = f"classification-evidence/{classification_id}"
        try:
            raw_bytes = raw_text.encode("utf-8")
            self._validate_values(
                classification_id,
                capture_id,
                model_id,
                prompt_version,
                received_at,
            )
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as error:
            raise ClassificationEvidenceWriteError(
                f"invalid classification evidence input: {error}", evidence_path
            ) from error

        directory = self._evidence_root / classification_id
        raw_path = directory / "raw-response.txt"
        meta_path = directory / "meta.json"
        metadata = {
            "classification_id": classification_id,
            "capture_id": capture_id,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "received_at": received_at,
            "byte_size": len(raw_bytes),
            "schema_version": 1,
        }

        try:
            self._evidence_root.mkdir(parents=True, exist_ok=True)
            directory.mkdir(exist_ok=False)
        except FileExistsError as error:
            raise ClassificationEvidenceCollision(
                f"classification evidence target already exists: {directory}",
                evidence_path,
            ) from error
        except OSError as error:
            raise ClassificationEvidenceWriteError(
                f"classification evidence directory creation failed: {error}",
                evidence_path,
            ) from error

        try:
            with raw_path.open("xb") as stream:
                stream.write(raw_bytes)
            with meta_path.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except OSError as error:
            raise ClassificationEvidenceWriteError(
                f"classification evidence write failed: {error}", evidence_path
            ) from error

        return self._record(
            classification_id,
            capture_id,
            model_id,
            prompt_version,
            received_at,
            directory,
        )

    def validate_directory(self, directory: Path) -> ClassificationEvidenceRecord:
        directory = Path(directory)
        evidence_path = f"classification-evidence/{directory.name}"
        try:
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("classification evidence target is not a directory")
            if not is_ulid(directory.name):
                raise ValueError("classification evidence directory is not a ULID")
            children = {path.name: path for path in directory.iterdir()}
            if set(children) != {"raw-response.txt", "meta.json"}:
                raise ValueError("classification evidence file set is invalid")
            raw_path = children["raw-response.txt"]
            meta_path = children["meta.json"]
            if any(not path.is_file() or path.is_symlink() for path in children.values()):
                raise ValueError("classification evidence contains a non-regular file")

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
                raise ValueError("classification evidence metadata keys are invalid")
            self._validate_metadata(metadata, directory.name)
            raw_bytes = raw_path.read_bytes()
            raw_bytes.decode("utf-8")
            if metadata["byte_size"] != len(raw_bytes):
                raise ValueError("classification evidence byte size disagrees")
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ClassificationEvidenceConsistencyError(
                f"classification evidence is inconsistent: {error}", evidence_path
            ) from error

        return self._record(
            metadata["classification_id"],
            metadata["capture_id"],
            metadata["model_id"],
            metadata["prompt_version"],
            metadata["received_at"],
            directory,
        )

    def _validate_metadata(self, metadata: dict, classification_id: str) -> None:
        if metadata["classification_id"] != classification_id:
            raise ValueError("classification ID disagrees with directory")
        self._validate_values(
            metadata["classification_id"],
            metadata["capture_id"],
            metadata["model_id"],
            metadata["prompt_version"],
            metadata["received_at"],
        )
        byte_size = metadata["byte_size"]
        if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
            raise ValueError("byte size is invalid")
        if metadata["schema_version"] != 1 or isinstance(
            metadata["schema_version"], bool
        ):
            raise ValueError("schema version is invalid")

    def _validate_values(
        self,
        classification_id: str,
        capture_id: str,
        model_id: str,
        prompt_version: str,
        received_at: str,
    ) -> None:
        if not is_ulid(classification_id):
            raise ValueError("classification ID is not a canonical ULID")
        capture_uuid = UUID(capture_id)
        if capture_uuid.version != 4 or str(capture_uuid) != capture_id:
            raise ValueError("capture ID is not a canonical UUID4")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model ID is invalid")
        if not isinstance(prompt_version, str) or not prompt_version:
            raise ValueError("prompt version is invalid")
        if not isinstance(received_at, str):
            raise ValueError("received timestamp is invalid")
        parsed = datetime.strptime(received_at, "%Y-%m-%dT%H:%M:%SZ")
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != received_at:
            raise ValueError("received timestamp is not canonical UTC")

    def _record(
        self,
        classification_id: str,
        capture_id: str,
        model_id: str,
        prompt_version: str,
        received_at: str,
        directory: Path,
    ) -> ClassificationEvidenceRecord:
        return ClassificationEvidenceRecord(
            classification_id=classification_id,
            capture_id=capture_id,
            model_id=model_id,
            prompt_version=prompt_version,
            received_at=received_at,
            evidence_path=f"classification-evidence/{classification_id}",
            directory=directory,
            raw_path=directory / "raw-response.txt",
            meta_path=directory / "meta.json",
        )

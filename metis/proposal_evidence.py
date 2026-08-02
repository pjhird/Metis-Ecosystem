"""Append-only storage for raw model proposal responses."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from .identifiers import is_ulid


METADATA_KEYS = {
    "proposal_id",
    "classification_id",
    "capture_id",
    "model_id",
    "prompt_version",
    "received_at",
    "byte_size",
    "schema_version",
}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("proposal evidence metadata key is duplicated")
        result[key] = value
    return result


@dataclass(frozen=True)
class ProposalEvidenceRecord:
    proposal_id: str
    classification_id: str
    capture_id: str
    model_id: str
    prompt_version: str
    received_at: str
    evidence_path: str
    raw_response_hash: str
    directory: Path
    raw_path: Path
    meta_path: Path


class ProposalEvidenceError(RuntimeError):
    def __init__(self, message: str, evidence_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path


class ProposalEvidenceCollision(ProposalEvidenceError):
    """Raised when an exclusive proposal-evidence target already exists."""


class ProposalEvidenceConsistencyError(ProposalEvidenceError):
    """Raised when existing proposal evidence cannot be trusted."""


class ProposalEvidenceWriteError(ProposalEvidenceError):
    """Raised when proposal evidence cannot be finalized."""


class ProposalEvidenceStore:
    def __init__(self, runtime_root: Path) -> None:
        self._runtime_root = Path(runtime_root)
        self._evidence_root = self._runtime_root / "proposal-evidence"

    def create(
        self,
        proposal_id: str,
        classification_id: str,
        capture_id: str,
        raw_text: str,
        model_id: str,
        prompt_version: str,
        received_at: str,
    ) -> ProposalEvidenceRecord:
        evidence_path = f"proposal-evidence/{proposal_id}"
        try:
            raw_bytes = raw_text.encode("utf-8")
            self._validate_values(
                proposal_id,
                classification_id,
                capture_id,
                model_id,
                prompt_version,
                received_at,
            )
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as error:
            raise ProposalEvidenceWriteError(
                f"invalid proposal evidence input: {error}",
                evidence_path,
            ) from error

        directory = self._evidence_root / proposal_id
        raw_path = directory / "raw-response.txt"
        meta_path = directory / "meta.json"
        metadata = {
            "proposal_id": proposal_id,
            "classification_id": classification_id,
            "capture_id": capture_id,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "received_at": received_at,
            "byte_size": len(raw_bytes),
            "schema_version": 1,
        }
        try:
            self._prepare_root()
            directory.mkdir(exist_ok=False)
        except FileExistsError as error:
            raise ProposalEvidenceCollision(
                f"proposal evidence target already exists: {directory}",
                evidence_path,
            ) from error
        except (OSError, ValueError) as error:
            raise ProposalEvidenceWriteError(
                f"proposal evidence directory creation failed: {error}",
                evidence_path,
            ) from error

        try:
            with raw_path.open("xb") as stream:
                stream.write(raw_bytes)
            with meta_path.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except OSError as error:
            raise ProposalEvidenceWriteError(
                f"proposal evidence write failed: {error}",
                evidence_path,
            ) from error

        return self._record(metadata, raw_bytes, directory)

    def validate_directory(self, directory: Path) -> ProposalEvidenceRecord:
        directory = Path(directory)
        evidence_path = f"proposal-evidence/{directory.name}"
        try:
            self._validate_directory(directory)
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("proposal evidence target is not a directory")
            if not is_ulid(directory.name):
                raise ValueError("proposal evidence directory is not a ULID")
            children = {path.name: path for path in directory.iterdir()}
            if set(children) != {"raw-response.txt", "meta.json"}:
                raise ValueError("proposal evidence file set is invalid")
            if any(
                not path.is_file() or path.is_symlink()
                for path in children.values()
            ):
                raise ValueError("proposal evidence contains a non-regular file")
            metadata = json.loads(
                children["meta.json"].read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
            if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
                raise ValueError("proposal evidence metadata keys are invalid")
            self._validate_metadata(metadata, directory.name)
            raw_bytes = children["raw-response.txt"].read_bytes()
            raw_bytes.decode("utf-8")
            if metadata["byte_size"] != len(raw_bytes):
                raise ValueError("proposal evidence byte size disagrees")
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            raise ProposalEvidenceConsistencyError(
                f"proposal evidence is inconsistent: {error}",
                evidence_path,
            ) from error

        return self._record(metadata, raw_bytes, directory)

    def _prepare_root(self) -> None:
        if self._runtime_root.is_symlink() or (
            self._runtime_root.exists() and not self._runtime_root.is_dir()
        ):
            raise ValueError("runtime root is not a real directory")
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        if self._evidence_root.is_symlink() or (
            self._evidence_root.exists() and not self._evidence_root.is_dir()
        ):
            raise ValueError("proposal evidence root is not a real directory")
        self._evidence_root.mkdir(exist_ok=True)
        if self._evidence_root.is_symlink() or not self._evidence_root.is_dir():
            raise ValueError("proposal evidence root is not a real directory")

    def _validate_directory(self, directory: Path) -> None:
        if self._runtime_root.is_symlink() or not self._runtime_root.is_dir():
            raise ValueError("runtime root is not a real directory")
        if self._evidence_root.is_symlink() or not self._evidence_root.is_dir():
            raise ValueError("proposal evidence root is not a real directory")
        if directory.parent != self._evidence_root:
            raise ValueError("proposal evidence target is outside the store root")

    def _validate_metadata(self, metadata: dict, proposal_id: str) -> None:
        if metadata["proposal_id"] != proposal_id:
            raise ValueError("proposal ID disagrees with directory")
        self._validate_values(
            metadata["proposal_id"],
            metadata["classification_id"],
            metadata["capture_id"],
            metadata["model_id"],
            metadata["prompt_version"],
            metadata["received_at"],
        )
        byte_size = metadata["byte_size"]
        if type(byte_size) is not int or byte_size < 0:
            raise ValueError("byte size is invalid")
        if type(metadata["schema_version"]) is not int:
            raise ValueError("schema version is invalid")
        if metadata["schema_version"] != 1:
            raise ValueError("schema version is invalid")

    def _validate_values(
        self,
        proposal_id: str,
        classification_id: str,
        capture_id: str,
        model_id: str,
        prompt_version: str,
        received_at: str,
    ) -> None:
        if not is_ulid(proposal_id):
            raise ValueError("proposal ID is not a canonical ULID")
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
        metadata: dict,
        raw_bytes: bytes,
        directory: Path,
    ) -> ProposalEvidenceRecord:
        proposal_id = metadata["proposal_id"]
        return ProposalEvidenceRecord(
            proposal_id=proposal_id,
            classification_id=metadata["classification_id"],
            capture_id=metadata["capture_id"],
            model_id=metadata["model_id"],
            prompt_version=metadata["prompt_version"],
            received_at=metadata["received_at"],
            evidence_path=f"proposal-evidence/{proposal_id}",
            raw_response_hash=hashlib.sha256(raw_bytes).hexdigest(),
            directory=directory,
            raw_path=directory / "raw-response.txt",
            meta_path=directory / "meta.json",
        )

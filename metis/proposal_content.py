"""Append-only storage for canonical proposal content."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from .identifiers import is_ulid


METADATA_KEYS = {
    "proposal_id",
    "classification_id",
    "capture_id",
    "raw_response_hash",
    "content_hash",
    "byte_size",
    "schema_version",
}


@dataclass(frozen=True)
class ProposalContentRecord:
    proposal_id: str
    classification_id: str
    capture_id: str
    raw_response_hash: str
    content_hash: str
    byte_size: int
    content_path: str
    directory: Path
    body_path: Path
    meta_path: Path


class ProposalContentError(RuntimeError):
    def __init__(self, message: str, content_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.content_path = content_path


class ProposalContentCollision(ProposalContentError):
    """Raised when an exclusive proposal-content target already exists."""


class ProposalContentConsistencyError(ProposalContentError):
    """Raised when existing canonical proposal content cannot be trusted."""


class ProposalContentWriteError(ProposalContentError):
    """Raised when canonical proposal content cannot be finalized."""


class ProposalContentStore:
    def __init__(self, runtime_root: Path) -> None:
        self._content_root = Path(runtime_root) / "proposal-content"

    def create(
        self,
        proposal_id: str,
        classification_id: str,
        capture_id: str,
        raw_response_hash: str,
        body_bytes: bytes,
    ) -> ProposalContentRecord:
        content_path = f"proposal-content/{proposal_id}/body.md"
        try:
            self._validate_values(
                proposal_id,
                classification_id,
                capture_id,
                raw_response_hash,
            )
            if type(body_bytes) is not bytes or not body_bytes:
                raise ValueError("body bytes are invalid")
            body_bytes.decode("utf-8")
        except (TypeError, UnicodeError, ValueError) as error:
            raise ProposalContentWriteError(
                f"invalid proposal content input: {error}",
                content_path,
            ) from error

        content_hash = hashlib.sha256(body_bytes).hexdigest()
        directory = self._content_root / proposal_id
        body_path = directory / "body.md"
        meta_path = directory / "meta.json"
        metadata = {
            "proposal_id": proposal_id,
            "classification_id": classification_id,
            "capture_id": capture_id,
            "raw_response_hash": raw_response_hash,
            "content_hash": content_hash,
            "byte_size": len(body_bytes),
            "schema_version": 1,
        }
        try:
            self._content_root.mkdir(parents=True, exist_ok=True)
            directory.mkdir(exist_ok=False)
        except FileExistsError as error:
            raise ProposalContentCollision(
                f"proposal content target already exists: {directory}",
                content_path,
            ) from error
        except OSError as error:
            raise ProposalContentWriteError(
                f"proposal content directory creation failed: {error}",
                content_path,
            ) from error
        try:
            with body_path.open("xb") as stream:
                stream.write(body_bytes)
            with meta_path.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except OSError as error:
            raise ProposalContentWriteError(
                f"proposal content write failed: {error}",
                content_path,
            ) from error
        return self._record(metadata, directory)

    def validate_directory(self, directory: Path) -> ProposalContentRecord:
        directory = Path(directory)
        content_path = f"proposal-content/{directory.name}/body.md"
        try:
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("proposal content target is not a directory")
            if not is_ulid(directory.name):
                raise ValueError("proposal content directory is not a ULID")
            children = {path.name: path for path in directory.iterdir()}
            if set(children) != {"body.md", "meta.json"}:
                raise ValueError("proposal content file set is invalid")
            if any(
                not path.is_file() or path.is_symlink()
                for path in children.values()
            ):
                raise ValueError("proposal content contains a non-regular file")
            metadata = json.loads(
                children["meta.json"].read_text(encoding="utf-8")
            )
            if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
                raise ValueError("proposal content metadata keys are invalid")
            self._validate_metadata(metadata, directory.name)
            body_bytes = children["body.md"].read_bytes()
            body_bytes.decode("utf-8")
            if not body_bytes or metadata["byte_size"] != len(body_bytes):
                raise ValueError("proposal content byte size disagrees")
            if metadata["content_hash"] != hashlib.sha256(body_bytes).hexdigest():
                raise ValueError("proposal content hash disagrees")
        except (
            OSError,
            UnicodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ProposalContentConsistencyError(
                f"proposal content is inconsistent: {error}",
                content_path,
            ) from error
        return self._record(metadata, directory)

    def _validate_metadata(self, metadata: dict, proposal_id: str) -> None:
        if metadata["proposal_id"] != proposal_id:
            raise ValueError("proposal ID disagrees with directory")
        self._validate_values(
            metadata["proposal_id"],
            metadata["classification_id"],
            metadata["capture_id"],
            metadata["raw_response_hash"],
        )
        if not _is_hash(metadata["content_hash"]):
            raise ValueError("content hash is invalid")
        if type(metadata["byte_size"]) is not int or metadata["byte_size"] < 0:
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
        raw_response_hash: str,
    ) -> None:
        if not is_ulid(proposal_id):
            raise ValueError("proposal ID is not a canonical ULID")
        if not is_ulid(classification_id):
            raise ValueError("classification ID is not a canonical ULID")
        capture_uuid = UUID(capture_id)
        if capture_uuid.version != 4 or str(capture_uuid) != capture_id:
            raise ValueError("capture ID is not a canonical UUID4")
        if not _is_hash(raw_response_hash):
            raise ValueError("raw response hash is invalid")

    def _record(self, metadata: dict, directory: Path) -> ProposalContentRecord:
        proposal_id = metadata["proposal_id"]
        return ProposalContentRecord(
            proposal_id=proposal_id,
            classification_id=metadata["classification_id"],
            capture_id=metadata["capture_id"],
            raw_response_hash=metadata["raw_response_hash"],
            content_hash=metadata["content_hash"],
            byte_size=metadata["byte_size"],
            content_path=f"proposal-content/{proposal_id}/body.md",
            directory=directory,
            body_path=directory / "body.md",
            meta_path=directory / "meta.json",
        )


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )

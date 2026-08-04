"""Immutable source-evidence filesystem storage."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


METADATA_KEYS = {
    "capture_id",
    "content_hash",
    "captured_at",
    "source_type",
    "source_detail",
    "byte_size",
    "mime_type",
    "type_pin",
    "parent_goal_id",
    "schema_version",
}

# The human's planning intent, pinned at capture and never chosen by a model (ADR-021).
PIN_TYPES = frozenset({"goal", "project"})

# ponytail: mirrors draft_notes.LINK_TARGET; kept local so persistence does not
# import the vault layer. Keep the two in step if the id grammar ever changes.
PARENT_GOAL_ID = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class EvidenceRecord:
    capture_id: str
    content_hash: str
    captured_at: str
    evidence_path: str
    directory: Path
    raw_path: Path
    meta_path: Path
    type_pin: Optional[str] = None
    parent_goal_id: Optional[str] = None


class EvidenceError(RuntimeError):
    def __init__(self, message: str, evidence_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path


class EvidenceCollision(EvidenceError):
    """Raised when exclusive evidence creation finds an existing target."""


class EvidenceConsistencyError(EvidenceError):
    """Raised when existing evidence cannot be trusted or uniquely resolved."""


class EvidenceWriteError(EvidenceError):
    """Raised when new evidence cannot be finalized."""


class EvidenceStore:
    """Creates and validates append-only evidence directories."""

    def __init__(self, runtime_root: Path) -> None:
        self._evidence_root = runtime_root / "evidence"

    def create(
        self,
        capture_id: str,
        raw_bytes: bytes,
        content_hash: str,
        captured_at: str,
        type_pin: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
    ) -> EvidenceRecord:
        evidence_path = f"evidence/{capture_id}"
        directory = self._evidence_root / capture_id
        raw_path = directory / "raw.txt"
        meta_path = directory / "meta.json"
        metadata = {
            "capture_id": capture_id,
            "content_hash": content_hash,
            "captured_at": captured_at,
            "source_type": "cli-typed",
            "source_detail": "metis capture",
            "byte_size": len(raw_bytes),
            "mime_type": "text/plain",
            "type_pin": type_pin,
            "parent_goal_id": parent_goal_id,
            "schema_version": 2,
        }

        try:
            self._evidence_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise EvidenceWriteError(
                f"evidence root creation failed for {directory}: {error}",
                evidence_path,
            ) from error

        try:
            directory.mkdir(exist_ok=False)
        except FileExistsError as error:
            raise EvidenceCollision(
                f"evidence target already exists: {directory}", evidence_path
            ) from error
        except OSError as error:
            raise EvidenceWriteError(
                f"evidence directory creation failed for {directory}: {error}",
                evidence_path,
            ) from error

        try:
            with raw_path.open("xb") as stream:
                stream.write(raw_bytes)
            with meta_path.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except OSError as error:
            raise EvidenceWriteError(
                f"evidence write failed for {directory}: {error}", evidence_path
            ) from error

        return EvidenceRecord(
            capture_id=capture_id,
            content_hash=content_hash,
            captured_at=captured_at,
            evidence_path=evidence_path,
            directory=directory,
            raw_path=raw_path,
            meta_path=meta_path,
            type_pin=type_pin,
            parent_goal_id=parent_goal_id,
        )

    def validate_directory(self, directory: Path) -> EvidenceRecord:
        """Return a record only when immutable evidence is internally consistent."""
        evidence_path = f"evidence/{directory.name}"
        try:
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("evidence target is not a directory")

            capture_id = self._validated_capture_id(directory)
            raw_path = directory / "raw.txt"
            meta_path = directory / "meta.json"
            for path in (raw_path, meta_path):
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"evidence file is missing or not regular: {path.name}")

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            self._validate_metadata(metadata, capture_id)
            raw_bytes = raw_path.read_bytes()
            content_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
            if metadata["content_hash"] != content_hash:
                raise ValueError("content hash does not match raw evidence")
            if metadata["byte_size"] != len(raw_bytes):
                raise ValueError("byte size does not match raw evidence")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            raise EvidenceConsistencyError(
                f"evidence consistency check failed for {directory}: {error}",
                evidence_path,
            ) from error

        return EvidenceRecord(
            capture_id=capture_id,
            content_hash=content_hash,
            captured_at=metadata["captured_at"],
            evidence_path=evidence_path,
            directory=directory,
            raw_path=raw_path,
            meta_path=meta_path,
            type_pin=metadata["type_pin"],
            parent_goal_id=metadata["parent_goal_id"],
        )

    def find_by_content_hash(self, content_hash: str) -> Optional[EvidenceRecord]:
        """Find exactly one valid evidence record with the requested content hash."""
        try:
            if self._evidence_root.is_symlink():
                raise ValueError("evidence root is not a directory")
            if not self._evidence_root.exists():
                return None
            if not self._evidence_root.is_dir():
                raise ValueError("evidence root is not a directory")
            children = sorted(self._evidence_root.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError) as error:
            raise EvidenceConsistencyError(
                f"evidence consistency check failed for {self._evidence_root}: {error}",
                "evidence",
            ) from error

        records = [self.validate_directory(child) for child in children]
        matches = [record for record in records if record.content_hash == content_hash]
        if len(matches) > 1:
            raise EvidenceConsistencyError(
                f"multiple evidence records match content hash: {content_hash}"
            )
        return matches[0] if matches else None

    def _validated_capture_id(self, directory: Path) -> str:
        from uuid import UUID

        capture_id = directory.name
        parsed = UUID(capture_id)
        if parsed.version != 4 or str(parsed) != capture_id:
            raise ValueError("directory name is not a canonical UUID4")
        return capture_id

    def _validate_metadata(self, metadata: object, capture_id: str) -> None:
        if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
            raise ValueError("metadata keys do not match the evidence contract")
        expected_types = {
            "capture_id": str,
            "content_hash": str,
            "captured_at": str,
            "source_type": str,
            "source_detail": str,
            "byte_size": int,
            "mime_type": str,
            "schema_version": int,
        }
        for key, expected_type in expected_types.items():
            if type(metadata[key]) is not expected_type:
                raise ValueError(f"metadata {key} has an invalid type")

        self._validate_pin(metadata["type_pin"], metadata["parent_goal_id"])

        timestamp = metadata["captured_at"]
        if not timestamp.endswith("Z"):
            raise ValueError("captured_at is not a UTC Z timestamp")
        parsed_timestamp = datetime.fromisoformat(timestamp[:-1] + "+00:00")
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != timedelta(0):
            raise ValueError("captured_at is not UTC")
        if metadata["capture_id"] != capture_id:
            raise ValueError("metadata capture_id does not match directory")
        if metadata["source_type"] != "cli-typed":
            raise ValueError("metadata source_type is invalid")
        if metadata["source_detail"] != "metis capture":
            raise ValueError("metadata source_detail is invalid")
        if metadata["mime_type"] != "text/plain":
            raise ValueError("metadata mime_type is invalid")
        if metadata["schema_version"] != 2:
            raise ValueError("metadata schema_version is invalid")

    @staticmethod
    def _validate_pin(type_pin: object, parent_goal_id: object) -> None:
        """A pin is either absent or a complete, well-formed planning intent."""
        if type_pin is not None and type_pin not in PIN_TYPES:
            raise ValueError("metadata type_pin is invalid")
        if parent_goal_id is not None:
            if type(parent_goal_id) is not str:
                raise ValueError("metadata parent_goal_id has an invalid type")
            if PARENT_GOAL_ID.fullmatch(parent_goal_id) is None:
                raise ValueError("metadata parent_goal_id is invalid")
        # A project names its parent goal; nothing else may carry one (ADR-021).
        if (type_pin == "project") != (parent_goal_id is not None):
            raise ValueError("metadata type_pin and parent_goal_id disagree")

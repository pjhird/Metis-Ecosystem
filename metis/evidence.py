"""Immutable source-evidence filesystem storage."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


_METADATA_KEYS_COMMON = {
    "capture_id",
    "content_hash",
    "captured_at",
    "source_type",
    "source_detail",
    "byte_size",
    "mime_type",
    "type_pin",
    "schema_version",
}

# v2 named the parent `parent_goal_id` because only a project could carry one.
# That evidence is immutable (ADR-003), so the key is read, never rewritten.
METADATA_KEYS_V2 = _METADATA_KEYS_COMMON | {"parent_goal_id"}
METADATA_KEYS_V3 = _METADATA_KEYS_COMMON | {"parent_id"}
SCHEMA_VERSIONS = {2: ("parent_goal_id", METADATA_KEYS_V2), 3: ("parent_id", METADATA_KEYS_V3)}
SCHEMA_VERSION = 3

# The human's planning intent, pinned at capture and never chosen by a model
# (ADR-021). A planning task exists only under a pin (ADR-022).
PIN_TYPES = frozenset({"goal", "project", "task"})

# A goal has nothing above it; a project names a goal and a task names a project.
PARENT_REQUIRED = frozenset({"project", "task"})

# ponytail: mirrors draft_notes.LINK_TARGET; kept local so persistence does not
# import the vault layer. Keep the two in step if the id grammar ever changes.
PARENT_ID = re.compile(r"[A-Za-z0-9._-]+")


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
    parent_id: Optional[str] = None


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
        parent_id: Optional[str] = None,
    ) -> EvidenceRecord:
        evidence_path = f"evidence/{capture_id}"
        directory = self._evidence_root / capture_id
        raw_path = directory / "raw.txt"
        meta_path = directory / "meta.json"
        # Refuse an incoherent pin before anything reaches disk, so a bad pin
        # never leaves a half-written evidence directory behind.
        try:
            self._validate_pin(type_pin, parent_id)
        except ValueError as error:
            raise EvidenceWriteError(
                f"evidence pin is invalid for {directory}: {error}", evidence_path
            ) from error
        metadata = {
            "capture_id": capture_id,
            "content_hash": content_hash,
            "captured_at": captured_at,
            "source_type": "cli-typed",
            "source_detail": "metis capture",
            "byte_size": len(raw_bytes),
            "mime_type": "text/plain",
            "type_pin": type_pin,
            "parent_id": parent_id,
            "schema_version": SCHEMA_VERSION,
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
            parent_id=parent_id,
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
            # The only v2 -> v3 mapping site in the system. Immutable v2 evidence
            # keeps its `parent_goal_id` key; callers only ever see `parent_id`.
            parent_id=metadata.get("parent_id", metadata.get("parent_goal_id")),
        )

    def find_all_by_content_hash(self, content_hash: str) -> Tuple[EvidenceRecord, ...]:
        """Find every valid evidence record with the requested content hash.

        Identical text under two different parents is two captures under
        ADR-022, so more than one match is legal and no longer an inconsistency.
        """
        try:
            if self._evidence_root.is_symlink():
                raise ValueError("evidence root is not a directory")
            if not self._evidence_root.exists():
                return ()
            if not self._evidence_root.is_dir():
                raise ValueError("evidence root is not a directory")
            children = sorted(self._evidence_root.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError) as error:
            raise EvidenceConsistencyError(
                f"evidence consistency check failed for {self._evidence_root}: {error}",
                "evidence",
            ) from error

        records = [self.validate_directory(child) for child in children]
        return tuple(
            sorted(
                (record for record in records if record.content_hash == content_hash),
                key=lambda record: record.capture_id,
            )
        )

    def _validated_capture_id(self, directory: Path) -> str:
        from uuid import UUID

        capture_id = directory.name
        parsed = UUID(capture_id)
        if parsed.version != 4 or str(parsed) != capture_id:
            raise ValueError("directory name is not a canonical UUID4")
        return capture_id

    def _validate_metadata(self, metadata: object, capture_id: str) -> None:
        # The version selects the key set, so it is read before the key set is
        # compared — and with `.get`, because `validate_directory` catches
        # `ValueError` but not `KeyError` and must refuse rather than crash.
        if not isinstance(metadata, dict):
            raise ValueError("metadata is not an object")
        # Type before membership: SCHEMA_VERSIONS is a dict, so testing an
        # unhashable value first would raise TypeError instead of refusing.
        version = metadata.get("schema_version")
        if type(version) is not int or version not in SCHEMA_VERSIONS:
            raise ValueError("metadata schema_version is invalid")
        parent_key, expected_keys = SCHEMA_VERSIONS[version]
        if set(metadata) != expected_keys:
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

        self._validate_pin(metadata["type_pin"], metadata[parent_key])

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

    @staticmethod
    def _validate_pin(type_pin: object, parent_id: object) -> None:
        """A pin is either absent or a complete, well-formed planning intent."""
        if type_pin is not None and type_pin not in PIN_TYPES:
            raise ValueError("metadata type_pin is invalid")
        if parent_id is not None:
            if type(parent_id) is not str:
                raise ValueError("metadata parent_id has an invalid type")
            if PARENT_ID.fullmatch(parent_id) is None:
                raise ValueError("metadata parent_id is invalid")
        # A project names its goal and a task names its project; nothing else
        # may carry a parent (ADR-021, ADR-022).
        if (type_pin in PARENT_REQUIRED) != (parent_id is not None):
            raise ValueError("metadata type_pin and parent_id disagree")

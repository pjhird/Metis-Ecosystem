"""Deterministic rendering and exclusive storage for proposed vault drafts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import UUID

from .data_access import ProposalRecord


class DraftStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DraftNoteRecord:
    draft_path: str
    content_hash: str
    observed_status: DraftStatus
    path: Path


class DraftNoteError(RuntimeError):
    def __init__(self, message: str, draft_path: Optional[str] = None) -> None:
        super().__init__(message)
        self.draft_path = draft_path


class DraftNoteCollision(DraftNoteError):
    """Raised when an exclusive draft target already exists."""


class DraftNoteConsistencyError(DraftNoteError):
    """Raised when an existing draft cannot be trusted."""


class DraftNoteWriteError(DraftNoteError):
    """Raised when a new draft cannot be finalized."""


def render_proposed_draft(
    proposal: ProposalRecord,
    canonical_body: bytes,
) -> bytes:
    try:
        body = canonical_body.decode("utf-8")
        confidence = json.dumps(
            proposal.confidence,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (AttributeError, TypeError, UnicodeError, ValueError) as error:
        raise DraftNoteConsistencyError("draft inputs are invalid") from error
    if (
        type(canonical_body) is not bytes
        or hashlib.sha256(canonical_body).hexdigest() != proposal.content_hash
    ):
        raise DraftNoteConsistencyError("draft content hash disagrees")

    def scalar(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = (
        "---\n"
        f"id: {scalar(f'note.{proposal.capture_id}')}\n"
        f"proposal_id: {scalar(proposal.proposal_id)}\n"
        f"classification_id: {scalar(proposal.classification_id)}\n"
        f"capture_id: {scalar(proposal.capture_id)}\n"
        f"type: {scalar(proposal.note_type)}\n"
        f"title: {scalar(proposal.title)}\n"
        "status: proposed\n"
        "verification: unverified\n"
        f"created: {scalar(proposal.created_at)}\n"
        "approved: null\n"
        f"confidence: {confidence}\n"
        f"sensitivity: {proposal.sensitivity}\n"
        f"risk_level: {proposal.risk_level}\n"
        "evidence:\n"
        f"  capture: {scalar(f'evidence/{proposal.capture_id}/raw.txt')}\n"
        "  classification: "
        f"{scalar(f'classification-evidence/{proposal.classification_id}/raw-response.txt')}\n"
        "  proposal: "
        f"{scalar(f'proposal-evidence/{proposal.proposal_id}/raw-response.txt')}\n"
        "links: []\n"
        "---\n\n"
    )
    return lines.encode("utf-8") + body.encode("utf-8")


class DraftNoteStore:
    def __init__(self, runtime_root: Path) -> None:
        self._runtime_root = Path(runtime_root)

    def create(
        self,
        relative_path: str,
        expected_bytes: bytes,
    ) -> DraftNoteRecord:
        try:
            path = self._validated_path(relative_path)
            self._prepare_parents(path.parent)
        except (OSError, TypeError, UnicodeError, ValueError) as error:
            raise DraftNoteWriteError(
                f"draft path or content is invalid: {error}",
                relative_path if isinstance(relative_path, str) else None,
            ) from error
        if os.path.lexists(path):
            raise DraftNoteCollision(
                f"draft target already exists: {relative_path}",
                relative_path,
            )
        try:
            self._validate_expected_bytes(expected_bytes)
        except (TypeError, UnicodeError, ValueError) as error:
            raise DraftNoteWriteError(
                f"draft content is invalid: {error}",
                relative_path,
            ) from error
        try:
            with path.open("xb") as stream:
                stream.write(expected_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            observed = path.read_bytes()
        except FileExistsError as error:
            raise DraftNoteCollision(
                f"draft target already exists: {relative_path}",
                relative_path,
            ) from error
        except OSError as error:
            raise DraftNoteWriteError(
                f"draft write failed: {error}",
                relative_path,
            ) from error
        if observed != expected_bytes:
            raise DraftNoteWriteError(
                "draft readback disagrees with expected bytes",
                relative_path,
            )
        return self._record(
            relative_path,
            observed,
            DraftStatus.PROPOSED,
            path,
        )

    def validate(
        self,
        relative_path: str,
        expected_proposed_bytes: bytes,
    ) -> DraftNoteRecord:
        try:
            path = self._validated_path(relative_path)
            self._validate_expected_bytes(expected_proposed_bytes)
            self._validate_parents(path.parent)
            if not path.is_file() or path.is_symlink():
                raise ValueError("draft is missing or not a regular file")
            observed = path.read_bytes()
            observed.decode("utf-8")
            variants = {
                status: expected_proposed_bytes.replace(
                    b"status: proposed\n",
                    f"status: {status.value}\n".encode("utf-8"),
                    1,
                )
                for status in DraftStatus
            }
            matches = [status for status, value in variants.items() if observed == value]
            if len(matches) != 1:
                raise ValueError("draft differs outside the editable status field")
        except (OSError, TypeError, UnicodeError, ValueError) as error:
            raise DraftNoteConsistencyError(
                f"draft is inconsistent: {error}",
                relative_path if isinstance(relative_path, str) else None,
            ) from error
        return self._record(relative_path, observed, matches[0], path)

    def _validated_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str):
            raise TypeError("draft path is not a string")
        relative = Path(relative_path)
        if relative.is_absolute() or len(relative.parts) != 4:
            raise ValueError("draft path is outside the proposed vault directory")
        if relative.parts[:3] != ("vault", "notes", "proposed"):
            raise ValueError("draft path is outside the proposed vault directory")
        filename = relative.parts[3]
        if not filename.startswith("note.") or not filename.endswith(".md"):
            raise ValueError("draft filename is invalid")
        capture_id = filename[len("note.") : -len(".md")]
        capture_uuid = UUID(capture_id)
        if capture_uuid.version != 4 or str(capture_uuid) != capture_id:
            raise ValueError("draft filename does not contain a canonical UUID4")
        return self._runtime_root / relative

    def _prepare_parents(self, expected_parent: Path) -> None:
        current = self._runtime_root
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError("runtime root is not a real directory")
        current.mkdir(parents=True, exist_ok=True)
        for part in ("vault", "notes", "proposed"):
            current = current / part
            try:
                current.mkdir(exist_ok=True)
            except FileExistsError:
                pass
            if current.is_symlink() or not current.is_dir():
                raise ValueError("draft parent is not a real directory")
        if current != expected_parent:
            raise ValueError("draft parent disagrees with path contract")

    def _validate_parents(self, expected_parent: Path) -> None:
        current = self._runtime_root
        if current.is_symlink() or not current.is_dir():
            raise ValueError("runtime root is not a real directory")
        for part in ("vault", "notes", "proposed"):
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ValueError("draft parent is not a real directory")
        if current != expected_parent:
            raise ValueError("draft parent disagrees with path contract")

    def _validate_expected_bytes(self, expected_bytes: bytes) -> None:
        if type(expected_bytes) is not bytes or not expected_bytes:
            raise ValueError("expected draft bytes are invalid")
        expected_bytes.decode("utf-8")
        if not expected_bytes.startswith(b"---\n"):
            raise ValueError("expected draft frontmatter is invalid")
        frontmatter_end = expected_bytes.find(b"---\n\n", len(b"---\n"))
        if frontmatter_end == -1:
            raise ValueError("expected draft frontmatter is invalid")
        frontmatter = expected_bytes[len(b"---\n") : frontmatter_end]
        if frontmatter.splitlines(keepends=True).count(b"status: proposed\n") != 1:
            raise ValueError("expected draft status field is invalid")

    def _record(
        self,
        relative_path: str,
        observed: bytes,
        status: DraftStatus,
        path: Path,
    ) -> DraftNoteRecord:
        return DraftNoteRecord(
            draft_path=relative_path,
            content_hash=hashlib.sha256(observed).hexdigest(),
            observed_status=status,
            path=path,
        )

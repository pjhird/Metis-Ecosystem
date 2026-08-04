"""Deterministic rendering and exclusive storage for proposed vault drafts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID

from .data_access import ProposalRecord


class DraftStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


EMPTY_LINKS = b"links: []\n"
LINKS_HEADER = b"links:\n"
LINK_PREFIX = b'  - "[['
LINK_SUFFIX = b']]"\n'
LINK_TARGET = re.compile(r"[A-Za-z0-9._-]+")
# stage -> (vault directory, note-id prefix, the status a note in it carries).
# A filed planning note enters its own lifecycle at `active` (ADR-021); only the
# approval surface speaks `DraftStatus`, so `active` is never a draft status.
STAGES = {
    "proposed": (("vault", "notes", "proposed"), "note.", DraftStatus.PROPOSED.value),
    "filed": (("vault", "notes", "filed"), "note.", DraftStatus.APPROVED.value),
    "goals": (("vault", "goals"), "goal.", "active"),
    "projects": (("vault", "projects"), "proj.", "active"),
}
PLANNING_STAGES = {"goal": "goals", "project": "projects"}
CAPTURE_NAMED_STAGES = ("proposed", "filed")


@dataclass(frozen=True)
class DraftNoteRecord:
    draft_path: str
    content_hash: str
    # A `DraftStatus` when read off the approval surface; a stage's own status
    # word (`active`) when a planning note is created.
    observed_status: str
    observed_links: Tuple[str, ...]
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


def _planning_field(note_type: str, parent_goal_id: Optional[str]) -> str:
    """The one frontmatter line a planning note adds, written by the system.

    A goal's horizon is the ratified default; a project names its parent goal.
    Neither is human-editable — ADR-020 still allows only `status` and `links`.
    """
    if (note_type == "project") != (parent_goal_id is not None):
        raise DraftNoteConsistencyError(
            "a project note requires a parent goal, and only a project may carry one"
        )
    if note_type == "goal":
        return "horizon: annual\n"
    if note_type == "project":
        if LINK_TARGET.fullmatch(parent_goal_id) is None:
            raise DraftNoteConsistencyError("parent goal is not a valid note id")
        return f'goal: "[[{parent_goal_id}]]"\n'
    return ""


def render_proposed_draft(
    proposal: ProposalRecord,
    canonical_body: bytes,
    *,
    parent_goal_id: Optional[str] = None,
) -> bytes:
    return render_note(proposal, canonical_body, parent_goal_id=parent_goal_id)


def render_note(
    proposal: ProposalRecord,
    canonical_body: bytes,
    *,
    status: str = DraftStatus.PROPOSED.value,
    links: Tuple[str, ...] = (),
    approved: Optional[str] = None,
    parent_goal_id: Optional[str] = None,
    note_id: Optional[str] = None,
) -> bytes:
    """Render one note. The proposed draft and the filed note share this."""
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

    if any(LINK_TARGET.fullmatch(target) is None for target in links) or len(
        set(links)
    ) != len(links):
        raise DraftNoteConsistencyError("note links are invalid")
    rendered_links = (
        "links: []\n"
        if not links
        else "links:\n" + "".join(f'  - "[[{target}]]"\n' for target in links)
    )
    planning_field = _planning_field(proposal.note_type, parent_goal_id)
    lines = (
        "---\n"
        f"id: {scalar(note_id or f'note.{proposal.capture_id}')}\n"
        f"proposal_id: {scalar(proposal.proposal_id)}\n"
        f"classification_id: {scalar(proposal.classification_id)}\n"
        f"capture_id: {scalar(proposal.capture_id)}\n"
        f"type: {scalar(proposal.note_type)}\n"
        f"title: {scalar(proposal.title)}\n"
        f"{planning_field}"
        f"status: {status}\n"
        "verification: unverified\n"
        f"created: {scalar(proposal.created_at)}\n"
        f"approved: {'null' if approved is None else scalar(approved)}\n"
        f"confidence: {confidence}\n"
        f"sensitivity: {proposal.sensitivity}\n"
        f"risk_level: {proposal.risk_level}\n"
        "evidence:\n"
        f"  capture: {scalar(f'evidence/{proposal.capture_id}/raw.txt')}\n"
        "  classification: "
        f"{scalar(f'classification-evidence/{proposal.classification_id}/raw-response.txt')}\n"
        "  proposal: "
        f"{scalar(f'proposal-evidence/{proposal.proposal_id}/raw-response.txt')}\n"
        f"{rendered_links}"
        "---\n\n"
    )
    return lines.encode("utf-8") + body.encode("utf-8")


class DraftNoteStore:
    """Exclusive storage for one vault note stage.

    `proposed` holds drafts awaiting a decision; `filed`, `goals`, and
    `projects` hold permanent notes. They share this store so the
    exclusive-create, fsync, and read-back write path has exactly one
    implementation.
    """

    def __init__(
        self,
        runtime_root: Path,
        *,
        stage: str = "proposed",
    ) -> None:
        self._runtime_root = Path(runtime_root)
        self._stage = stage
        self._parts, self._prefix, self._expected_status = STAGES[stage]

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
            status, links = self._validate_expected_bytes(expected_bytes)
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
        return self._record(relative_path, observed, status, links, path)

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
            status, links = self._match_editable_fields(
                observed,
                expected_proposed_bytes,
            )
        except (OSError, TypeError, UnicodeError, ValueError) as error:
            raise DraftNoteConsistencyError(
                f"draft is inconsistent: {error}",
                relative_path if isinstance(relative_path, str) else None,
            ) from error
        return self._record(relative_path, observed, status, links, path)

    def _match_editable_fields(
        self,
        observed: bytes,
        expected: bytes,
    ) -> Tuple[DraftStatus, Tuple[str, ...]]:
        """Match every byte outside `status` and `links` exactly (ADR-020)."""
        head, tail = self._split_editable(expected)
        boundary = observed.find(b"---\n\n", len(b"---\n"))
        if not observed.startswith(b"---\n") or boundary == -1:
            raise ValueError("draft frontmatter is missing")
        if observed[boundary:] != tail:
            raise ValueError("draft differs outside the editable fields")
        frontmatter = observed[len(b"---\n") : boundary]
        matches = [
            (status, variant)
            for status, variant in (
                (status, self._status_variant(head, status))
                for status in DraftStatus
            )
            if frontmatter.startswith(variant)
        ]
        if len(matches) != 1:
            raise ValueError("draft differs outside the editable fields")
        status, variant = matches[0]
        return status, self._parsed_links(frontmatter[len(variant) :])

    def _split_editable(self, expected: bytes) -> Tuple[bytes, bytes]:
        boundary = expected.find(b"---\n\n", len(b"---\n"))
        frontmatter = expected[len(b"---\n") : boundary]
        return frontmatter[: -len(EMPTY_LINKS)], expected[boundary:]

    def _status_variant(self, head: bytes, status: DraftStatus) -> bytes:
        return head.replace(
            b"status: proposed\n",
            f"status: {status.value}\n".encode("utf-8"),
            1,
        )

    def _parsed_links(self, region: bytes) -> Tuple[str, ...]:
        if region == EMPTY_LINKS:
            return ()
        if not region.startswith(LINKS_HEADER):
            raise ValueError("draft links field is malformed")
        lines = region[len(LINKS_HEADER) :].splitlines(keepends=True)
        if not lines:
            raise ValueError("draft links field is empty")
        targets = []
        for line in lines:
            if not line.startswith(LINK_PREFIX) or not line.endswith(LINK_SUFFIX):
                raise ValueError("draft link entry is malformed")
            target = line[len(LINK_PREFIX) : -len(LINK_SUFFIX)].decode("utf-8")
            if LINK_TARGET.fullmatch(target) is None:
                raise ValueError("draft link target is invalid")
            targets.append(target)
        if len(set(targets)) != len(targets):
            raise ValueError("draft links contain a duplicate")
        return tuple(targets)

    def _validated_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str):
            raise TypeError("draft path is not a string")
        relative = Path(relative_path)
        if relative.is_absolute() or len(relative.parts) != len(self._parts) + 1:
            raise ValueError("draft path is outside the staged vault directory")
        if relative.parts[:-1] != self._parts:
            raise ValueError("draft path is outside the staged vault directory")
        filename = relative.parts[-1]
        if not filename.startswith(self._prefix) or not filename.endswith(".md"):
            raise ValueError("draft filename is invalid")
        note_id = filename[: -len(".md")]
        if LINK_TARGET.fullmatch(note_id) is None:
            raise ValueError("draft filename is not a linkable note id")
        if self._stage in CAPTURE_NAMED_STAGES:
            capture_id = note_id[len(self._prefix) :]
            capture_uuid = UUID(capture_id)
            if capture_uuid.version != 4 or str(capture_uuid) != capture_id:
                raise ValueError("draft filename does not contain a canonical UUID4")
        return self._runtime_root / relative

    def _prepare_parents(self, expected_parent: Path) -> None:
        current = self._runtime_root
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError("runtime root is not a real directory")
        current.mkdir(parents=True, exist_ok=True)
        for part in self._parts:
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
        for part in self._parts:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ValueError("draft parent is not a real directory")
        if current != expected_parent:
            raise ValueError("draft parent disagrees with path contract")

    def _validate_expected_bytes(
        self,
        expected_bytes: bytes,
    ) -> Tuple[str, Tuple[str, ...]]:
        if type(expected_bytes) is not bytes or not expected_bytes:
            raise ValueError("expected note bytes are invalid")
        expected_bytes.decode("utf-8")
        if not expected_bytes.startswith(b"---\n"):
            raise ValueError("expected note frontmatter is invalid")
        frontmatter_end = expected_bytes.find(b"---\n\n", len(b"---\n"))
        if frontmatter_end == -1:
            raise ValueError("expected note frontmatter is invalid")
        frontmatter = expected_bytes[len(b"---\n") : frontmatter_end]
        lines = frontmatter.splitlines(keepends=True)
        status_line = f"status: {self._expected_status}\n".encode("utf-8")
        if lines.count(status_line) != 1:
            raise ValueError("expected note status field is invalid")
        self._validate_provenance(lines)
        if self._stage == "proposed" and not frontmatter.endswith(EMPTY_LINKS):
            raise ValueError("expected note links field is invalid")
        starts = [
            index for index, line in enumerate(lines) if line.startswith(b"links:")
        ]
        if len(starts) != 1 or starts[0] == 0:
            raise ValueError("expected note links field is invalid")
        links = self._parsed_links(b"".join(lines[starts[0] :]))
        return self._expected_status, links

    def _validate_provenance(self, lines: list) -> None:
        """A note without capture_id or evidence is invalid (REQ-VLT-004)."""
        if (
            sum(line.startswith(b"capture_id: ") for line in lines) != 1
            or lines.count(b"evidence:\n") != 1
            or sum(line.startswith(b"  capture: ") for line in lines) != 1
        ):
            raise ValueError("note provenance is missing")

    def _record(
        self,
        relative_path: str,
        observed: bytes,
        status: str,
        links: Tuple[str, ...],
        path: Path,
    ) -> DraftNoteRecord:
        return DraftNoteRecord(
            draft_path=relative_path,
            content_hash=hashlib.sha256(observed).hexdigest(),
            observed_status=status,
            observed_links=links,
            path=path,
        )

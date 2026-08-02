from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metis.data_access import ProposalRecord
from metis.draft_notes import (
    DraftNoteCollision,
    DraftNoteConsistencyError,
    DraftNoteStore,
    DraftNoteWriteError,
    DraftStatus,
    render_proposed_draft,
)


PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
DRAFT_PATH = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
BODY_BYTES = (
    b"A reviewable proposal.\n\n## Proposal rationale\nReason.\n\n"
    b"## Uncertainties\nNone identified by the proposal model.\n"
)


def proposal_record(**changes: object) -> ProposalRecord:
    values = {
        "proposal_id": PROPOSAL_ID,
        "capture_id": CAPTURE_ID,
        "classification_id": CLASSIFICATION_ID,
        "note_type": "idea",
        "title": 'Review "this" idea',
        "body_path": f"proposal-content/{PROPOSAL_ID}/body.md",
        "proposed_links": "[]",
        "evidence_refs": "[]",
        "confidence": 0.82,
        "sensitivity": "normal",
        "risk_level": "low",
        "reason": "Reason.",
        "uncertainties_json": "[]",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "propose-v1",
        "raw_response_path": (
            f"proposal-evidence/{PROPOSAL_ID}/raw-response.txt"
        ),
        "content_hash": hashlib.sha256(BODY_BYTES).hexdigest(),
        "draft_note_path": None,
        "state": "pending",
        "created_at": "2026-08-02T20:00:00Z",
    }
    values.update(changes)
    return ProposalRecord(**values)


class DraftNoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)
        self.store = DraftNoteStore(self.runtime_root)
        self.expected = render_proposed_draft(proposal_record(), BODY_BYTES)

    def test_rendered_draft_is_byte_exact_and_safely_quoted(self) -> None:
        self.assertEqual(
            self.expected,
            (
                '---\n'
                f'id: "note.{CAPTURE_ID}"\n'
                f'proposal_id: "{PROPOSAL_ID}"\n'
                f'classification_id: "{CLASSIFICATION_ID}"\n'
                f'capture_id: "{CAPTURE_ID}"\n'
                'type: "idea"\n'
                'title: "Review \\"this\\" idea"\n'
                'status: proposed\n'
                'verification: unverified\n'
                'created: "2026-08-02T20:00:00Z"\n'
                'approved: null\n'
                'confidence: 0.82\n'
                'sensitivity: normal\n'
                'risk_level: low\n'
                'evidence:\n'
                f'  capture: "evidence/{CAPTURE_ID}/raw.txt"\n'
                f'  classification: "classification-evidence/{CLASSIFICATION_ID}/raw-response.txt"\n'
                f'  proposal: "proposal-evidence/{PROPOSAL_ID}/raw-response.txt"\n'
                'links: []\n'
                '---\n\n'
            ).encode("utf-8")
            + BODY_BYTES,
        )

    def test_create_writes_exact_draft_and_validates_readback(self) -> None:
        record = self.store.create(DRAFT_PATH, self.expected)

        self.assertEqual(record.draft_path, DRAFT_PATH)
        self.assertEqual(record.path.read_bytes(), self.expected)
        self.assertEqual(record.content_hash, hashlib.sha256(self.expected).hexdigest())
        self.assertEqual(record.observed_status, DraftStatus.PROPOSED)
        self.assertEqual(self.store.validate(DRAFT_PATH, self.expected), record)

    def test_status_like_body_and_reason_lines_are_not_frontmatter_fields(self) -> None:
        bodies = {
            "body": (
                b"status: proposed\n\n## Proposal rationale\nReason.\n\n"
                b"## Uncertainties\nNone identified by the proposal model.\n"
            ),
            "reason": (
                b"A reviewable proposal.\n\n## Proposal rationale\n"
                b"status: proposed\n\n## Uncertainties\n"
                b"None identified by the proposal model.\n"
            ),
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                root = self.runtime_root / name
                store = DraftNoteStore(root)
                proposal = proposal_record(
                    content_hash=hashlib.sha256(body).hexdigest()
                )
                expected = render_proposed_draft(proposal, body)

                created = store.create(DRAFT_PATH, expected)

                self.assertEqual(store.validate(DRAFT_PATH, expected), created)

    def test_approved_and_rejected_status_are_preserved_not_interpreted(self) -> None:
        for status in (DraftStatus.APPROVED, DraftStatus.REJECTED):
            with self.subTest(status=status.value):
                root = self.runtime_root / status.value
                store = DraftNoteStore(root)
                record = store.create(DRAFT_PATH, self.expected)
                changed = self.expected.replace(
                    b"status: proposed\n",
                    f"status: {status.value}\n".encode("utf-8"),
                    1,
                )
                record.path.write_bytes(changed)

                observed = store.validate(DRAFT_PATH, self.expected)

                self.assertEqual(observed.observed_status, status)
                self.assertEqual(observed.path.read_bytes(), changed)

    def test_any_non_status_edit_or_unknown_status_fails_closed(self) -> None:
        changes = (
            (b"title: ", b"title: changed "),
            (b"A reviewable proposal.", b"Edited proposal."),
            (b"status: proposed", b"status: maybe"),
        )
        for index, (old, new) in enumerate(changes):
            with self.subTest(old=old):
                root = self.runtime_root / f"edit-{index}"
                store = DraftNoteStore(root)
                record = store.create(DRAFT_PATH, self.expected)
                record.path.write_bytes(self.expected.replace(old, new, 1))

                with self.assertRaises(DraftNoteConsistencyError):
                    store.validate(DRAFT_PATH, self.expected)

    def test_invalid_or_permanent_paths_are_refused_before_creation(self) -> None:
        paths = (
            f"vault/notes/filed/note.{CAPTURE_ID}.md",
            f"vault/notes/proposed/../filed/note.{CAPTURE_ID}.md",
            f"vault/notes/proposed/wrong.{CAPTURE_ID}.md",
            "vault/notes/proposed/note.not-a-uuid.md",
            f"/vault/notes/proposed/note.{CAPTURE_ID}.md",
        )
        for relative_path in paths:
            with self.subTest(relative_path=relative_path):
                with self.assertRaises(DraftNoteWriteError):
                    self.store.create(relative_path, self.expected)

        self.assertFalse((self.runtime_root / "vault").exists())

    def test_symlink_parent_and_final_file_fail_closed(self) -> None:
        outside = self.runtime_root / "outside"
        outside.mkdir()
        vault = self.runtime_root / "vault"
        vault.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DraftNoteWriteError):
            self.store.create(DRAFT_PATH, self.expected)

        vault.unlink()
        target = self.runtime_root / "target.md"
        target.write_bytes(b"outside")
        final = self.runtime_root / DRAFT_PATH
        final.parent.mkdir(parents=True)
        final.symlink_to(target)
        with self.assertRaises(DraftNoteCollision):
            self.store.create(DRAFT_PATH, self.expected)
        with self.assertRaises(DraftNoteConsistencyError):
            self.store.validate(DRAFT_PATH, self.expected)

    def test_validate_refuses_parent_replaced_by_symlink(self) -> None:
        self.store.create(DRAFT_PATH, self.expected)
        proposed = self.runtime_root / "vault" / "notes" / "proposed"
        relocated = self.runtime_root / "relocated-proposed"
        proposed.rename(relocated)
        proposed.symlink_to(relocated, target_is_directory=True)

        with self.assertRaises(DraftNoteConsistencyError):
            self.store.validate(DRAFT_PATH, self.expected)

    def test_collision_never_overwrites_existing_bytes(self) -> None:
        record = self.store.create(DRAFT_PATH, self.expected)

        with self.assertRaises(DraftNoteCollision):
            self.store.create(DRAFT_PATH, b"different")

        self.assertEqual(record.path.read_bytes(), self.expected)

    def test_readback_disagreement_is_a_write_failure(self) -> None:
        real_read_bytes = Path.read_bytes

        def disagree(path: Path) -> bytes:
            if path == self.runtime_root / DRAFT_PATH:
                return b"different"
            return real_read_bytes(path)

        with patch.object(Path, "read_bytes", new=disagree):
            with self.assertRaises(DraftNoteWriteError):
                self.store.create(DRAFT_PATH, self.expected)


if __name__ == "__main__":
    unittest.main()

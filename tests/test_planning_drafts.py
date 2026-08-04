"""Task 3 — goal and project drafts render their planning fields (ADR-021).

A goal carries the ratified `horizon: annual`. A project carries its parent
goal as a system-written `goal:` field, resolving design open point 3 in
favour of showing it on the draft as well as the filed note. Neither field is
human-editable: ADR-020 still allows only `status` and `links`.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from metis.data_access import ProposalRecord
from metis.draft_notes import (
    DraftNoteConsistencyError,
    DraftNoteStore,
    DraftStatus,
    render_proposed_draft,
)


PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
DRAFT_PATH = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
GOAL_ID = "goal.health-baseline"
BODY_BYTES = b"A reviewable proposal.\n"


def proposal_record(**changes: object) -> ProposalRecord:
    values = {
        "proposal_id": PROPOSAL_ID,
        "capture_id": CAPTURE_ID,
        "classification_id": CLASSIFICATION_ID,
        "note_type": "idea",
        "title": "Establish a health baseline",
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
        "raw_response_path": f"proposal-evidence/{PROPOSAL_ID}/raw-response.txt",
        "content_hash": hashlib.sha256(BODY_BYTES).hexdigest(),
        "draft_note_path": None,
        "state": "pending",
        "created_at": "2026-08-02T20:00:00Z",
    }
    values.update(changes)
    return ProposalRecord(**values)


def frontmatter(rendered: bytes) -> list[str]:
    head = rendered.split(b"---\n\n")[0]
    return head.decode("utf-8").removeprefix("---\n").splitlines()


class PlanningDraftRenderingTests(unittest.TestCase):
    def test_a_goal_draft_carries_the_annual_horizon_after_its_title(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="goal"), BODY_BYTES
        )

        lines = frontmatter(rendered)
        self.assertIn("horizon: annual", lines)
        self.assertEqual(
            lines[lines.index("horizon: annual") - 1],
            'title: "Establish a health baseline"',
        )

    def test_a_goal_draft_carries_no_parent_goal_field(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="goal"), BODY_BYTES
        )

        self.assertNotIn(b"\ngoal: ", rendered)

    def test_a_project_draft_carries_its_system_written_parent_goal(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="project"),
            BODY_BYTES,
            parent_goal_id=GOAL_ID,
        )

        lines = frontmatter(rendered)
        self.assertIn(f'goal: "[[{GOAL_ID}]]"', lines)
        self.assertEqual(
            lines[lines.index(f'goal: "[[{GOAL_ID}]]"') - 1],
            'title: "Establish a health baseline"',
        )

    def test_a_project_draft_carries_no_horizon(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="project"),
            BODY_BYTES,
            parent_goal_id=GOAL_ID,
        )

        self.assertNotIn(b"\nhorizon: ", rendered)

    def test_links_remain_the_final_frontmatter_field(self) -> None:
        """The draft matcher slices the links region off the end (ADR-020)."""
        for note_type, parent in (
            ("idea", None),
            ("goal", None),
            ("project", GOAL_ID),
        ):
            with self.subTest(note_type=note_type):
                rendered = render_proposed_draft(
                    proposal_record(note_type=note_type),
                    BODY_BYTES,
                    parent_goal_id=parent,
                )

                self.assertEqual(frontmatter(rendered)[-1], "links: []")

    def test_a_typed_note_draft_is_byte_identical_to_before(self) -> None:
        """Planning fields must not leak into the existing note shape."""
        rendered = render_proposed_draft(proposal_record(), BODY_BYTES)

        self.assertNotIn(b"\nhorizon: ", rendered)
        self.assertNotIn(b"\ngoal: ", rendered)

    def test_a_project_without_a_parent_goal_is_refused(self) -> None:
        with self.assertRaises(DraftNoteConsistencyError):
            render_proposed_draft(proposal_record(note_type="project"), BODY_BYTES)

    def test_a_parent_goal_on_a_non_project_is_refused(self) -> None:
        for note_type in ("idea", "goal"):
            with self.subTest(note_type=note_type):
                with self.assertRaises(DraftNoteConsistencyError):
                    render_proposed_draft(
                        proposal_record(note_type=note_type),
                        BODY_BYTES,
                        parent_goal_id=GOAL_ID,
                    )

    def test_an_unsafe_parent_goal_is_refused(self) -> None:
        with self.assertRaises(DraftNoteConsistencyError):
            render_proposed_draft(
                proposal_record(note_type="project"),
                BODY_BYTES,
                parent_goal_id="../escape",
            )


class PlanningDraftApprovalSurfaceTests(unittest.TestCase):
    """ADR-020 is unchanged: only `status` and `links` may be edited."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.store = DraftNoteStore(self.root)

    def _write(self, note_type: str, parent: str | None) -> bytes:
        rendered = render_proposed_draft(
            proposal_record(note_type=note_type),
            BODY_BYTES,
            parent_goal_id=parent,
        )
        self.store.create(DRAFT_PATH, rendered)
        return rendered

    def test_approving_a_goal_draft_is_read_as_approved(self) -> None:
        rendered = self._write("goal", None)
        path = self.root / DRAFT_PATH
        path.write_bytes(rendered.replace(b"status: proposed\n", b"status: approved\n"))

        record = self.store.validate(DRAFT_PATH, rendered)

        self.assertIs(record.observed_status, DraftStatus.APPROVED)

    def test_editing_a_projects_parent_goal_is_refused(self) -> None:
        rendered = self._write("project", GOAL_ID)
        path = self.root / DRAFT_PATH
        path.write_bytes(
            rendered.replace(
                f'goal: "[[{GOAL_ID}]]"'.encode("utf-8"),
                b'goal: "[[goal.something-else]]"',
            )
        )

        with self.assertRaises(DraftNoteConsistencyError):
            self.store.validate(DRAFT_PATH, rendered)


if __name__ == "__main__":
    unittest.main()

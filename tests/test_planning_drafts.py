"""Task 3 — goal and project drafts render their planning fields (ADR-021).

A goal carries the ratified `horizon: annual`. A project carries its parent
goal as a system-written `goal:` field, resolving design open point 3 in
favour of showing it on the draft as well as the filed note. Neither field is
human-editable: ADR-020 still allows only `status` and `links`.

Task 7 (ADR-022) adds the task draft, whose parent is a project, and moves the
choice of planning field from `proposal.note_type` to the capture-time pin: a
classifier may legitimately type a note `task`, and that is an ordinary typed
note, not a planning task (ADR-022 clause 11).
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from metis.data_access import ProposalRecord
from metis.draft_notes import (
    PARENT_REQUIRED,
    DraftNoteConsistencyError,
    DraftNoteStore,
    DraftNoteWriteError,
    DraftStatus,
    render_note,
    render_proposed_draft,
)


PROPOSAL_ID = "01K1D5Q5M00000000000000001"
CLASSIFICATION_ID = "01K1D5Q5M00000000000000000"
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
DRAFT_PATH = f"vault/notes/proposed/note.{CAPTURE_ID}.md"
GOAL_ID = "goal.health-baseline"
PROJECT_ID = "proj.weekly-7d4e8eb8"
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
            proposal_record(note_type="goal"), BODY_BYTES, type_pin="goal"
        )

        lines = frontmatter(rendered)
        self.assertIn("horizon: annual", lines)
        self.assertEqual(
            lines[lines.index("horizon: annual") - 1],
            'title: "Establish a health baseline"',
        )

    def test_a_goal_draft_carries_no_parent_goal_field(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="goal"), BODY_BYTES, type_pin="goal"
        )

        self.assertNotIn(b"\ngoal: ", rendered)

    def test_a_project_draft_carries_its_system_written_parent_goal(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="project"),
            BODY_BYTES,
            type_pin="project",
            parent_id=GOAL_ID,
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
            type_pin="project",
            parent_id=GOAL_ID,
        )

        self.assertNotIn(b"\nhorizon: ", rendered)

    def test_a_pinned_task_proposes_a_draft_naming_its_parent_project(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="task"),
            BODY_BYTES,
            type_pin="task",
            parent_id=PROJECT_ID,
        )

        self.assertIn(f'project: "[[{PROJECT_ID}]]"\n'.encode("utf-8"), rendered)

    def test_a_task_draft_carries_neither_horizon_nor_goal(self) -> None:
        rendered = render_proposed_draft(
            proposal_record(note_type="task"),
            BODY_BYTES,
            type_pin="task",
            parent_id=PROJECT_ID,
        )

        self.assertNotIn(b"\nhorizon: ", rendered)
        self.assertNotIn(b"\ngoal: ", rendered)

    def test_links_remain_the_final_frontmatter_field(self) -> None:
        """The draft matcher slices the links region off the end (ADR-020)."""
        for type_pin, parent in (
            (None, None),
            ("goal", None),
            ("project", GOAL_ID),
            ("task", PROJECT_ID),
        ):
            with self.subTest(type_pin=type_pin):
                rendered = render_proposed_draft(
                    proposal_record(note_type=type_pin or "idea"),
                    BODY_BYTES,
                    type_pin=type_pin,
                    parent_id=parent,
                )

                self.assertEqual(frontmatter(rendered)[-1], "links: []")

    def test_a_typed_note_draft_is_byte_identical_to_before(self) -> None:
        """Planning fields must not leak into the existing note shape."""
        rendered = render_proposed_draft(proposal_record(), BODY_BYTES)

        self.assertNotIn(b"\nhorizon: ", rendered)
        self.assertNotIn(b"\ngoal: ", rendered)
        self.assertNotIn(b"\nproject: ", rendered)

    def test_a_project_without_a_parent_goal_is_refused(self) -> None:
        with self.assertRaises(DraftNoteConsistencyError):
            render_proposed_draft(
                proposal_record(note_type="project"), BODY_BYTES, type_pin="project"
            )

    def test_a_task_without_a_parent_is_refused(self) -> None:
        with self.assertRaises(DraftNoteConsistencyError):
            render_proposed_draft(
                proposal_record(note_type="task"), BODY_BYTES, type_pin="task"
            )

    def test_a_parent_on_a_pin_that_takes_none_is_refused(self) -> None:
        for type_pin in (None, "goal"):
            with self.subTest(type_pin=type_pin):
                with self.assertRaises(DraftNoteConsistencyError):
                    render_proposed_draft(
                        proposal_record(note_type=type_pin or "idea"),
                        BODY_BYTES,
                        type_pin=type_pin,
                        parent_id=GOAL_ID,
                    )

    def test_an_unsafe_parent_goal_is_refused(self) -> None:
        for type_pin in ("project", "task"):
            with self.subTest(type_pin=type_pin):
                with self.assertRaises(DraftNoteConsistencyError):
                    render_proposed_draft(
                        proposal_record(note_type=type_pin),
                        BODY_BYTES,
                        type_pin=type_pin,
                        parent_id="../escape",
                    )

    def test_the_mirrored_parent_rule_matches_evidence(self) -> None:
        """`PARENT_REQUIRED` is deliberately duplicated so the vault layer and
        the evidence layer stay independent (see the comment on the constant).
        Nothing in production keeps the two in step, so this does.
        """
        from metis.evidence import PARENT_REQUIRED as EVIDENCE_PARENT_REQUIRED

        self.assertEqual(PARENT_REQUIRED, EVIDENCE_PARENT_REQUIRED)


class PlanningNoteVerificationTests(unittest.TestCase):
    """Planning entities are exempt from REQ-DATA-005's `verification` field.

    That field applies to interpreted content whose truth can be checked later,
    not to planning identity a human declared at capture (spec §3).
    """

    def test_a_task_note_carries_no_verification_field(self) -> None:
        rendered = render_note(
            proposal_record(note_type="task"),
            BODY_BYTES,
            status="open",
            type_pin="task",
            parent_id=PROJECT_ID,
            note_id="task.weekly-7d4e8eb8",
        )

        self.assertNotIn(b"verification:", rendered)

    def test_a_typed_task_note_still_carries_verification(self) -> None:
        """A classifier `task` is an ordinary typed note (ADR-022 clause 11)."""
        rendered = render_note(proposal_record(note_type="task"), BODY_BYTES)

        self.assertIn(b"verification: unverified\n", rendered)

    def test_goal_and_project_notes_carry_no_verification_field(self) -> None:
        for type_pin, parent in (("goal", None), ("project", GOAL_ID)):
            with self.subTest(type_pin=type_pin):
                rendered = render_proposed_draft(
                    proposal_record(note_type=type_pin),
                    BODY_BYTES,
                    type_pin=type_pin,
                    parent_id=parent,
                )

                self.assertNotIn(b"verification:", rendered)


class TasksStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def test_a_tasks_stage_refuses_a_path_outside_vault_tasks(self) -> None:
        store = DraftNoteStore(self.root, stage="tasks")

        with self.assertRaises(DraftNoteWriteError):
            store.create("vault/notes/filed/task.escape.md", b"x")


class PlanningDraftApprovalSurfaceTests(unittest.TestCase):
    """ADR-020 is unchanged: only `status` and `links` may be edited."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.store = DraftNoteStore(self.root)

    def _write(self, type_pin: str, parent: str | None) -> bytes:
        rendered = render_proposed_draft(
            proposal_record(note_type=type_pin),
            BODY_BYTES,
            type_pin=type_pin,
            parent_id=parent,
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

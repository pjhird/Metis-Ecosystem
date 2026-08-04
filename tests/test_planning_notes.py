"""Task 4 — filing routes a planning note by its pinned type (ADR-021).

The pin decides where the note lands, which status it carries, and which links
have to resolve. These drive the whole loop through the CLI, so the id a goal is
filed under is the same string the next capture passes to `--goal`.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.draft_notes import LINK_TARGET, DraftNoteError, DraftNoteStore
from metis.identifiers import note_slug
from metis.model_adapters import ModelResponse


GOAL_TEXT = "Establish a health baseline."
PROJECT_TEXT = "Stand up the weekly measurement routine."
PLAIN_TEXT = "Build a review workflow."
CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
GOAL_NOTE = (
    b"---\n"
    b'id: "goal.health-baseline-8f14e45f"\n'
    b'type: "goal"\n'
    b'title: "Establish a health baseline"\n'
    b"horizon: annual\n"
    b"status: active\n"
    b'capture_id: "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"\n'
    b"evidence:\n"
    b'  capture: "evidence/8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70/raw.txt"\n'
    b"links: []\n"
    b"---\n\n"
    b"Measure the starting point before changing anything.\n"
)
GOAL_NOTE_PATH = "vault/goals/goal.health-baseline-8f14e45f.md"


class PlanningAdapter:
    """One title per proposal, set by the test, so a filed id is predictable."""

    def __init__(self) -> None:
        self.title = "Establish a health baseline"

    def classify(self, prompt: str) -> ModelResponse:
        return ModelResponse(
            "fake-classification-model",
            '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}',
        )

    def propose(self, prompt: str) -> ModelResponse:
        return ModelResponse(
            "fake-proposal-model",
            json.dumps(
                {
                    "title": self.title,
                    "body": "Measure the starting point before changing anything.",
                    "reason": "The capture asks for a baseline.",
                    "uncertainties": [],
                },
                separators=(",", ":"),
            ),
        )


class PlanningFilingFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.adapter = PlanningAdapter()

    def _run(self, argv: list[str]) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            main(
                argv,
                runtime_root=self.root,
                model_adapter_factory=lambda: self.adapter,
            )
        return json.loads(stdout.getvalue() or stderr.getvalue())

    def _proposed(self, text: str, *flags: str, title: str) -> str:
        self.adapter.title = title
        capture_id = self._run(["capture", *flags, text])["capture_id"]
        self._run(["classify", capture_id])
        self._run(["propose", capture_id])
        return capture_id

    def _approve(self, capture_id: str, links: bytes = b"links: []\n") -> None:
        draft = self.root / "vault" / "notes" / "proposed" / f"note.{capture_id}.md"
        draft.write_bytes(
            draft.read_bytes()
            .replace(b"links: []\n", links, 1)
            .replace(b"status: proposed\n", b"status: approved\n", 1)
        )
        self._run(["approvals"])

    def _goal(self) -> tuple[str, str]:
        """File one goal through the real loop and return its capture and id."""
        capture_id = self._proposed(
            GOAL_TEXT, "--as", "goal", title="Establish a health baseline"
        )
        self._approve(capture_id)
        filed = self._run(["file", capture_id])
        self.assertEqual(filed["status"], "filed", filed)
        return capture_id, Path(filed["filed_path"]).stem

    def _frontmatter(self, relative_path: str) -> bytes:
        return (self.root / relative_path).read_bytes().split(b"---\n\n", 1)[0]


class PlanningFilingTests(PlanningFilingFixture):
    def test_a_pinned_goal_files_under_vault_goals_with_empty_links(self) -> None:
        capture_id = self._proposed(
            GOAL_TEXT, "--as", "goal", title="Establish a health baseline"
        )
        self._approve(capture_id)

        filed = self._run(["file", capture_id])

        note_id = f"goal.establish-a-health-baseline-{capture_id.replace('-', '')[:8]}"
        self.assertEqual(filed["status"], "filed")
        self.assertEqual(filed["links"], [])
        self.assertEqual(filed["filed_path"], f"vault/goals/{note_id}.md")
        frontmatter = self._frontmatter(filed["filed_path"])
        self.assertIn(f'id: "{note_id}"\n'.encode(), frontmatter)
        self.assertIn(b'type: "goal"\n', frontmatter)
        self.assertIn(b"status: active\n", frontmatter)
        self.assertIn(b"horizon: annual\n", frontmatter)
        self.assertIn(f'capture_id: "{capture_id}"\n'.encode(), frontmatter)
        self.assertIn(
            f'  capture: "evidence/{capture_id}/raw.txt"\n'.encode(),
            frontmatter,
        )
        self.assertIn(b"links: []\n", frontmatter)
        self.assertFalse((self.root / "vault" / "notes" / "filed").exists())

    def test_a_pinned_project_files_under_vault_projects_naming_its_goal(self) -> None:
        _, goal_id = self._goal()

        capture_id = self._proposed(
            PROJECT_TEXT,
            "--as",
            "project",
            "--goal",
            goal_id,
            title="Weekly measurement routine",
        )
        self._approve(capture_id)
        filed = self._run(["file", capture_id])

        note_id = f"proj.weekly-measurement-routine-{capture_id.replace('-', '')[:8]}"
        self.assertEqual(filed["status"], "filed")
        self.assertEqual(filed["filed_path"], f"vault/projects/{note_id}.md")
        frontmatter = self._frontmatter(filed["filed_path"])
        self.assertIn(f'id: "{note_id}"\n'.encode(), frontmatter)
        self.assertIn(b'type: "project"\n', frontmatter)
        self.assertIn(b"status: active\n", frontmatter)
        self.assertIn(f'goal: "[[{goal_id}]]"\n'.encode(), frontmatter)
        self.assertIn(f'capture_id: "{capture_id}"\n'.encode(), frontmatter)
        self.assertIn(b"links: []\n", frontmatter)

    def test_a_project_whose_parent_goal_is_not_filed_is_refused(self) -> None:
        capture_id = self._proposed(
            PROJECT_TEXT,
            "--as",
            "project",
            "--goal",
            "goal.never-filed",
            title="Weekly measurement routine",
        )
        self._approve(capture_id)

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "filing.parent_goal_unresolvable")
        self.assertIn("goal.never-filed", result["message"])
        self.assertFalse((self.root / "vault" / "projects").exists())

    def test_a_project_may_not_name_another_project_as_its_parent(self) -> None:
        _, goal_id = self._goal()
        first = self._proposed(
            PROJECT_TEXT,
            "--as",
            "project",
            "--goal",
            goal_id,
            title="Weekly measurement routine",
        )
        self._approve(first)
        project_id = Path(self._run(["file", first])["filed_path"]).stem

        second = self._proposed(
            "Track the weekly numbers.",
            "--as",
            "project",
            "--goal",
            project_id,
            title="Weekly number tracking",
        )
        self._approve(second)
        result = self._run(["file", second])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "filing.parent_goal_unresolvable")
        self.assertEqual(
            sorted(path.name for path in (self.root / "vault" / "projects").iterdir()),
            [f"{project_id}.md"],
        )

    def test_a_filed_goal_resolves_a_typed_notes_link(self) -> None:
        """The point of creating goals: a typed note links to one Metis wrote."""
        _, goal_id = self._goal()

        capture_id = self._proposed(PLAIN_TEXT, title="Review the governed workflow")
        self._approve(capture_id, links=f'links:\n  - "[[{goal_id}]]"\n'.encode())
        filed = self._run(["file", capture_id])

        self.assertEqual(filed["status"], "filed")
        self.assertEqual(filed["links"], [goal_id])
        self.assertEqual(
            filed["filed_path"],
            f"vault/notes/filed/note.{capture_id}.md",
        )

    def test_a_goal_link_that_does_not_resolve_still_blocks_the_commit(self) -> None:
        capture_id = self._proposed(
            GOAL_TEXT, "--as", "goal", title="Establish a health baseline"
        )
        self._approve(capture_id, links=b'links:\n  - "[[goal.absent]]"\n')

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "filing.link_unresolvable")
        self.assertFalse((self.root / "vault" / "goals").exists())

    def test_a_plain_capture_still_requires_a_link_to_file(self) -> None:
        capture_id = self._proposed(PLAIN_TEXT, title="Review the governed workflow")
        self._approve(capture_id)

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "filing.links_absent")
        self.assertFalse((self.root / "vault" / "notes" / "filed").exists())

    def test_an_unapproved_planning_note_is_refused(self) -> None:
        capture_id = self._proposed(
            GOAL_TEXT, "--as", "goal", title="Establish a health baseline"
        )

        result = self._run(["file", capture_id])

        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["reason"], "filing.not_approved")
        self.assertFalse((self.root / "vault" / "goals").exists())

    def test_duplicate_replay_creates_one_planning_note(self) -> None:
        capture_id, note_id = self._goal()

        self._run(["capture", "--as", "goal", GOAL_TEXT])
        replayed = self._run(["file", capture_id])

        self.assertEqual(replayed["status"], "duplicate")
        self.assertEqual(replayed["filed_path"], f"vault/goals/{note_id}.md")
        self.assertEqual(
            sorted(path.name for path in (self.root / "vault" / "goals").iterdir()),
            [f"{note_id}.md"],
        )


class PlanningNoteIdTests(unittest.TestCase):
    """The id is pure in title and capture_id, so filing never probes for a name."""

    def test_the_slug_reads_from_the_title_and_the_capture(self) -> None:
        slug = note_slug("Establish a health baseline", CAPTURE_ID)

        self.assertEqual(slug, "establish-a-health-baseline-8f14e45f")
        self.assertEqual(slug, note_slug("Establish a health baseline", CAPTURE_ID))

    def test_a_different_capture_of_the_same_title_gets_a_different_id(self) -> None:
        self.assertNotEqual(
            note_slug("Establish a health baseline", CAPTURE_ID),
            note_slug(
                "Establish a health baseline",
                "1b4e28ba-2fa1-4d8f-b2c9-7e0a5d3c1f66",
            ),
        )

    def test_an_unpromising_title_still_yields_a_linkable_id(self) -> None:
        for title in ("", "// ---- //", "Établir une référence", "x" * 200):
            with self.subTest(title=title):
                note_id = f"goal.{note_slug(title, CAPTURE_ID)}"
                self.assertIsNotNone(LINK_TARGET.fullmatch(note_id))
                self.assertLessEqual(len(note_id), 64)
                self.assertTrue(note_id.endswith("8f14e45f"))


class PlanningNoteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.store = DraftNoteStore(self.root, stage="goals")

    def test_a_planning_note_without_provenance_fails_validation(self) -> None:
        for missing in (
            f'capture_id: "{CAPTURE_ID}"\n'.encode("utf-8"),
            b"evidence:\n",
            f'  capture: "evidence/{CAPTURE_ID}/raw.txt"\n'.encode("utf-8"),
        ):
            with self.subTest(missing=missing):
                with self.assertRaises(DraftNoteError):
                    self.store.create(
                        GOAL_NOTE_PATH,
                        GOAL_NOTE.replace(missing, b"", 1),
                    )
                self.assertFalse((self.root / GOAL_NOTE_PATH).exists())

    def test_a_goal_stage_refuses_a_path_outside_vault_goals(self) -> None:
        for path in (
            f"vault/notes/filed/note.{CAPTURE_ID}.md",
            "vault/projects/goal.health-baseline-8f14e45f.md",
            "vault/goals/proj.health-baseline-8f14e45f.md",
            "vault/goals/nested/goal.health-baseline-8f14e45f.md",
        ):
            with self.subTest(path=path):
                with self.assertRaises(DraftNoteError):
                    self.store.create(path, GOAL_NOTE)

    def test_a_valid_goal_note_is_written_and_read_back(self) -> None:
        record = self.store.create(GOAL_NOTE_PATH, GOAL_NOTE)

        self.assertEqual(record.draft_path, GOAL_NOTE_PATH)
        self.assertEqual(record.observed_links, ())
        self.assertEqual((self.root / GOAL_NOTE_PATH).read_bytes(), GOAL_NOTE)


if __name__ == "__main__":
    unittest.main()

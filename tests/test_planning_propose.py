"""Task 3 — the propose and approval paths carry the pin into the draft.

`parent_goal_id` lives only in immutable evidence, so every place that renders
or re-renders a draft has to read it from there. A site that forgets produces a
byte mismatch, which is a refused draft rather than a wrong one.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.model_adapters import ModelResponse


GOAL_ID = "goal.health-baseline"

CLASSIFICATION_RAW = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)
PROPOSAL_RAW = json.dumps(
    {
        "title": "Establish a health baseline",
        "body": "Measure the starting point before changing anything.",
        "reason": "The capture asks for a baseline.",
        "uncertainties": [],
    },
    separators=(",", ":"),
)


class PipelineAdapter:
    def classify(self, prompt: str) -> ModelResponse:
        return ModelResponse("fake-classification-model", CLASSIFICATION_RAW)

    def propose(self, prompt: str) -> ModelResponse:
        return ModelResponse("fake-proposal-model", PROPOSAL_RAW)


class PlanningProposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.adapter = PipelineAdapter()

    def _run(self, argv: list[str]) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            main(argv, runtime_root=self.root, model_adapter_factory=lambda: self.adapter)
        return json.loads(stdout.getvalue() or stderr.getvalue())

    def _propose(self, *flags: str) -> tuple[str, dict]:
        capture_id = self._run(["capture", *flags, "Establish a health baseline"])[
            "capture_id"
        ]
        self._run(["classify", capture_id])
        return capture_id, self._run(["propose", capture_id])

    def _draft(self, capture_id: str) -> str:
        path = self.root / "vault" / "notes" / "proposed" / f"note.{capture_id}.md"
        return path.read_text(encoding="utf-8")

    def test_a_pinned_goal_proposes_a_draft_with_its_horizon(self) -> None:
        capture_id, result = self._propose("--as", "goal")

        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["note_type"], "goal")
        self.assertIn("horizon: annual\n", self._draft(capture_id))

    def test_a_pinned_project_proposes_a_draft_naming_its_parent_goal(self) -> None:
        capture_id, result = self._propose("--as", "project", "--goal", GOAL_ID)

        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["note_type"], "project")
        self.assertIn(f'goal: "[[{GOAL_ID}]]"\n', self._draft(capture_id))

    def test_a_plain_capture_proposes_an_unchanged_draft(self) -> None:
        capture_id, result = self._propose()

        self.assertEqual(result["note_type"], "idea")
        draft = self._draft(capture_id)
        self.assertNotIn("horizon:", draft)
        self.assertNotIn("goal:", draft)

    def test_approvals_reads_a_planning_draft_it_did_not_render(self) -> None:
        """`metis approvals` re-renders the expected bytes; it needs the pin too."""
        capture_id, _ = self._propose("--as", "project", "--goal", GOAL_ID)
        path = self.root / "vault" / "notes" / "proposed" / f"note.{capture_id}.md"
        path.write_bytes(
            path.read_bytes().replace(b"status: proposed\n", b"status: approved\n")
        )

        result = self._run(["approvals"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [decision["status"] for decision in result["decisions"]], ["approved"]
        )


if __name__ == "__main__":
    unittest.main()

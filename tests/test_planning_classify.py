"""Task 2 — a capture-time pin overrides the classifier's type (ADR-021).

Planning identity is the owner's intent, not a model proposal. Classification
still runs for sensitivity and confidence, the model's raw response is still
preserved byte-for-byte, and the model may never select a planning type itself.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.data_access import SQLiteStateStore
from metis.model_adapters import ModelResponse


GOAL_ID = "goal.health-baseline"
TEXT = "Establish a health baseline"


class TypedAdapter:
    """Answers with a plain typed note, whatever the capture was pinned as."""

    def __init__(self, candidate_type: str = "idea") -> None:
        self.candidate_type = candidate_type
        self.classification_calls = 0

    def classify(self, prompt: str) -> ModelResponse:
        self.classification_calls += 1
        raw = json.dumps(
            {
                "candidate_type": self.candidate_type,
                "sensitivity": "normal",
                "confidence": 0.82,
            },
            separators=(",", ":"),
        )
        return ModelResponse("fake-classification-model", raw)


class PlanningClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.adapter = TypedAdapter()

    def _run(self, argv: list[str]) -> dict:
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            main(argv, runtime_root=self.root, model_adapter_factory=lambda: self.adapter)
        return json.loads(stdout.getvalue())

    def _capture(self, *flags: str) -> str:
        return self._run(["capture", *flags, TEXT])["capture_id"]

    def _classification(self, capture_id: str):
        with SQLiteStateStore(self.root / "state" / "metis.db") as store:
            store.initialize()
            return store.find_classification_by_capture_id(capture_id)

    def test_goal_pin_overrides_the_model_candidate_type(self) -> None:
        capture_id = self._capture("--as", "goal")

        result = self._run(["classify", capture_id])

        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["candidate_type"], "goal")
        self.assertEqual(result["routing"], "proposal:goal")
        self.assertEqual(self._classification(capture_id).candidate_type, "goal")

    def test_project_pin_overrides_the_model_candidate_type(self) -> None:
        capture_id = self._capture("--as", "project", "--goal", GOAL_ID)

        result = self._run(["classify", capture_id])

        self.assertEqual(result["candidate_type"], "project")
        self.assertEqual(result["routing"], "proposal:project")

    def test_pin_override_preserves_the_model_response_verbatim(self) -> None:
        """ADR-003: the pin changes the interpretation, never the evidence."""
        capture_id = self._capture("--as", "goal")

        result = self._run(["classify", capture_id])

        raw = (self.root / result["raw_response_path"]).read_text(encoding="utf-8")
        self.assertIn('"candidate_type":"idea"', raw)

    def test_pin_does_not_override_sensitivity_or_confidence(self) -> None:
        capture_id = self._capture("--as", "goal")

        result = self._run(["classify", capture_id])

        self.assertEqual(result["sensitivity"], "normal")
        self.assertEqual(result["confidence"], 0.82)

    def test_plain_capture_still_uses_the_model_candidate_type(self) -> None:
        capture_id = self._capture()

        result = self._run(["classify", capture_id])

        self.assertEqual(result["candidate_type"], "idea")
        self.assertEqual(result["routing"], "proposal:idea")

    def test_classifying_a_pinned_capture_twice_is_a_duplicate(self) -> None:
        """The replay path re-parses the raw response; the pin must apply there too."""
        capture_id = self._capture("--as", "goal")
        first = self._run(["classify", capture_id])
        second = self._run(["classify", capture_id])

        self.assertEqual(first["status"], "classified")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["reason"], "already_classified")
        self.assertEqual(second["candidate_type"], "goal")
        self.assertEqual(self.adapter.classification_calls, 1)

    def test_the_model_may_not_select_a_planning_type(self) -> None:
        """Planning identity is owner intent — a model that claims it is rejected."""
        for candidate_type in ("goal", "project"):
            with self.subTest(candidate_type=candidate_type):
                root = self.root / f"model-{candidate_type}"
                adapter = TypedAdapter(candidate_type)
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    main(["capture", TEXT], runtime_root=root)
                    capture_id = json.loads(stdout.getvalue())["capture_id"]
                stdout = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(stdout):
                    main(
                        ["classify", capture_id],
                        runtime_root=root,
                        model_adapter_factory=lambda: adapter,
                    )
                result = json.loads(stdout.getvalue())

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["reason"], "model_response_invalid")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.data_access import SQLiteStateStore
from metis.draft_notes import DraftNoteStore, DraftStatus, render_proposed_draft
from metis.model_adapters import ModelResponse

from tests.data_access.inspection import audit_event_rows, table_row_count


CLASSIFICATION_RAW = (
    '{"candidate_type":"idea","sensitivity":"normal","confidence":0.82}'
)
PROPOSAL_RAW = json.dumps(
    {
        "title": "Review the governed workflow",
        "body": "Define the reviewable workflow before permanent filing.",
        "reason": "The capture asks for a governed proposal workflow.",
        "uncertainties": ["The later approval cadence is not specified."],
    },
    separators=(",", ":"),
)


class PipelineAdapter:
    def __init__(self) -> None:
        self.classification_calls = 0
        self.proposal_calls = 0

    def classify(self, prompt: str) -> ModelResponse:
        self.classification_calls += 1
        self.assert_prompt(prompt, "Captured text as a JSON string:")
        return ModelResponse("fake-classification-model", CLASSIFICATION_RAW)

    def propose(self, prompt: str) -> ModelResponse:
        self.proposal_calls += 1
        self.assert_prompt(prompt, "Validated classification as a JSON object:")
        self.assert_prompt(prompt, '"candidate_type":"idea"')
        return ModelResponse("fake-proposal-model", PROPOSAL_RAW)

    @staticmethod
    def assert_prompt(prompt: str, expected: str) -> None:
        if expected not in prompt:
            raise AssertionError(f"packaged prompt omitted {expected}")


class ProposalIntegrationTests(unittest.TestCase):
    def test_capture_classify_propose_and_replay_stop_before_step_five(self):
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            captured = self._run(root, adapter, ["capture", "Build a review workflow."])
            capture_id = captured["capture_id"]
            classified = self._run(root, adapter, ["classify", capture_id])
            proposed = self._run(root, adapter, ["propose", capture_id])
            replayed = self._run(root, adapter, ["propose", capture_id])

            self.assertEqual(captured["status"], "captured")
            self.assertEqual(classified["status"], "classified")
            self.assertEqual(proposed["status"], "proposed")
            self.assertEqual(replayed["status"], "duplicate")
            self.assertEqual(adapter.classification_calls, 1)
            self.assertEqual(adapter.proposal_calls, 1)
            self.assertEqual(replayed["proposal_id"], proposed["proposal_id"])
            self.assertEqual(replayed["draft_path"], proposed["draft_path"])

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                intake = store.find_intake_by_capture_id(capture_id)
                classification = store.find_classification_by_capture_id(capture_id)
                proposal = store.find_proposal_by_capture_id(capture_id)
                self.assertEqual(intake.state, "awaiting_approval")
                self.assertIsNone(intake.failure_reason)
                self.assertEqual(proposal.state, "pending")
                self.assertEqual(proposal.proposed_links, "[]")
                self.assertEqual(proposal.confidence, classification.confidence)
                self.assertEqual(proposal.sensitivity, classification.sensitivity)
                self.assertEqual(table_row_count(store, "intake"), 1)
                self.assertEqual(table_row_count(store, "classification"), 1)
                self.assertEqual(table_row_count(store, "proposal"), 1)
                self.assertEqual(table_row_count(store, "proposal_reservation"), 0)
                self.assertEqual(table_row_count(store, "approval"), 0)
                # One event per transition, and the replay is a refused write.
                self.assertEqual(
                    tuple(
                        (event.action, event.outcome)
                        for event in audit_event_rows(store)
                    ),
                    (
                        ("capture.written", "success"),
                        ("classification.started", "success"),
                        ("classification.completed", "success"),
                        ("proposal.reserved", "success"),
                        ("proposal.recorded", "success"),
                        ("draft.registered", "success"),
                        ("command.propose", "refused"),
                    ),
                )

                body = (root / proposal.body_path).read_bytes()
                draft = DraftNoteStore(root).validate(
                    proposal.draft_note_path,
                    render_proposed_draft(proposal, body),
                )
                self.assertEqual(draft.observed_status, DraftStatus.PROPOSED)
                self.assertEqual(
                    proposal.evidence_refs,
                    json.dumps(
                        [
                            f"evidence/{capture_id}/raw.txt",
                            (
                                "classification-evidence/"
                                f"{classification.classification_id}/raw-response.txt"
                            ),
                            (
                                "proposal-evidence/"
                                f"{proposal.proposal_id}/raw-response.txt"
                            ),
                        ],
                        separators=(",", ":"),
                    ),
                )

            proposed_notes = list((root / "vault" / "notes" / "proposed").iterdir())
            self.assertEqual(len(proposed_notes), 1)
            self.assertFalse((root / "vault" / "notes" / "filed").exists())

    def _run(self, root: Path, adapter: PipelineAdapter, argv: list[str]) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                argv,
                runtime_root=root,
                model_adapter_factory=lambda: adapter,
            )
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

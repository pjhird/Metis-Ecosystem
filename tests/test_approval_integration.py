from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.cli import main
from metis.data_access import SQLiteStateStore
from tests.data_access.inspection import approval_rows, table_row_count
from tests.test_proposal_integration import PipelineAdapter


class ApprovalIntegrationTests(unittest.TestCase):
    def test_capture_classify_propose_approve_stops_before_filing(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._run(root, adapter, ["capture", "Build a workflow."])[
                "capture_id"
            ]
            self._run(root, adapter, ["classify", capture_id])
            proposed = self._run(root, adapter, ["propose", capture_id])

            pending = self._run(root, adapter, ["approvals"])
            self.assertEqual(pending["status"], "completed")
            self.assertEqual(len(pending["decisions"]), 1)
            self.assertEqual(pending["decisions"][0]["status"], "pending")

            draft = root / proposed["draft_path"]
            before = draft.read_bytes()
            draft.write_bytes(
                before.replace(b"status: proposed\n", b"status: approved\n", 1)
            )

            approved = self._run(root, adapter, ["approvals"])
            replayed = self._run(root, adapter, ["approvals"])

            self.assertEqual(approved["status"], "completed")
            decision = approved["decisions"][0]
            self.assertEqual(decision["status"], "approved")
            self.assertEqual(decision["decision"], "approved")
            self.assertEqual(decision["observed_status"], "approved")
            self.assertEqual(decision["approver"], "human:owner")
            self.assertEqual(decision["intake_state"], "approved")
            self.assertEqual(decision["proposal_id"], proposed["proposal_id"])
            self.assertEqual(replayed["decisions"], [])
            self.assertEqual(adapter.classification_calls, 1)
            self.assertEqual(adapter.proposal_calls, 1)

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                intake = store.find_intake_by_capture_id(capture_id)
                proposal = store.find_proposal_by_capture_id(capture_id)
                rows = approval_rows(store)
                self.assertEqual(intake.state, "approved")
                self.assertIsNone(intake.failure_reason)
                self.assertEqual(proposal.state, "approved")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].proposal_id, proposal.proposal_id)
                self.assertEqual(rows[0].decision, "approved")
                self.assertIsNone(rows[0].committed_at)
                self.assertIsNone(rows[0].revoked_at)
                self.assertEqual(table_row_count(store, "approval"), 1)
                self.assertEqual(table_row_count(store, "audit_event"), 0)

            self.assertEqual(
                draft.read_bytes(),
                before.replace(b"status: proposed\n", b"status: approved\n", 1),
            )
            self.assertFalse((root / "vault" / "notes" / "filed").exists())
            self.assertEqual(
                len(list((root / "vault" / "notes" / "proposed").iterdir())),
                1,
            )

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

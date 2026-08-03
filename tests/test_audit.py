from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.approval import ApprovalService
from metis.audit import AuditTrail
from metis.classification_evidence import ClassificationEvidenceStore
from metis.cli import main
from metis.data_access import (
    AuditEventRecord,
    SQLiteStateStore,
    StateStoreError,
    StateTransitionRefused,
)
from metis.draft_notes import DraftNoteStore
from metis.evidence import EvidenceStore
from metis.filing import FilingService
from metis.identifiers import is_ulid
from metis.model_adapters import ModelRequestError
from metis.proposal_content import ProposalContentStore
from metis.proposal_evidence import ProposalEvidenceStore

from tests.data_access.inspection import audit_event_rows
from tests.test_filing_integration import CAPTURED_TEXT, GOAL_NOTE
from tests.test_proposal_integration import PipelineAdapter


# The state path one capture walks from typed text to a filed note. One event
# each, in order, is what REQ-ORCH-004 asks the audit trail to be.
MVP_LOOP_TRAIL = (
    ("capture.written", "success"),
    ("classification.started", "success"),
    ("classification.completed", "success"),
    ("proposal.reserved", "success"),
    ("proposal.recorded", "success"),
    ("draft.registered", "success"),
    ("approval.detected", "success"),
    ("note.committed", "success"),
)


class FailingClassificationAdapter(PipelineAdapter):
    def classify(self, prompt: str):
        self.classification_calls += 1
        raise ModelRequestError("model_request_failed", "the provider is unreachable")


class RacedTransitionStore:
    """Lose the compare-and-swap the way a competing process would."""

    def __init__(self, store: SQLiteStateStore, method: str, capture_id: str) -> None:
        self._store = store
        self._method = method
        self._capture_id = capture_id

    def __getattr__(self, name):
        if name != self._method:
            return getattr(self._store, name)

        def refuse(*_: object, **__: object):
            raise StateTransitionRefused(
                "simulated lost race",
                self._store.find_intake_by_capture_id(self._capture_id),
            )

        return refuse


class AuditTrailTests(unittest.TestCase):
    def test_every_material_transition_emits_exactly_one_event(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)

            self._run(root, adapter, ["file", capture_id])

            events = self._trail(root)
            self.assertEqual(
                tuple((event.action, event.outcome) for event in events),
                MVP_LOOP_TRAIL,
            )
            for event in events:
                with self.subTest(action=event.action):
                    self.assertTrue(is_ulid(event.event_id))
                    self.assertEqual(event.trace_id, capture_id)
                    self.assertEqual(event.capture_id, capture_id)
                    self.assertIsInstance(json.loads(event.detail), dict)
            self.assertEqual(
                len({event.event_id for event in events}),
                len(MVP_LOOP_TRAIL),
            )
            # The human decided; the orchestrator only executed the transition.
            actors = {event.action: event.actor for event in events}
            self.assertEqual(actors["approval.detected"], "human:owner")
            self.assertEqual(actors["capture.written"], "orchestrator")
            self.assertEqual(actors["note.committed"], "orchestrator")

    def test_a_refused_write_is_recorded_as_refused_not_failure(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])[
                "capture_id"
            ]
            self._run(root, adapter, ["classify", capture_id])
            self._run(root, adapter, ["propose", capture_id])

            stdout, stderr, code = self._capture(root, adapter, ["file", capture_id])

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["status"], "refused")
            refusal = self._trail(root)[-1]
            self.assertEqual(refusal.action, "command.file")
            self.assertEqual(refusal.outcome, "refused")
            self.assertEqual(refusal.capture_id, capture_id)
            self.assertEqual(
                json.loads(refusal.detail)["reason"],
                "filing.not_approved",
            )
            self.assertFalse((root / "vault" / "notes" / "filed").exists())

    def test_a_replayed_filing_records_one_refusal_and_no_second_note(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)
            self._run(root, adapter, ["file", capture_id])

            replayed = self._run(root, adapter, ["file", capture_id])

            self.assertEqual(replayed["status"], "duplicate")
            events = self._trail(root)
            committed = [
                event for event in events if event.action == "note.committed"
            ]
            self.assertEqual(len(committed), 1)
            self.assertEqual(
                (events[-1].action, events[-1].outcome),
                ("command.file", "refused"),
            )
            self.assertEqual(
                sorted(
                    path.name
                    for path in (root / "vault" / "notes" / "filed").iterdir()
                ),
                [f"note.{capture_id}.md"],
            )

    def test_a_recorded_failure_emits_one_failure_event(self) -> None:
        adapter = FailingClassificationAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])[
                "capture_id"
            ]

            _, stderr, code = self._capture(root, adapter, ["classify", capture_id])

            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stderr)["status"], "failed")
            self.assertEqual(
                tuple((event.action, event.outcome) for event in self._trail(root)),
                (
                    ("capture.written", "success"),
                    ("classification.started", "success"),
                    ("classification.failed", "failure"),
                ),
            )

    def test_a_refused_transition_writes_no_event(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._approve(root, adapter)
            filed = self._run(root, adapter, ["file", capture_id])

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                before = audit_event_rows(store)
                with self.assertRaises(StateTransitionRefused):
                    store.record_filing(
                        capture_id,
                        filed["proposal_id"],
                        filed["approval_id"],
                        "2026-08-02T23:00:00Z",
                        audit=AuditTrail(store).event(
                            "note.committed",
                            "success",
                            capture_id=capture_id,
                        ),
                    )
                self.assertEqual(audit_event_rows(store), before)

    def test_an_invalid_event_rolls_back_its_transition(self) -> None:
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])[
                "capture_id"
            ]

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                before = audit_event_rows(store)
                with self.assertRaises(StateStoreError):
                    store.begin_classification(
                        capture_id,
                        "2026-08-02T23:00:00Z",
                        audit=self._invalid_event(detail="not a JSON object"),
                    )
                self.assertEqual(
                    store.find_intake_by_capture_id(capture_id).state,
                    "captured",
                )
                self.assertEqual(audit_event_rows(store), before)

    def test_an_unusable_event_is_refused_by_the_data_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                for field, value in (
                    ("event_id", "not-a-ulid"),
                    ("trace_id", ""),
                    ("actor", "agent:reviewer"),
                    ("detail", "[]"),
                    ("detail", "not a JSON object"),
                    ("created_at", "2026-08-02 23:00:00"),
                ):
                    with self.subTest(field=field, value=value):
                        with self.assertRaises(StateStoreError):
                            store.append_audit_event(
                                self._invalid_event(**{field: value})
                            )
                self.assertEqual(audit_event_rows(store), [])

    def test_a_rolled_back_transition_is_recorded_as_refused(self) -> None:
        """The transaction carried no event with it, so one is appended after."""
        adapter = PipelineAdapter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            capture_id = self._awaiting_approval(root, adapter)

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                raced = RacedTransitionStore(store, "record_approval", capture_id)
                run = ApprovalService(
                    raced,
                    ProposalContentStore(root),
                    DraftNoteStore(root),
                    EvidenceStore(root),
                    root,
                ).review()
                trail = audit_event_rows(store)

                self.assertEqual(run.decisions[0].status.value, "failed")
                self.assertEqual(
                    (trail[-1].action, trail[-1].outcome, trail[-1].actor),
                    ("approval.detected", "refused", "human:owner"),
                )
                self.assertEqual(
                    store.find_intake_by_capture_id(capture_id).state,
                    "awaiting_approval",
                )

            self._run(root, adapter, ["approvals"])

            with SQLiteStateStore(root / "state" / "metis.db") as store:
                store.initialize()
                raced = RacedTransitionStore(store, "record_filing", capture_id)
                result = FilingService(
                    raced,
                    EvidenceStore(root),
                    ClassificationEvidenceStore(root),
                    ProposalEvidenceStore(root),
                    ProposalContentStore(root),
                    DraftNoteStore(root),
                    DraftNoteStore(root, stage="filed"),
                    root,
                ).file(capture_id)
                trail = audit_event_rows(store)

                self.assertEqual(result.status.value, "failed")
                self.assertEqual(result.reason, "filing.state_undetermined")
                self.assertEqual(
                    (trail[-1].action, trail[-1].outcome),
                    ("note.committed", "refused"),
                )
                self.assertEqual(
                    store.find_intake_by_capture_id(capture_id).state,
                    "approved",
                )

    def _invalid_event(self, **overrides: str) -> AuditEventRecord:
        fields = {
            "event_id": "01K1D5Q5M00000000000000000",
            "trace_id": "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70",
            "capture_id": None,
            "actor": "orchestrator",
            "action": "capture.written",
            "outcome": "success",
            "detail": "{}",
            "created_at": "2026-08-02T23:00:00Z",
        }
        fields.update(overrides)
        return AuditEventRecord(**fields)

    def _trail(self, root: Path) -> list[AuditEventRecord]:
        with SQLiteStateStore(root / "state" / "metis.db") as store:
            store.initialize()
            return audit_event_rows(store)

    def _approve(self, root: Path, adapter: PipelineAdapter) -> str:
        capture_id = self._awaiting_approval(root, adapter)
        self._run(root, adapter, ["approvals"])
        return capture_id

    def _awaiting_approval(self, root: Path, adapter: PipelineAdapter) -> str:
        """Everything up to the human's edit, which is written here."""
        capture_id = self._run(root, adapter, ["capture", CAPTURED_TEXT])["capture_id"]
        self._run(root, adapter, ["classify", capture_id])
        proposed = self._run(root, adapter, ["propose", capture_id])
        goal = root / "vault" / "goals" / "goal.health-baseline.md"
        goal.parent.mkdir(parents=True, exist_ok=True)
        goal.write_bytes(GOAL_NOTE)
        draft = root / proposed["draft_path"]
        draft.write_bytes(
            draft.read_bytes()
            .replace(b"links: []\n", b'links:\n  - "[[goal.health-baseline]]"\n', 1)
            .replace(b"status: proposed\n", b"status: approved\n", 1)
        )
        return capture_id

    def _capture(
        self,
        root: Path,
        adapter: PipelineAdapter,
        argv: list[str],
    ) -> tuple[str, str, int]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv, runtime_root=root, model_adapter_factory=lambda: adapter)
        return stdout.getvalue(), stderr.getvalue(), code

    def _run(self, root: Path, adapter: PipelineAdapter, argv: list[str]) -> dict:
        stdout, stderr, code = self._capture(root, adapter, argv)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        return json.loads(stdout)


if __name__ == "__main__":
    unittest.main()

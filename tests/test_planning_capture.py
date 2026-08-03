"""Task 1 — pinned capture for planning notes (ADR-021).

`metis capture --as goal|project` records the human's planning intent as
durable capture metadata, written with the evidence and therefore before any
model call (ADR-003). The pin is capture-time intent, so a replay of the same
text under a different pin is refused rather than silently reinterpreted.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from metis.capture import CaptureService, CaptureStatus
from metis.cli import main
from metis.data_access import SQLiteStateStore
from metis.evidence import EvidenceStore


GOAL_ID = "goal.health-baseline"
TEXT = "Establish a health baseline"


class PlanningCaptureCliTest(unittest.TestCase):
    """The flags exist, constrain each other, and never write evidence when misused."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            return_code = main(argv, runtime_root=self.runtime_root)
        return return_code, stdout.getvalue()

    def _usage_error(self, argv: list[str]) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(argv, runtime_root=self.runtime_root)
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_capture_as_project_requires_goal_flag(self) -> None:
        self._usage_error(["capture", "--as", "project", TEXT])

    def test_goal_flag_requires_as_project(self) -> None:
        self._usage_error(["capture", "--goal", GOAL_ID, TEXT])
        self._usage_error(["capture", "--as", "goal", "--goal", GOAL_ID, TEXT])

    def test_as_accepts_only_goal_and_project(self) -> None:
        self._usage_error(["capture", "--as", "idea", TEXT])

    def test_goal_flag_rejects_an_unsafe_identifier(self) -> None:
        self._usage_error(["capture", "--as", "project", "--goal", "../escape", TEXT])

    def test_capture_as_goal_succeeds(self) -> None:
        return_code, stdout = self._run(["capture", "--as", "goal", TEXT])

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["status"], "captured")


class PlanningPinPersistenceTest(unittest.TestCase):
    """The pin is durable evidence metadata, written with the raw input."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name)

    def _capture(self, text: str, **pin: object) -> object:
        with SQLiteStateStore(self.runtime_root / "state" / "metis.db") as store:
            store.initialize()
            service = CaptureService(store, EvidenceStore(self.runtime_root))
            return service.capture(text, **pin)

    def _metadata(self, capture_id: str) -> dict:
        path = self.runtime_root / "evidence" / capture_id / "meta.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_plain_capture_records_a_null_pin(self) -> None:
        result = self._capture(TEXT)

        metadata = self._metadata(result.capture_id)
        self.assertIsNone(metadata["type_pin"])
        self.assertIsNone(metadata["parent_goal_id"])

    def test_capture_as_goal_records_the_pin(self) -> None:
        result = self._capture(TEXT, type_pin="goal")

        metadata = self._metadata(result.capture_id)
        self.assertEqual(metadata["type_pin"], "goal")
        self.assertIsNone(metadata["parent_goal_id"])

    def test_capture_as_project_records_the_pin_and_parent_goal(self) -> None:
        result = self._capture(TEXT, type_pin="project", parent_goal_id=GOAL_ID)

        metadata = self._metadata(result.capture_id)
        self.assertEqual(metadata["type_pin"], "project")
        self.assertEqual(metadata["parent_goal_id"], GOAL_ID)

    def test_pin_is_readable_from_the_validated_evidence_record(self) -> None:
        """Classify and file read the pin through the evidence store, not SQLite."""
        result = self._capture(TEXT, type_pin="project", parent_goal_id=GOAL_ID)

        store = EvidenceStore(self.runtime_root)
        record = store.validate_directory(
            self.runtime_root / "evidence" / result.capture_id
        )
        self.assertEqual(record.type_pin, "project")
        self.assertEqual(record.parent_goal_id, GOAL_ID)

    def test_capture_service_rejects_a_project_pin_without_a_parent_goal(self) -> None:
        result = self._capture(TEXT, type_pin="project")

        self.assertIs(result.status, CaptureStatus.REFUSED)
        self.assertEqual(result.reason, "pin_incomplete")
        self.assertFalse((self.runtime_root / "evidence").exists())


class PlanningPinReplayTest(unittest.TestCase):
    """A pin is part of the human's intent, so replay compares it (fail closed)."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name)

    def _capture(self, text: str, **pin: object) -> object:
        with SQLiteStateStore(self.runtime_root / "state" / "metis.db") as store:
            store.initialize()
            service = CaptureService(store, EvidenceStore(self.runtime_root))
            return service.capture(text, **pin)

    def _drop_state(self) -> None:
        """Leave evidence in place with no intake row, to reach the second branch."""
        (self.runtime_root / "state" / "metis.db").unlink()

    def test_replay_with_the_same_pin_is_a_duplicate(self) -> None:
        first = self._capture(TEXT, type_pin="goal")
        second = self._capture(TEXT, type_pin="goal")

        self.assertIs(second.status, CaptureStatus.DUPLICATE)
        self.assertEqual(second.capture_id, first.capture_id)

    def test_replay_with_a_different_pin_is_refused(self) -> None:
        self._capture(TEXT, type_pin="goal")
        second = self._capture(TEXT, type_pin="project", parent_goal_id=GOAL_ID)

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")

    def test_replay_dropping_a_pin_is_refused(self) -> None:
        self._capture(TEXT, type_pin="goal")
        second = self._capture(TEXT)

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")

    def test_replay_adding_a_pin_is_refused(self) -> None:
        self._capture(TEXT)
        second = self._capture(TEXT, type_pin="goal")

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")

    def test_replay_with_a_different_parent_goal_is_refused(self) -> None:
        self._capture(TEXT, type_pin="project", parent_goal_id=GOAL_ID)
        second = self._capture(TEXT, type_pin="project", parent_goal_id="goal.other")

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")

    def test_orphaned_evidence_replay_with_the_same_pin_mints_no_second_capture(
        self,
    ) -> None:
        """Rebuilding state re-adopts the existing evidence rather than duplicating it."""
        first = self._capture(TEXT, type_pin="goal")
        self._drop_state()
        second = self._capture(TEXT, type_pin="goal")

        self.assertEqual(second.capture_id, first.capture_id)
        self.assertEqual(
            [path.name for path in (self.runtime_root / "evidence").iterdir()],
            [first.capture_id],
        )

    def test_orphaned_evidence_replay_with_a_different_pin_is_refused(self) -> None:
        """The evidence-only branch must fail closed exactly like the state branch."""
        self._capture(TEXT, type_pin="goal")
        self._drop_state()
        second = self._capture(TEXT, type_pin="project", parent_goal_id=GOAL_ID)

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")


if __name__ == "__main__":
    unittest.main()

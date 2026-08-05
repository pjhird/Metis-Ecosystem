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

from tests.data_access.inspection import force_intake_pin, intake_pin


GOAL_ID = "goal.health-baseline"
PROJECT_ID = "proj.weekly-7d4e8eb8"
OTHER_PROJECT_ID = "proj.reading-2b91c4de"
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

    def _usage_error(self, argv: list[str]) -> str:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(argv, runtime_root=self.runtime_root)
        self.assertEqual(raised.exception.code, 2)
        # `parser.error` exits before any service is built, so nothing is written.
        self.assertFalse((self.runtime_root / "evidence").exists())
        return stderr.getvalue()

    def _only_evidence_metadata(self) -> dict:
        directories = list((self.runtime_root / "evidence").iterdir())
        self.assertEqual(len(directories), 1)
        return json.loads(
            (directories[0] / "meta.json").read_text(encoding="utf-8")
        )

    def test_capture_as_project_requires_goal_flag(self) -> None:
        self._usage_error(["capture", "--as", "project", TEXT])

    def test_goal_flag_requires_as_project(self) -> None:
        self._usage_error(["capture", "--goal", GOAL_ID, TEXT])
        self._usage_error(["capture", "--as", "goal", "--goal", GOAL_ID, TEXT])

    def test_as_rejects_a_type_that_is_not_a_planning_pin(self) -> None:
        """`task` joins the choices in ADR-022; a classifier type still does not."""
        self._usage_error(["capture", "--as", "idea", TEXT])

    def test_goal_flag_rejects_an_unsafe_identifier(self) -> None:
        self._usage_error(["capture", "--as", "project", "--goal", "../escape", TEXT])

    def test_capture_as_goal_succeeds(self) -> None:
        return_code, stdout = self._run(["capture", "--as", "goal", TEXT])

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["status"], "captured")

    def test_capture_as_task_requires_project_flag_at_the_cli(self) -> None:
        stderr = self._usage_error(["capture", "--as", "task", TEXT])

        self.assertIn("--as task requires --project", stderr)

    def test_project_flag_without_task_pin_writes_no_evidence(self) -> None:
        stderr = self._usage_error(["capture", "--project", PROJECT_ID, TEXT])

        self.assertIn("--project is only valid with --as task", stderr)

    def test_project_flag_rejects_an_unsafe_identifier(self) -> None:
        self._usage_error(
            ["capture", "--as", "task", "--project", "../escape", TEXT]
        )

    def test_goal_flag_is_rejected_for_a_task(self) -> None:
        """The two parent flags are not interchangeable; each names its own pin."""
        self._usage_error(["capture", "--as", "task", "--goal", GOAL_ID, TEXT])

    def test_capture_as_task_records_the_pin_and_parent_project(self) -> None:
        return_code, stdout = self._run(
            ["capture", "--as", "task", "--project", PROJECT_ID, TEXT]
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(stdout)["status"], "captured")
        metadata = self._only_evidence_metadata()
        self.assertEqual(metadata["type_pin"], "task")
        self.assertEqual(metadata["parent_id"], PROJECT_ID)


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
        self.assertIsNone(metadata["parent_id"])

    def test_capture_as_goal_records_the_pin(self) -> None:
        result = self._capture(TEXT, type_pin="goal")

        metadata = self._metadata(result.capture_id)
        self.assertEqual(metadata["type_pin"], "goal")
        self.assertIsNone(metadata["parent_id"])

    def test_capture_as_project_records_the_pin_and_parent_goal(self) -> None:
        result = self._capture(TEXT, type_pin="project", parent_id=GOAL_ID)

        metadata = self._metadata(result.capture_id)
        self.assertEqual(metadata["type_pin"], "project")
        self.assertEqual(metadata["parent_id"], GOAL_ID)

    def test_pin_is_readable_from_the_validated_evidence_record(self) -> None:
        """Classify and file read the pin through the evidence store, not SQLite."""
        result = self._capture(TEXT, type_pin="project", parent_id=GOAL_ID)

        store = EvidenceStore(self.runtime_root)
        record = store.validate_directory(
            self.runtime_root / "evidence" / result.capture_id
        )
        self.assertEqual(record.type_pin, "project")
        self.assertEqual(record.parent_id, GOAL_ID)

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

    def test_goal_then_project_under_a_goal_creates_two_captures(self) -> None:
        """ADR-022 replaces the ADR-021 refusal here, and the exemption is structural.

        A conflict requires the same parent under a differing pin. A goal's
        parent is always `None` and a project's never is, so `goal` can never
        collide with `project` or `task` — no test could make it. That is a
        deliberate consequence of keying intake on
        `content_hash + type_pin + parent_id`, not an oversight: the only pairs
        that can still conflict are unpinned/goal and project/task under one
        parent, both of which are covered below.
        """
        first = self._capture(TEXT, type_pin="goal")
        second = self._capture(TEXT, type_pin="project", parent_id=GOAL_ID)

        self.assertIs(second.status, CaptureStatus.CAPTURED)
        self.assertNotEqual(second.capture_id, first.capture_id)

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

    def test_replay_under_a_different_parent_goal_creates_two_captures(self) -> None:
        """Same text, different parent, same pin: a different intent (ADR-022).

        ADR-021 refused this as `pin_conflict`; the composite uniqueness key
        makes it two captures.
        """
        first = self._capture(TEXT, type_pin="project", parent_id=GOAL_ID)
        second = self._capture(TEXT, type_pin="project", parent_id="goal.other")

        self.assertIs(second.status, CaptureStatus.CAPTURED)
        self.assertNotEqual(second.capture_id, first.capture_id)

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

    def test_orphaned_evidence_replay_with_a_conflicting_pin_is_refused(self) -> None:
        """The evidence-only branch must fail closed exactly like the state branch.

        Held on unpinned/goal, which share a `None` parent, because that is one
        of the two pairs that can still conflict at all under ADR-022.
        """
        self._capture(TEXT, type_pin="goal")
        self._drop_state()
        second = self._capture(TEXT)

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")


class PlanningTaskCaptureTest(unittest.TestCase):
    """ADR-022: the uniqueness key is `content_hash + type_pin + parent_id`."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name)
        self.database_path = self.runtime_root / "state" / "metis.db"

    def _capture(self, text: str, **pin: object) -> object:
        with SQLiteStateStore(self.database_path) as store:
            store.initialize()
            service = CaptureService(store, EvidenceStore(self.runtime_root))
            return service.capture(text, **pin)

    def _corrupt_intake_pin(self, capture_id: str, type_pin: str, parent_id: str) -> None:
        """Write divergent projection values, as a hand edit or half-migration would.

        Routed through tests/data_access/ so SQL stays inside the boundary the
        `sql_appears_only_in_data_layer` test enforces (ADR-002).
        """
        with SQLiteStateStore(self.database_path) as store:
            store.initialize()
            force_intake_pin(store, capture_id, type_pin=type_pin, parent_id=parent_id)

    def _intake_pin(self, capture_id: str) -> tuple[str, str]:
        with SQLiteStateStore(self.database_path) as store:
            store.initialize()
            return intake_pin(store, capture_id)

    def test_same_text_under_two_projects_creates_two_captures(self) -> None:
        """Capture half of the filed proof; Task 8 proves the vault half."""
        first = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)
        second = self._capture(TEXT, type_pin="task", parent_id=OTHER_PROJECT_ID)

        self.assertIs(second.status, CaptureStatus.CAPTURED)
        self.assertNotEqual(second.capture_id, first.capture_id)

    def test_replay_with_the_same_task_pin_is_a_duplicate(self) -> None:
        first = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)
        second = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)

        self.assertIs(second.status, CaptureStatus.DUPLICATE)
        self.assertEqual(second.capture_id, first.capture_id)

    def test_conflicting_pin_on_identical_text_is_refused(self) -> None:
        """Same parent, differing pin — one of the two pairs that can conflict."""
        self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)
        second = self._capture(TEXT, type_pin="project", parent_id=PROJECT_ID)

        self.assertIs(second.status, CaptureStatus.REFUSED)
        self.assertEqual(second.reason, "pin_conflict")

    def test_duplicate_plain_capture_still_creates_one_capture(self) -> None:
        """The capture half of the NULL-hazard regression.

        Sentinels rather than NULL in the key: SQLite compares NULLs as
        distinct, which would silently allow both rows. The filed half is the
        pre-existing end-to-end `duplicate_replay_creates_one_note`.
        """
        first = self._capture(TEXT)
        second = self._capture(TEXT)

        self.assertIs(second.status, CaptureStatus.DUPLICATE)
        self.assertEqual(second.capture_id, first.capture_id)

    def test_capture_as_task_requires_project_flag(self) -> None:
        result = self._capture(TEXT, type_pin="task")

        self.assertIs(result.status, CaptureStatus.REFUSED)
        self.assertEqual(result.reason, "pin_incomplete")
        self.assertFalse((self.runtime_root / "evidence").exists())

    def test_intake_pin_columns_match_evidence_meta(self) -> None:
        """The projection is derived; divergence is refused, not preferred away."""
        first = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)
        self._corrupt_intake_pin(first.capture_id, type_pin="goal", parent_id="")

        replay = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)

        self.assertIs(replay.status, CaptureStatus.FAILED)
        self.assertEqual(replay.reason, "state_evidence_mismatch")

    def test_a_legacy_unprojected_planning_row_fails_closed(self) -> None:
        """A pre-ADR-022 row carries the sentinel while its evidence records a pin.

        The migration reads *.sql only and cannot open evidence, so this state
        is reachable on any store predating ADR-022. It carries its own reason
        so an operator can tell a migration artifact from tampering.
        """
        first = self._capture(TEXT, type_pin="goal")
        self._corrupt_intake_pin(first.capture_id, type_pin="", parent_id="")

        replay = self._capture(TEXT, type_pin="goal")

        self.assertIs(replay.status, CaptureStatus.FAILED)
        self.assertEqual(replay.reason, "intake_pin_unprojected")
        self.assertEqual(replay.capture_id, first.capture_id)

    def test_a_half_projected_row_is_tampering_not_a_migration_artifact(self) -> None:
        """Both columns empty is the migration artifact; one is not.

        No migration or application path produces a half-projected row, so the
        likeliest cause is a repair UPDATE run halfway — investigate rather
        than re-run (ADR-022 clause 9).
        """
        first = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)
        self._corrupt_intake_pin(first.capture_id, type_pin="", parent_id=PROJECT_ID)

        replay = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)

        self.assertIs(replay.status, CaptureStatus.FAILED)
        self.assertEqual(replay.reason, "state_evidence_mismatch")

    def test_the_projection_records_the_pin_for_a_task(self) -> None:
        result = self._capture(TEXT, type_pin="task", parent_id=PROJECT_ID)

        self.assertEqual(self._intake_pin(result.capture_id), ("task", PROJECT_ID))

    def test_an_unpinned_capture_projects_the_sentinel(self) -> None:
        """`''`, never NULL — NULL in the key would disable replay protection."""
        result = self._capture(TEXT)

        self.assertEqual(self._intake_pin(result.capture_id), ("", ""))


if __name__ == "__main__":
    unittest.main()

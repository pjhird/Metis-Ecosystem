from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from metis.capture import CaptureResult, CaptureStatus
from metis.cli import main


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
EVIDENCE_PATH = f"evidence/{CAPTURE_ID}"
RESULT_KEYS = {"capture_id", "evidence_path", "message", "reason", "status"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_root = Path(self.temporary_directory.name)

    def _run_with_result(
        self, result: CaptureResult
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("metis.cli.CaptureService") as service_type:
            service_type.return_value.capture.return_value = result
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main(
                    ["capture", "typed input"],
                    runtime_root=self.runtime_root,
                )
        return return_code, stdout.getvalue(), stderr.getvalue()

    def test_capture_requires_exactly_one_text_argument(self) -> None:
        for argv in (["capture"], ["capture", "one", "two"]):
            with self.subTest(argv=argv):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(argv, runtime_root=self.runtime_root)
                self.assertEqual(raised.exception.code, 2)

    def test_successful_shell_outcomes_write_one_stable_json_object(self) -> None:
        cases = (
            CaptureResult(
                CaptureStatus.CAPTURED,
                CAPTURE_ID,
                EVIDENCE_PATH,
                None,
                None,
            ),
            CaptureResult(
                CaptureStatus.DUPLICATE,
                CAPTURE_ID,
                EVIDENCE_PATH,
                "exact_replay",
                None,
            ),
            CaptureResult(
                CaptureStatus.REFUSED,
                CAPTURE_ID,
                EVIDENCE_PATH,
                "evidence_collision",
                "evidence target already exists",
            ),
        )

        for result in cases:
            with self.subTest(status=result.status.value):
                return_code, stdout, stderr = self._run_with_result(result)
                payload = json.loads(stdout)

                self.assertEqual(return_code, 0)
                self.assertEqual(stderr, "")
                self.assertEqual(set(payload), RESULT_KEYS)
                self.assertEqual(payload["status"], result.status.value)
                self.assertEqual(payload["reason"], result.reason)
                self.assertEqual(
                    stdout,
                    json.dumps(payload, sort_keys=True) + "\n",
                )

    def test_failed_shell_outcome_writes_json_to_stderr_and_exits_one(self) -> None:
        result = CaptureResult(
            CaptureStatus.FAILED,
            CAPTURE_ID,
            EVIDENCE_PATH,
            "state_registration_failed",
            "registration unavailable",
        )

        return_code, stdout, stderr = self._run_with_result(result)
        payload = json.loads(stderr)

        self.assertEqual(return_code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(set(payload), RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "state_registration_failed")
        self.assertEqual(stderr, json.dumps(payload, sort_keys=True) + "\n")

    def test_state_initialization_failure_is_an_explicit_failed_result(self) -> None:
        blocked_root = self.runtime_root / "not-a-directory"
        blocked_root.write_text("blocks state directory creation", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = main(
                ["capture", "typed input"],
                runtime_root=blocked_root,
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(set(payload), RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "state_initialization_failed")
        self.assertIsNone(payload["capture_id"])
        self.assertIsNone(payload["evidence_path"])
        self.assertIsInstance(payload["message"], str)
        self.assertTrue(payload["message"])


class ModuleEntryTests(unittest.TestCase):
    def test_module_entry_preserves_utf8_input_and_reports_exact_replay(self) -> None:
        text = "  café\n  "
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT)

        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime_root = Path(temporary_directory)
            results = [
                subprocess.run(
                    [sys.executable, "-m", "metis", "capture", text],
                    cwd=runtime_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for _ in range(2)
            ]
            payloads = [json.loads(result.stdout) for result in results]
            evidence_directories = list((runtime_root / "evidence").iterdir())

            self.assertEqual([result.returncode for result in results], [0, 0])
            self.assertEqual([result.stderr for result in results], ["", ""])
            self.assertEqual(
                [payload["status"] for payload in payloads],
                ["captured", "duplicate"],
            )
            self.assertEqual([set(payload) for payload in payloads], [RESULT_KEYS] * 2)
            self.assertEqual(len(evidence_directories), 1)
            self.assertEqual(
                (evidence_directories[0] / "raw.txt").read_bytes(),
                text.encode("utf-8"),
            )


class PackagingTests(unittest.TestCase):
    def test_console_script_and_project_metadata_match_runtime_contract(self) -> None:
        with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)["project"]

        self.assertEqual(project["name"], "metis-ecosystem")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["scripts"]["metis"], "metis.cli:main")


if __name__ == "__main__":
    unittest.main()

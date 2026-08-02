from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from metis.capture import CaptureResult, CaptureStatus
from metis.classification import ClassificationResult, ClassificationStatus
from metis.cli import main
from metis.proposal import ProposalResult, ProposalStatus


CAPTURE_ID = "8f14e45f-ea3c-4f7a-9f2d-6c8b5a1d3e70"
EVIDENCE_PATH = f"evidence/{CAPTURE_ID}"
RESULT_KEYS = {"capture_id", "evidence_path", "message", "reason", "status"}
CLASSIFICATION_RESULT_KEYS = {
    "candidate_type",
    "capture_id",
    "classification_id",
    "confidence",
    "message",
    "raw_response_path",
    "reason",
    "routing",
    "sensitivity",
    "status",
}
PROPOSAL_RESULT_KEYS = {
    "capture_id",
    "classification_id",
    "confidence",
    "content_path",
    "draft_path",
    "intake_state",
    "message",
    "note_type",
    "proposal_id",
    "raw_response_path",
    "reason",
    "risk_level",
    "sensitivity",
    "status",
    "title",
}
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

    def _run_with_classification_result(
        self, result: ClassificationResult
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("metis.cli.ClassificationService", create=True) as service_type:
            service_type.return_value.classify.return_value = result
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main(
                    ["classify", CAPTURE_ID],
                    runtime_root=self.runtime_root,
                    model_adapter_factory=lambda: object(),
                )
        return return_code, stdout.getvalue(), stderr.getvalue()

    def _run_with_proposal_result(
        self, result: ProposalResult
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("metis.cli.ProposalService", create=True) as service_type:
            service_type.return_value.propose.return_value = result
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main(
                    ["propose", CAPTURE_ID],
                    runtime_root=self.runtime_root,
                    model_adapter_factory=lambda: object(),
                )
        return return_code, stdout.getvalue(), stderr.getvalue()

    def test_capture_requires_exactly_one_text_argument(self) -> None:
        for argv in (["capture"], ["capture", "one", "two"]):
            with self.subTest(argv=argv):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(argv, runtime_root=self.runtime_root)
                self.assertEqual(raised.exception.code, 2)

    def test_classify_requires_exactly_one_capture_id(self) -> None:
        for argv in (["classify"], ["classify", CAPTURE_ID, "extra"]):
            with self.subTest(argv=argv):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(
                            argv,
                            runtime_root=self.runtime_root,
                            model_adapter_factory=lambda: object(),
                        )
                self.assertEqual(raised.exception.code, 2)

    def test_propose_requires_exactly_one_capture_id(self) -> None:
        for argv in (["propose"], ["propose", CAPTURE_ID, "extra"]):
            with self.subTest(argv=argv):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(
                            argv,
                            runtime_root=self.runtime_root,
                            model_adapter_factory=lambda: object(),
                        )
                self.assertEqual(raised.exception.code, 2)

    def test_proposal_shell_outcomes_use_stable_json_streams_and_codes(self):
        base = dict(
            capture_id=CAPTURE_ID,
            classification_id="01K1D5Q5M00000000000000000",
            proposal_id="01K1D5Q5M00000000000000001",
            note_type="idea",
            title="Review workflow",
            confidence=0.82,
            sensitivity="normal",
            risk_level="low",
            raw_response_path=(
                "proposal-evidence/01K1D5Q5M00000000000000001/raw-response.txt"
            ),
            content_path="proposal-content/01K1D5Q5M00000000000000001/body.md",
            draft_path=f"vault/notes/proposed/note.{CAPTURE_ID}.md",
            intake_state="awaiting_approval",
            reason=None,
            message=None,
        )
        cases = (
            (ProposalResult(status=ProposalStatus.PROPOSED, **base), 0, "stdout"),
            (ProposalResult(status=ProposalStatus.DUPLICATE, **base), 0, "stdout"),
            (
                ProposalResult(
                    status=ProposalStatus.REFUSED,
                    **{
                        **base,
                        "intake_state": "proposing",
                        "reason": "proposal_in_progress",
                        "message": "proposal generation is already in progress",
                    },
                ),
                0,
                "stdout",
            ),
            (
                ProposalResult(
                    status=ProposalStatus.FAILED,
                    **{
                        **base,
                        "draft_path": None,
                        "intake_state": "failed",
                        "reason": "draft_write_failed",
                        "message": "proposal failed",
                    },
                ),
                1,
                "stderr",
            ),
        )
        for result, code, destination in cases:
            with self.subTest(status=result.status.value):
                return_code, stdout, stderr = self._run_with_proposal_result(result)
                rendered = stdout if destination == "stdout" else stderr
                other = stderr if destination == "stdout" else stdout
                payload = json.loads(rendered)

                self.assertEqual(return_code, code)
                self.assertEqual(other, "")
                self.assertEqual(set(payload), PROPOSAL_RESULT_KEYS)
                self.assertEqual(payload["status"], result.status.value)
                self.assertEqual(rendered, json.dumps(payload, sort_keys=True) + "\n")
                for key in ("raw_response_path", "content_path", "draft_path"):
                    if payload[key] is not None:
                        self.assertFalse(Path(payload[key]).is_absolute())
                        self.assertNotIn("\\", payload[key])

    def test_propose_initialization_failure_is_safe_and_shape_stable(self):
        blocked_root = self.runtime_root / "sensitive-host-path"
        blocked_root.write_text("blocks state directory creation", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = main(
                ["propose", CAPTURE_ID],
                runtime_root=blocked_root,
                model_adapter_factory=lambda: object(),
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(set(payload), PROPOSAL_RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["capture_id"], CAPTURE_ID)
        self.assertEqual(payload["reason"], "state_initialization_failed")
        self.assertEqual(payload["message"], "state initialization failed")
        self.assertNotIn(str(blocked_root), stderr.getvalue())
        self.assertNotIn("blocks state", stderr.getvalue())

    def test_cli_exposes_no_step_five_or_permanent_write_surface(self):
        forbidden = (
            ["approve", CAPTURE_ID],
            ["reject", CAPTURE_ID],
            ["file", CAPTURE_ID],
            ["link", CAPTURE_ID],
            ["propose", CAPTURE_ID, "--approve"],
            ["propose", CAPTURE_ID, "--status", "approved"],
        )
        for argv in forbidden:
            with self.subTest(argv=argv):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(
                            argv,
                            runtime_root=self.runtime_root,
                            model_adapter_factory=lambda: object(),
                        )
                self.assertEqual(raised.exception.code, 2)

    def test_classification_shell_outcomes_write_one_stable_json_object(
        self,
    ) -> None:
        cases = (
            (
                ClassificationResult(
                    ClassificationStatus.CLASSIFIED,
                    CAPTURE_ID,
                    "01K1D5Q5M00000000000000000",
                    "idea",
                    "normal",
                    0.82,
                    "proposal:idea",
                    (
                        "classification-evidence/"
                        "01K1D5Q5M00000000000000000/raw-response.txt"
                    ),
                    None,
                    None,
                ),
                0,
                "stdout",
            ),
            (
                ClassificationResult(
                    ClassificationStatus.DUPLICATE,
                    CAPTURE_ID,
                    "01K1D5Q5M00000000000000000",
                    "idea",
                    "normal",
                    0.82,
                    "proposal:idea",
                    (
                        "classification-evidence/"
                        "01K1D5Q5M00000000000000000/raw-response.txt"
                    ),
                    "already_classified",
                    "capture is already classified",
                ),
                0,
                "stdout",
            ),
            (
                ClassificationResult(
                    ClassificationStatus.REFUSED,
                    CAPTURE_ID,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "capture_not_found",
                    "capture was not found",
                ),
                0,
                "stdout",
            ),
            (
                ClassificationResult(
                    ClassificationStatus.FAILED,
                    CAPTURE_ID,
                    "01K1D5Q5M00000000000000000",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "model_request_failed",
                    "classification failed",
                ),
                1,
                "stderr",
            ),
        )

        for result, expected_code, destination in cases:
            with self.subTest(status=result.status.value):
                return_code, stdout, stderr = self._run_with_classification_result(
                    result
                )
                rendered = stdout if destination == "stdout" else stderr
                other = stderr if destination == "stdout" else stdout
                payload = json.loads(rendered)

                self.assertEqual(return_code, expected_code)
                self.assertEqual(other, "")
                self.assertEqual(set(payload), CLASSIFICATION_RESULT_KEYS)
                self.assertEqual(payload["status"], result.status.value)
                self.assertEqual(rendered, json.dumps(payload, sort_keys=True) + "\n")

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

    def test_classify_state_initialization_failure_preserves_stable_shape(self) -> None:
        blocked_root = self.runtime_root / "not-a-directory-classify"
        blocked_root.write_text("blocks state directory creation", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = main(
                ["classify", CAPTURE_ID],
                runtime_root=blocked_root,
                model_adapter_factory=lambda: object(),
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(set(payload), CLASSIFICATION_RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["capture_id"], CAPTURE_ID)
        self.assertEqual(payload["reason"], "state_initialization_failed")

    def test_partial_evidence_collision_is_failed_json_with_nonzero_exit(
        self,
    ) -> None:
        evidence_root = self.runtime_root / "evidence"
        original_mkdir = Path.mkdir

        def mkdir_then_create_raw(path: Path, *args: object, **kwargs: object) -> None:
            original_mkdir(path, *args, **kwargs)
            if path.parent == evidence_root:
                (path / "raw.txt").write_bytes(b"raced raw evidence")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(Path, "mkdir", new=mkdir_then_create_raw):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main(
                    ["capture", "new content"],
                    runtime_root=self.runtime_root,
                )

        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        evidence_directories = list(evidence_root.iterdir())
        self.assertEqual(set(payload), RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "evidence_write_failed")
        self.assertIsNotNone(payload["capture_id"])
        self.assertEqual(len(evidence_directories), 1)
        self.assertEqual(
            (evidence_directories[0] / "raw.txt").read_bytes(),
            b"raced raw evidence",
        )
        self.assertFalse((evidence_directories[0] / "meta.json").exists())

    def test_unencodable_text_is_failed_json_without_evidence_mutation(
        self,
    ) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main(
                    ["capture", "invalid surrogate: \ud800"],
                    runtime_root=self.runtime_root,
                )
        except UnicodeError as error:
            self.fail(f"CLI raised instead of rendering a failed result: {error!r}")

        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(set(payload), RESULT_KEYS)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "utf8_encoding_failed")
        self.assertIsNone(payload["capture_id"])
        self.assertIsNone(payload["evidence_path"])
        self.assertIsInstance(payload["message"], str)
        self.assertTrue(payload["message"])
        self.assertFalse((self.runtime_root / "evidence").exists())


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
            configuration = tomllib.load(stream)
        project = configuration["project"]

        self.assertEqual(project["name"], "metis-ecosystem")
        self.assertEqual(project["dependencies"], ["anthropic>=0.104,<1"])
        self.assertEqual(project["scripts"]["metis"], "metis.cli:main")
        self.assertEqual(
            configuration["tool"]["setuptools"]["package-data"],
            {
                "metis.data_access": ["migrations/*.sql"],
                "metis.prompts": ["*.txt"],
            },
        )

    def test_built_wheel_installs_migration_and_working_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            source_root = temporary_root / "source"
            wheel_directory = temporary_root / "wheel"
            virtual_environment = temporary_root / "venv"
            runtime_root = temporary_root / "runtime"
            source_root.mkdir()
            wheel_directory.mkdir()
            runtime_root.mkdir()
            shutil.copy2(REPOSITORY_ROOT / "pyproject.toml", source_root)
            shutil.copytree(
                REPOSITORY_ROOT / "metis",
                source_root / "metis",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            environment = os.environ.copy()
            environment["PIP_NO_INDEX"] = "1"
            environment.pop("PYTHONPATH", None)

            build = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheel_directory),
                    str(source_root),
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            wheels = list(wheel_directory.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            wheel = wheels[0]
            with zipfile.ZipFile(wheel) as archive:
                self.assertIn(
                    "metis/data_access/migrations/001_initial.sql",
                    archive.namelist(),
                )
                self.assertIn(
                    "metis/data_access/migrations/002_unique_classification_capture.sql",
                    archive.namelist(),
                )
                self.assertIn(
                    "metis/prompts/classify-v1.txt",
                    archive.namelist(),
                )

            create_environment = subprocess.run(
                [sys.executable, "-m", "venv", str(virtual_environment)],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                create_environment.returncode,
                0,
                create_environment.stderr,
            )
            installed_python = virtual_environment / "bin" / "python"
            install = subprocess.run(
                [
                    str(installed_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-index",
                    str(wheel),
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            result = subprocess.run(
                [
                    str(virtual_environment / "bin" / "metis"),
                    "capture",
                    "wheel smoke test",
                ],
                cwd=runtime_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload), RESULT_KEYS)
            self.assertEqual(payload["status"], "captured")
            self.assertTrue(
                (
                    virtual_environment
                    / "lib"
                    / f"python{sys.version_info.major}.{sys.version_info.minor}"
                    / "site-packages"
                    / "metis"
                    / "data_access"
                    / "migrations"
                    / "001_initial.sql"
                ).is_file()
            )

    def test_installed_classify_records_missing_configuration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            source_root = temporary_root / "source"
            wheel_directory = temporary_root / "wheel"
            virtual_environment = temporary_root / "venv"
            runtime_root = temporary_root / "runtime"
            source_root.mkdir()
            wheel_directory.mkdir()
            runtime_root.mkdir()
            shutil.copy2(REPOSITORY_ROOT / "pyproject.toml", source_root)
            shutil.copytree(
                REPOSITORY_ROOT / "metis",
                source_root / "metis",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            environment = os.environ.copy()
            environment["PIP_NO_INDEX"] = "1"
            environment.pop("PYTHONPATH", None)
            environment.pop("ANTHROPIC_API_KEY", None)
            environment.pop("METIS_CLASSIFICATION_MODEL", None)

            build = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheel_directory),
                    str(source_root),
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            wheel = next(wheel_directory.glob("*.whl"))

            create_environment = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "venv",
                    "--system-site-packages",
                    str(virtual_environment),
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                create_environment.returncode,
                0,
                create_environment.stderr,
            )
            installed_python = virtual_environment / "bin" / "python"
            install = subprocess.run(
                [
                    str(installed_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-index",
                    str(wheel),
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            executable = virtual_environment / "bin" / "metis"
            capture = subprocess.run(
                [str(executable), "capture", "wheel classification smoke test"],
                cwd=runtime_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(capture.returncode, 0, capture.stderr)
            capture_payload = json.loads(capture.stdout)
            capture_id = capture_payload["capture_id"]
            capture_directory = runtime_root / "evidence" / capture_id
            before = {
                path.name: path.read_bytes() for path in capture_directory.iterdir()
            }

            classification = subprocess.run(
                [str(executable), "classify", capture_id],
                cwd=runtime_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(classification.returncode, 1)
            self.assertEqual(classification.stdout, "")
            payload = json.loads(classification.stderr)
            self.assertEqual(set(payload), CLASSIFICATION_RESULT_KEYS)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "model_configuration_failed")
            self.assertEqual(
                {path.name: path.read_bytes() for path in capture_directory.iterdir()},
                before,
            )
            self.assertFalse((runtime_root / "classification-evidence").exists())


if __name__ == "__main__":
    unittest.main()

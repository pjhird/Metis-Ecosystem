from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RepositorySkeletonTests(unittest.TestCase):
    def test_runtime_artifacts_are_ignored(self) -> None:
        paths = (
            ".env",
            ".venv/pyvenv.cfg",
            "state/metis.db",
            "evidence/capture/raw.txt",
            "classification-evidence/classification/raw-response.txt",
            "proposal-evidence/proposal/raw-response.txt",
            "proposal-content/proposal/body.md",
            "vault/notes/proposed/note.md",
            "vault/notes/filed/note.md",
            "metis/__pycache__/module.cpython-39.pyc",
            ".coverage",
        )

        for path in paths:
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--quiet", path],
                    cwd=REPOSITORY_ROOT,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_governance_entrypoints_exist(self) -> None:
        self.assertTrue((REPOSITORY_ROOT / "AGENTS.md").is_file())
        self.assertTrue((REPOSITORY_ROOT / "CLAUDE.md").is_file())
        self.assertTrue((REPOSITORY_ROOT / ".github" / "CODEOWNERS").is_file())
        claude_lines = [
            line.strip()
            for line in (REPOSITORY_ROOT / "CLAUDE.md").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(claude_lines[0], "@AGENTS.md")
        self.assertLessEqual(
            len((REPOSITORY_ROOT / "AGENTS.md").read_text().splitlines()),
            200,
        )

    def test_pull_request_ci_workflow_contract(self) -> None:
        workflow_path = (
            REPOSITORY_ROOT / ".github" / "workflows" / "metis-tests.yml"
        )
        self.assertTrue(workflow_path.is_file(), "pull-request CI workflow is missing")

        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

        self.assertEqual(
            workflow,
            {
                "name": "Metis tests",
                "on": {"pull_request": {"branches": ["main"]}},
                "permissions": {"contents": "read"},
                "jobs": {
                    "tests": {
                        "name": "metis/tests",
                        "runs-on": "ubuntu-latest",
                        "steps": [
                            {
                                "name": "Check out repository",
                                "uses": "actions/checkout@v4",
                            },
                            {
                                "name": "Set up Python",
                                "uses": "actions/setup-python@v5",
                                "with": {"python-version": "3.13"},
                            },
                            {
                                "name": "Install build backend",
                                "run": "python -m pip install setuptools",
                            },
                            {
                                "name": "Install project and runtime dependencies",
                                "run": "python -m pip install --no-build-isolation -e .",
                            },
                            {
                                "name": "Run test suite",
                                "run": "python -m unittest discover -s tests -v",
                            },
                        ],
                    }
                },
            },
        )

    def test_step_seven_governed_documentation_is_current(self) -> None:
        agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        schemas = (REPOSITORY_ROOT / "METIS-SCHEMAS.md").read_text(encoding="utf-8")
        ledger = (REPOSITORY_ROOT / "METIS-REQUIREMENT-LEDGER.md").read_text(
            encoding="utf-8"
        )

        self.assertIn('metis propose "<capture-id>"', agents)
        self.assertNotIn("metis approvals            # not yet implemented", agents)
        self.assertIn("migration `003_proposal_reservation.sql`", schemas)
        self.assertIn("### 2.4 `proposal_reservation`", schemas)
        self.assertIn("`004_unique_approval_proposal.sql`", schemas)
        self.assertIn("metis file <capture-id>", agents)
        self.assertIn("`005_audit_event_append_only.sql`", schemas)
        self.assertIn("build-order step 7", ledger)
        for requirement, expected in (
            ("REQ-GOV-003", "| Verified |"),
            ("REQ-GOV-004", "| Verified |"),
            ("REQ-VLT-003", "| Verified |"),
            ("REQ-GOV-001", "| Verified |"),
            ("REQ-INTK-002", "| Verified |"),
            ("REQ-INTK-004", "| Verified |"),
            ("REQ-VLT-004", "| Verified |"),
            ("REQ-ORCH-004", "| Verified |"),
            ("REQ-ORCH-001", "| Verified |"),
            ("REQ-GOV-002", "| Verified |"),
            ("REQ-INTK-005", "| Verified |"),
            ("REQ-TEST-003", "| Verified |"),
        ):
            row = next(
                line
                for line in ledger.splitlines()
                if line.startswith(f"| {requirement} ")
            )
            with self.subTest(requirement=requirement):
                self.assertIn(expected, row)


if __name__ == "__main__":
    unittest.main()

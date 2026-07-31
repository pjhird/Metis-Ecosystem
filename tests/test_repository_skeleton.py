from __future__ import annotations

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
            "vault/notes/filed/note.md",
            "metis/__pycache__/module.cpython-39.pyc",
            ".coverage",
        )

        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--stdin"],
            cwd=REPOSITORY_ROOT,
            input="\n".join(paths),
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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "metis"
TEST_ROOT = REPOSITORY_ROOT / "tests"


class DataAccessBoundaryTests(unittest.TestCase):
    def test_sql_appears_only_in_data_layer(self) -> None:
        violations: list[str] = []

        for root in (PACKAGE_ROOT, TEST_ROOT):
            for path in root.rglob("*.sql"):
                if "data_access" not in path.parts:
                    violations.append(str(path.relative_to(REPOSITORY_ROOT)))

        for root in (PACKAGE_ROOT, TEST_ROOT):
            for path in root.rglob("*.py"):
                if "data_access" in path.parts:
                    continue
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        modules = (
                            [alias.name for alias in node.names]
                            if isinstance(node, ast.Import)
                            else [node.module or ""]
                        )
                        if any(module == "sqlite3" for module in modules):
                            violations.append(
                                f"{path.relative_to(REPOSITORY_ROOT)} imports sqlite3"
                            )
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"execute", "executemany", "executescript"}
                    ):
                        violations.append(
                            f"{path.relative_to(REPOSITORY_ROOT)} calls {node.func.attr}"
                        )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

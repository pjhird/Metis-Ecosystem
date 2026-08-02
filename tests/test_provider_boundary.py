from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PROVIDER_MODULE = Path("metis/model_adapters/claude.py")


class ProviderBoundaryTests(unittest.TestCase):
    def test_provider_sdk_imported_only_by_adapter(self) -> None:
        violations: list[str] = []
        for path in sorted((REPOSITORY_ROOT / "metis").rglob("*.py")):
            relative_path = path.relative_to(REPOSITORY_ROOT)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported_modules: list[str] = []
                if isinstance(node, ast.Import):
                    imported_modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported_modules = [node.module]
                if any(
                    module == "anthropic" or module.startswith("anthropic.")
                    for module in imported_modules
                ) and relative_path != ALLOWED_PROVIDER_MODULE:
                    violations.append(str(relative_path))

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

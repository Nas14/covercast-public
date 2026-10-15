"""The dependency between the two apps runs one way: coverage -> workforce.

If workforce imported coverage, neither app could be understood or tested
on its own (see docs/02-architecture.md and errors log E-04).
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

WORKFORCE = Path(__file__).resolve().parents[1]


class DependencyDirectionTests(SimpleTestCase):
    def test_workforce_never_imports_coverage(self):
        offenders = []
        for path in WORKFORCE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(n == "coverage" or n.startswith("coverage.") for n in names):
                    offenders.append(f"{path.relative_to(WORKFORCE.parent)}:{node.lineno}")
        self.assertEqual(offenders, [], "workforce must not import coverage")

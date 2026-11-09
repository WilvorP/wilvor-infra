"""Import isolation for the MA7 offline safety matrix.

The subprocess imports the MA7 fixture and matrix modules in a fresh
interpreter. LangGraph belongs to the existing MA3 orchestration module.
The synthetic-evidence test imports that module inside one test function,
so importing this matrix does not load it.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "ma7_master_safety_cases.py"
MATRIX = REPO_ROOT / "tests" / "contracts" / "test_ma7_master_safety_matrix.py"

FORBIDDEN_MODULES = {
    "aiohttp",
    "anthropic",
    "boto3",
    "botocore",
    "httpx",
    "langgraph",
    "openai",
    "requests",
    "urllib3",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_preflight",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_response_policy",
    "wilvor_ai.master_response_renderer",
    "wilvor_ai.master_specialist_adapters",
    "wilvor_ai.providers.anthropic_messages",
    "wilvor_ai.providers.bedrock_converse",
    "wilvor_operational",
}


def _imported_modules(path: Path, *, nested: bool) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = ast.walk(tree) if nested else tree.body
    imported: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_ma7_source_does_not_import_runtimes_or_unmerged_modules():
    fixture_imports = _imported_modules(FIXTURE, nested=True)
    matrix_imports = _imported_modules(MATRIX, nested=False)
    assert FORBIDDEN_MODULES.isdisjoint(fixture_imports)
    assert FORBIDDEN_MODULES.isdisjoint(matrix_imports)
    fixture_source = FIXTURE.read_text(encoding="utf-8")
    assert "master_orchestration" not in fixture_source
    assert "boto3" not in fixture_source
    assert "langgraph" not in fixture_source


def test_importing_ma7_fixtures_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(REPO_ROOT) + os.pathsep + str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import tests.fixtures.ma7_master_safety_cases as cases
import tests.contracts.test_ma7_master_safety_matrix as matrix
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert len(cases.SAFETY_CASES) == len(cases.REQUIRED_SCENARIO_IDS)
assert matrix.REQUIRED_SCENARIO_IDS == cases.REQUIRED_SCENARIO_IDS
assert 'urllib.request' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

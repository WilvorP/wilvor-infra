"""Import isolation for Phase 3A.1 schema and projection modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
SCHEMA = SHARED_DIR / "wilvor_ai" / "tool_schema.py"
PROJECTION = SHARED_DIR / "wilvor_ai" / "tool_result_projection.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "inspect",
    "wilvor_operational",
    "wilvor_historical_query",
    "wilvor_ai.live_ops",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_analytics_mapping",
    "wilvor_ai.specialist_runtime",
    "wilvor_ai.historical_specialist",
}


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_import_schema_and_projection_stay_offline():
    script = """
import sys
import wilvor_ai.tool_schema as tool_schema
import wilvor_ai.tool_result_projection as tool_result_projection
assert hasattr(tool_schema, 'build_tool_schemas')
assert hasattr(tool_result_projection, 'project_tool_result')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_historical_query' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert 'wilvor_ai.historical_specialist' not in sys.modules
assert 'AthenaExecutor' not in dir(tool_schema)
assert 'CoverageStore' not in dir(tool_result_projection)
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_schema_and_projection_sources_exclude_runtime_and_sdks():
    for path in (SCHEMA, PROJECTION):
        imported = _imported_modules(path)
        source = path.read_text(encoding="utf-8")
        assert imported.isdisjoint(_FORBIDDEN)
        assert "AthenaExecutor" not in source
        assert "CoverageStore" not in source
        assert "datetime.now" not in source
        assert "inspect.signature" not in source

"""DT5 catalog and projection stay clear of processors, providers, and AWS clients."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULES = (
    SHARED_DIR / "wilvor_ai" / "decision_tools.py",
    SHARED_DIR / "wilvor_ai" / "decision_tool_projection.py",
)
_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "wilvor_ai.providers",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_specialist",
    "wilvor_historical_query",
}
_PROCESSOR_MARKERS = (
    "functions.risk",
    "functions.recommendations",
    "functions.airport_assessment",
    "functions.active_alerts",
    "risk.processor",
    "recommendations.processor",
    "airport_assessment.processor",
    "active_alerts.processor",
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


def test_decision_tool_sources_do_not_import_processors_or_providers():
    for path in MODULES:
        imported = _imported_modules(path)
        source = path.read_text(encoding="utf-8")
        assert imported.isdisjoint(_FORBIDDEN)
        assert "boto3.client" not in source
        assert "boto3.resource" not in source
        assert "put_item" not in source
        assert "datetime.now" not in source
        assert "time.time" not in source
        for marker in _PROCESSOR_MARKERS:
            assert marker not in source
            assert all(marker not in name for name in imported)


def test_loading_decision_tools_does_not_import_processors_or_providers():
    script = """
import sys
before = set(sys.modules)
import wilvor_ai.decision_tools as decision_tools
import wilvor_ai.decision_tool_projection as decision_tool_projection
loaded = set(sys.modules) - before
markers = (
    "risk.processor",
    "recommendations.processor",
    "airport_assessment.processor",
    "active_alerts.processor",
    "wilvor_ai.providers",
    "wilvor_ai.historical_specialist",
    "wilvor_historical_query",
    "anthropic",
)
assert not any(any(marker in name for marker in markers) for name in loaded)
assert hasattr(decision_tools, "DECISION_TOOLS")
assert hasattr(decision_tool_projection, "project_decision_tool_result")
"""
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

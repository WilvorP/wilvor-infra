"""DT2 decision context does not import processors, AWS clients, or model SDKs."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "decision_context.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_ai.live_ops",
    "wilvor_ai.providers",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_specialist",
}

_PROCESSOR_MARKERS = (
    "functions.risk",
    "functions.recommendations",
    "functions.airport_assessment",
    "functions.active_alerts",
    "functions.encounter",
    "functions.projection",
    "risk.processor",
    "recommendations.processor",
    "airport_assessment.processor",
    "active_alerts.processor",
    "encounter.processor",
    "projection.processor",
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


def test_decision_context_source_does_not_import_processors_or_sdks():
    imported = _imported_modules(MODULE)
    source = MODULE.read_text(encoding="utf-8")

    assert imported.isdisjoint(_FORBIDDEN)
    assert "boto3" not in source
    assert "DECISION_TOOLS" not in source
    assert "datetime.now" not in source
    assert "time.time" not in source
    for marker in _PROCESSOR_MARKERS:
        assert marker not in source
        assert all(marker not in name for name in imported)


def test_loading_decision_context_does_not_import_processors():
    script = """
import sys
before = set(sys.modules)
import wilvor_ai.decision_context as decision_context
loaded = set(sys.modules) - before
markers = (
    "risk.processor",
    "recommendations.processor",
    "airport_assessment",
    "active_alerts",
    "encounter.processor",
    "projection.processor",
)
assert not any(any(marker in name for marker in markers) for name in loaded)
assert hasattr(decision_context, "get_current_decision_context")
assert not hasattr(decision_context, "DECISION_TOOLS")
assert "wilvor_ai.live_ops" not in loaded
assert "anthropic" not in loaded
assert "langgraph" not in loaded
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

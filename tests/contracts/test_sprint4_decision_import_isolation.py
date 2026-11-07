"""Sprint 4 Decision modules stay clear of AWS clients, processors, and models."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULES = (
    SHARED_DIR / "wilvor_ai" / "sprint4_decision_live.py",
    SHARED_DIR / "wilvor_ai" / "sprint4_decision_reconciliation.py",
    SHARED_DIR / "wilvor_ai" / "sprint4_decision_weather_context.py",
)

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_ai.providers",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_specialist",
    "wilvor_historical_query",
    "wilvor_operational.query",
    "wilvor_operational.discovery",
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

_FORBIDDEN_SOURCE = (
    "boto3.client",
    "boto3.resource",
    ".put_item(",
    ".delete_item(",
    ".update_item(",
    "datetime.now",
    "time.time",
    "os.environ",
    "getenv(",
    "terraform",
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


def test_sprint4_decision_sources_do_not_import_processors_or_aws_sdks():
    for path in MODULES:
        imported = _imported_modules(path)
        source = path.read_text(encoding="utf-8")
        assert imported.isdisjoint(_FORBIDDEN)
        for marker in _FORBIDDEN_SOURCE:
            assert marker not in source
        for marker in _PROCESSOR_MARKERS:
            assert marker not in source
            assert all(marker not in name for name in imported)
        assert "wilvor_ai.contracts" in imported or path.name != "sprint4_decision_live.py"


def test_sprint4_decision_modules_do_not_edit_shared_or_operational_files():
    owned = {path.name for path in MODULES}
    assert owned == {
        "sprint4_decision_live.py",
        "sprint4_decision_reconciliation.py",
        "sprint4_decision_weather_context.py",
    }
    for path in MODULES:
        source = path.read_text(encoding="utf-8")
        assert "wilvor_ai.contracts import" in source or "from wilvor_ai.contracts" in source
        assert "issue_production_application_principal" not in source
        assert "construct_decision_native_request" not in source
        assert "enable_ai_query_route" not in source


def test_loading_sprint4_decision_live_does_not_import_processors_or_model_sdks():
    script = """
import sys
before = set(sys.modules)
import wilvor_ai.sprint4_decision_live as live
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
    "langgraph",
    "openai",
)
assert not any(any(marker in name for marker in markers) for name in loaded)
assert live.PRODUCTION_DECISION_LIVE_AWS_ENABLED is False
assert live.ABSENT_RISK_IS_LOW_RISK is False
assert live.ABSENT_RECOMMENDATION_IS_MONITOR is False
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

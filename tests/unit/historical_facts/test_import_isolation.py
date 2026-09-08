"""Import isolation for wilvor_historical."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PACKAGE_DIR = SHARED_DIR / "wilvor_historical"


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


def test_package_source_has_no_aws_ai_or_currentness_imports():
    forbidden = (
        "boto3",
        "botocore",
        "wilvor_ai",
        "wilvor_operational",
        "shapely",
        "langgraph",
        "openai",
        "anthropic",
    )
    for path in PACKAGE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        assert not imported.intersection(forbidden), path


def test_import_wilvor_historical_is_isolated():
    script = """
import sys
import wilvor_historical
assert 'boto3' not in sys.modules
assert 'wilvor_ai' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_operational.current_set' not in sys.modules
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
)
assert hasattr(wilvor_historical, 'fact_from_event')
assert hasattr(wilvor_historical, 'build_hazard_geometry_fact')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout

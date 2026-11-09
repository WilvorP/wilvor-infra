"""LO2 claim and verifier modules stay offline and specialist-local."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
CLAIMS = SHARED_DIR / "wilvor_ai" / "live_ops_claims.py"
VERIFIER = SHARED_DIR / "wilvor_ai" / "live_ops_evidence_verifier.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


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


def test_claims_do_not_import_verifier_tools_or_operational_code():
    forbidden = {
        "wilvor_ai.live_ops",
        "wilvor_ai.live_ops_adapter",
        "wilvor_ai.live_ops_mapping",
        "wilvor_ai.live_ops_evidence_verifier",
        "wilvor_ai.live_ops_model_contracts",
        "wilvor_ai.decision_evidence_verifier",
        "wilvor_ai.historical_evidence_verifier",
        "wilvor_operational",
        "shapely",
        "boto3",
        "anthropic",
    }
    overlap = forbidden.intersection(_imported_modules(CLAIMS))
    assert overlap == set(), overlap


def test_verifier_does_not_import_tools_mapping_or_other_verifiers():
    forbidden = {
        "wilvor_ai.live_ops",
        "wilvor_ai.live_ops_adapter",
        "wilvor_ai.live_ops_mapping",
        "wilvor_ai.decision_evidence_verifier",
        "wilvor_ai.historical_evidence_verifier",
        "wilvor_operational",
        "shapely",
        "boto3",
        "anthropic",
    }
    overlap = forbidden.intersection(_imported_modules(VERIFIER))
    assert overlap == set(), overlap
    source = VERIFIER.read_text(encoding="utf-8")
    assert "time.time" not in source
    assert "os.environ" not in source


def test_importing_claims_and_verifier_stays_offline():
    completed = _run_isolated(
        """
import sys
import wilvor_ai.live_ops_claims
import wilvor_ai.live_ops_evidence_verifier
absent = [
    'wilvor_ai.live_ops',
    'wilvor_ai.live_ops_adapter',
    'wilvor_ai.live_ops_mapping',
    'wilvor_ai.decision_evidence_verifier',
    'wilvor_ai.historical_evidence_verifier',
    'wilvor_ai.historical_specialist',
    'wilvor_ai.decision_specialist',
    'wilvor_operational',
    'shapely',
    'boto3',
    'anthropic',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
assert 'wilvor_ai.live_ops_claims' in sys.modules
assert 'wilvor_ai.live_ops_evidence_verifier' in sys.modules
"""
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

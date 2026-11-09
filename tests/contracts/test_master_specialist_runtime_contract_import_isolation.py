"""The specialist runtime contract does not import runtimes, providers, or AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_specialist_runtime_contract.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_answer_renderer",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.live_ops_specialist",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.providers",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = (
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "LiveOpsSpecialist(",
    "HistoricalAnalyticsSpecialist(",
    "DecisionSpecialist(",
    "DecisionToolsRuntime(",
    "LiveOpsTrustedContext(",
    "finalize_historical_specialist_run(",
    ".run(",
    ".dispatch(",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_specialist_runtime_contract_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source


def test_importing_specialist_runtime_contract_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["ENVIRONMENT"] = "test"
    env["WILVOR_TEST_MODE"] = "1"
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_specialist_runtime_contract as contract
from wilvor_ai.contracts import ContractValidationError
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert contract.PRODUCTION_HOST is None
assert contract.PRODUCTION_AUTHORITY_AVAILABLE is False
assert contract.PRINCIPAL_ISSUANCE_AVAILABLE is False
assert contract.GRANT_ISSUANCE_AVAILABLE is False
assert contract.EVIDENCE_ADMISSION_AVAILABLE is False
assert contract.SPECIALIST_DISPATCH_AVAILABLE is False
assert contract.NATIVE_TYPES_CHANGED_THIS_SPRINT is False
assert not hasattr(contract, "LiveOpsSpecialist")
assert not hasattr(contract, "HistoricalAnalyticsSpecialist")
assert not hasattr(contract, "DecisionSpecialist")
assert not hasattr(contract, "DecisionToolsRuntime")
try:
    contract.invoke_specialist()
except ContractValidationError as exc:
    assert "specialist_invocation_not_granted" in str(exc)
else:
    raise SystemExit("dispatch returned")
try:
    contract.issue_principal(None)
except ContractValidationError as exc:
    assert "principal_not_issued" in str(exc)
else:
    raise SystemExit("principal issued")
try:
    contract.issue_grant(None)
except ContractValidationError as exc:
    assert "grant_not_issued" in str(exc)
else:
    raise SystemExit("grant issued")
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

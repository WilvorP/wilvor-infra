"""Sprint 4 security modules stay free of live AWS, models, and specialist runtimes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wilvor_ai.sprint4_security_authorization import SHARED_INTERFACE_REQUIREMENTS
from wilvor_ai.sprint4_security_aws_plan import SPRINT4_AWS_PLAN_EXECUTION_ENABLED
from wilvor_ai.sprint4_security_lambda_profile import SPRINT4_CLIENT_NETWORK_INIT_ENABLED
from wilvor_ai.sprint4_security_ledger import SPRINT4_LEDGER_WRITES_ENABLED, SPRINT4_LIVE_AWS_ENABLED


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED = REPO_ROOT / "functions" / "shared" / "wilvor_ai"

MODULES = (
    SHARED / "sprint4_security_ledger.py",
    SHARED / "sprint4_security_authorization.py",
    SHARED / "sprint4_security_aws_plan.py",
    SHARED / "sprint4_security_lambda_profile.py",
)

FORBIDDEN = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "requests",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops_specialist",
    "wilvor_ai.contracts",
    "wilvor_ai.specialist_contracts",
    "wilvor_ai.sprint3d_integration_host",
    "wilvor_ai.sprint3d_native_specialists",
    "wilvor_ai.sprint3d_source_evidence",
    "wilvor_operational",
}

# contracts.py is imported by many sealed modules. The Sprint 4 files may
# import ContractValidationError from it. Ban writing new shared contracts,
# not the existing error type.
ALLOWED_CONTRACTS_EXCEPTION = "wilvor_ai.contracts"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_sprint4_security_modules_do_not_import_live_or_specialist_runtimes():
    banned = FORBIDDEN - {ALLOWED_CONTRACTS_EXCEPTION}
    for path in MODULES:
        imported = _imported_modules(path)
        assert banned.isdisjoint(imported), (path.name, imported & banned)
        source = path.read_text(encoding="utf-8")
        assert "boto3.client" not in source
        assert "enable_ai_query_route = true" not in source
        assert "subprocess" not in imported
        assert "os.system" not in source


def test_sprint4_security_gates_stay_false():
    assert SPRINT4_LEDGER_WRITES_ENABLED is False
    assert SPRINT4_LIVE_AWS_ENABLED is False
    assert SPRINT4_AWS_PLAN_EXECUTION_ENABLED is False
    assert SPRINT4_CLIENT_NETWORK_INIT_ENABLED is False
    assert SHARED_INTERFACE_REQUIREMENTS


def test_sprint4_does_not_modify_frozen_or_probe_files():
    frozen = (
        SHARED / "contracts.py",
        SHARED / "specialist_contracts.py",
        SHARED / "sprint3d_integration_host.py",
        SHARED / "sprint3d_native_specialists.py",
        SHARED / "sprint3d_source_evidence.py",
    )
    for path in frozen:
        assert path.is_file()
    probe = REPO_ROOT / "functions" / "ai_execution_role_probe"
    assert (probe / "execution_role_probe.py").is_file()
    assert (probe / "PROCEDURE.md").is_file()


def test_ledger_module_does_not_call_prohibited_dynamodb_apis():
    source = (SHARED / "sprint4_security_ledger.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert "scan" not in called
    assert "query" not in called
    assert "delete_item" not in called
    assert "update_item" not in called
    assert "batch_write_item" not in called
    assert "create_table" not in called
    pytest.importorskip("wilvor_ai.sprint4_security_ledger")

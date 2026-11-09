"""Sprint 4 Lambda import and fake-client profiling. No live AWS, no probe edits."""

from __future__ import annotations

from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_security_lambda_profile import (
    ARMED_IMPORT_BUDGET_MS,
    CLIENT_INIT_BUDGET_MS,
    CLOSED_HANDLER_IMPORT_BUDGET_MS,
    FORBIDDEN_HANDLER_IMPORTS,
    HOST_MEMORY_MB,
    HOST_TIMEOUT_SECONDS,
    PROBE_RELATIVE_PATHS,
    SPRINT4_CLIENT_NETWORK_INIT_ENABLED,
    SPRINT4_LAMBDA_PROFILE_SCHEMA,
    assert_probe_artifacts_untouched,
    initialize_live_boto3_client,
    inspect_closed_handler,
    profile_sprint4_lambda,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeLedgerClient:
    def get_item(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def put_item(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def transact_write_items(self, **_kwargs: object) -> dict[str, object]:
        return {}


def test_closed_handler_stays_import_light_and_probe_files_are_untouched():
    imported = inspect_closed_handler(REPO_ROOT)
    assert FORBIDDEN_HANDLER_IMPORTS.isdisjoint(imported)
    assert "boto3" not in imported
    assert "wilvor_ai" not in imported
    assert_probe_artifacts_untouched(REPO_ROOT)
    for relative in PROBE_RELATIVE_PATHS:
        assert (REPO_ROOT / relative).is_file()
    handler = (REPO_ROOT / "modules" / "ai_host" / "src" / "handler.py").read_text(encoding="utf-8")
    assert "ai_host_invoke_closed" in handler
    assert "ledger_writes_enabled" in handler
    assert SPRINT4_CLIENT_NETWORK_INIT_ENABLED is False
    with pytest.raises(ContractValidationError, match="sprint4_client_network_init_disabled"):
        initialize_live_boto3_client(region="us-west-1")


def test_profile_report_stays_within_host_budget_and_does_not_import_boto3():
    report = profile_sprint4_lambda(REPO_ROOT, client_factory=FakeLedgerClient)
    assert bool(report) is False
    assert report.schema_version == SPRINT4_LAMBDA_PROFILE_SCHEMA
    assert report.closed_handler.imported is True
    assert report.closed_handler.boto3_imported is False
    assert report.closed_handler.live_network_used is False
    assert report.closed_handler.within_budget is True
    assert report.closed_handler.budget_ms == CLOSED_HANDLER_IMPORT_BUDGET_MS
    assert report.armed_aws_execution.imported is True
    assert report.armed_aws_execution.boto3_imported is False
    assert report.armed_aws_execution.budget_ms == ARMED_IMPORT_BUDGET_MS
    assert report.security_ledger.imported is True
    assert report.security_ledger.boto3_imported is False
    assert report.client_init.constructed is True
    assert report.client_init.network_used is False
    assert report.client_init.client_type == "FakeLedgerClient"
    assert report.client_init.budget_ms == CLIENT_INIT_BUDGET_MS
    assert report.client_init.within_budget is True
    budget = report.budget
    assert budget.timeout_seconds == HOST_TIMEOUT_SECONDS
    assert budget.memory_mb == HOST_MEMORY_MB
    assert budget.closed_handler_within_budget is True
    assert budget.client_init_within_budget is True
    assert budget.closed_handler_imports_wilvor_ai is False
    assert budget.probe_artifacts_modified is False
    assert budget.ready_for_live_invoke is False
    assert bool(budget) is False
    source = (REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "sprint3d_aws_execution.py").read_text(
        encoding="utf-8"
    )
    assert "import boto3" in source
    assert source.index("def build_boto3_dynamodb_client") < source.index("import boto3")

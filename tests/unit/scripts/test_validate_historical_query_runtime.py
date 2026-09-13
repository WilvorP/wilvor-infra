"""Offline tests for the 2B.6 historical query runtime validator."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "validate_historical_query_runtime.py"
SHARED = REPO_ROOT / "functions" / "shared"


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_historical_query_runtime",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return load_runner()


def test_script_exists_and_has_no_generic_sql_surface(runner: ModuleType):
    source = SCRIPT.read_text(encoding="utf-8")
    assert SCRIPT.is_file()
    for flag in runner.FORBIDDEN_CLI_FLAGS:
        assert flag in runner.FORBIDDEN_CLI_FLAGS
        assert f'"{flag}"' in source or f"'{flag}'" in source
    parser = runner.build_parser()
    dests = {action.dest for action in parser._actions}
    assert dests.isdisjoint(
        {"sql", "query_string", "table", "group_by", "order_by", "raw_athena", "query"}
    )
    help_text = parser.format_help()
    assert "--sql" not in help_text
    assert "--query-string" not in help_text
    assert "--raw-athena" not in help_text
    assert "summarize_historical_encounters" in help_text
    assert "list_historical_encounters" in help_text
    assert "client(\"dynamodb\")" not in source
    assert "resource(\"dynamodb\")" not in source
    assert "boto3" in source
    assert "HistoricalAnalyticsOperations" in source
    assert "CoverageGate" in source
    assert "AthenaExecutor" in source
    assert "create_aws_clients" in source
    assert "ResultReuseByAgeConfiguration" in source


def test_forbidden_flag_exits_before_dispatch(runner: ModuleType):
    code = runner.main(["--sql", "SELECT 1"])
    assert code == 2


def test_dry_run_makes_zero_aws_calls(runner: ModuleType, monkeypatch: pytest.MonkeyPatch):
    def fail_clients(_config):
        raise AssertionError("dry-run must not create AWS clients")

    monkeypatch.setattr(runner, "create_aws_clients", fail_clients)
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = runner.main(
        [
            "--action",
            "dry-run",
            "--operation",
            "summarize_historical_encounters",
            "--start-utc",
            "2026-09-11T00:00:00Z",
            "--end-utc",
            "2026-09-12T00:00:00Z",
            "--as-of-utc",
            "2026-09-13T12:00:00Z",
            "--account-id",
            "123456789012",
        ]
    )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["aws_calls"] == 0
    assert payload["sql_printed"] is False
    assert payload["operation"] == "summarize_historical_encounters"
    assert payload["requested_scope"]["start_utc"] == "2026-09-11T00:00:00Z"
    assert payload["config"]["workgroup"] == "wilvor-dev-historical-analytics"
    assert payload["config"]["database"] == "wilvor_dev_historical_facts"
    assert payload["config"]["results_prefix"].endswith("/athena-results/")
    assert payload["config"]["claims"]["operator_sso_does_not_prove_query_policy"] is True
    assert "SELECT" not in stdout.getvalue()


def test_dry_run_subprocess_zero_aws(runner: ModuleType):
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--action",
            "dry-run",
            "--operation",
            "summarize_historical_risks",
            "--start-utc",
            "2026-09-11T00:00:00Z",
            "--end-utc",
            "2026-09-12T00:00:00Z",
            "--as-of-utc",
            "2026-09-13T12:00:00Z",
            "--account-id",
            "123456789012",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(SHARED)},
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["aws_calls"] == 0
    assert payload["operation"] == "summarize_historical_risks"


def test_dry_run_list_requires_selector(runner: ModuleType):
    with pytest.raises(runner.ValidationRunnerError):
        runner.build_request(
            runner.build_parser().parse_args(
                [
                    "--action",
                    "dry-run",
                    "--operation",
                    "list_historical_encounters",
                    "--start-utc",
                    "2026-09-11T00:00:00Z",
                    "--end-utc",
                    "2026-09-12T00:00:00Z",
                    "--as-of-utc",
                    "2026-09-13T12:00:00Z",
                ]
            )
        )


def test_dry_run_list_and_hazard_version_requests(runner: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runner,
        "create_aws_clients",
        lambda _config: (_ for _ in ()).throw(AssertionError("no clients")),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = runner.main(
        [
            "--action",
            "dry-run",
            "--operation",
            "list_historical_encounters",
            "--start-utc",
            "2026-09-11T00:00:00Z",
            "--end-utc",
            "2026-09-12T00:00:00Z",
            "--as-of-utc",
            "2026-09-13T12:00:00Z",
            "--aircraft-id",
            "abc123",
            "--limit",
            "5",
            "--account-id",
            "123456789012",
        ]
    )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["requested_scope"]["aircraft_id"] == "abc123"
    assert payload["requested_scope"]["limit"] == 5

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = runner.main(
        [
            "--action",
            "dry-run",
            "--operation",
            "summarize_historical_hazard_versions",
            "--start-utc",
            "2026-09-11T00:00:00Z",
            "--end-utc",
            "2026-09-12T00:00:00Z",
            "--as-of-utc",
            "2026-09-13T12:00:00Z",
            "--account-id",
            "123456789012",
        ]
    )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert runner.HAZARD_VERSION_WINDOW_LIMITATION in payload["limitations"]


def test_future_as_of_is_refused_for_live_paths(runner: ModuleType):
    with pytest.raises(runner.ValidationRunnerError, match="future"):
        runner.refuse_future_as_of(
            "2099-01-01T00:00:00Z",
            now_utc="2026-09-13T16:00:00Z",
        )
    runner.refuse_future_as_of(
        "2026-09-13T16:00:00Z",
        now_utc="2026-09-13T16:00:00Z",
    )


def test_compare_snapshot_detects_changed_and_appended(runner: ModuleType, tmp_path: Path):
    before = {
        "objects": [
            {"Key": "dataset=encounter/a.jsonl.gz", "Size": 10, "ETag": "etag-a"},
            {"Key": "metadata/epoch/e.json", "Size": 2, "ETag": "etag-e"},
        ]
    }
    after_ok = {
        "objects": [
            {"Key": "dataset=encounter/a.jsonl.gz", "Size": 10, "ETag": "etag-a"},
            {"Key": "metadata/epoch/e.json", "Size": 2, "ETag": "etag-e"},
            {"Key": "metadata/coverage/new.json", "Size": 3, "ETag": "etag-n"},
        ]
    }
    after_bad = {
        "objects": [
            {"Key": "dataset=encounter/a.jsonl.gz", "Size": 11, "ETag": "etag-changed"},
            {"Key": "metadata/epoch/e.json", "Size": 2, "ETag": "etag-e"},
        ]
    }
    before_path = tmp_path / "before.json"
    after_ok_path = tmp_path / "after-ok.json"
    after_bad_path = tmp_path / "after-bad.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_ok_path.write_text(json.dumps(after_ok), encoding="utf-8")
    after_bad_path.write_text(json.dumps(after_bad), encoding="utf-8")

    ok = runner.compare_snapshots(before_path, after_ok_path)
    assert ok["preexisting_unchanged"] is True
    assert ok["blocker"] is False
    assert ok["appended_after_validation"] == ["metadata/coverage/new.json"]

    bad = runner.compare_snapshots(before_path, after_bad_path)
    assert bad["blocker"] is True
    assert bad["changed_preexisting"] == ["dataset=encounter/a.jsonl.gz"]


def test_structural_policy_proof_and_simulation_cases(runner: ModuleType):
    document = {
        "Statement": [
            {
                "Sid": "AthenaFixedQueries",
                "Action": [
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                ],
                "Resource": [
                    "arn:aws:athena:us-west-1:123:workgroup/wilvor-dev-historical-analytics"
                ],
            },
            {
                "Sid": "GlueCatalogRead",
                "Action": ["glue:GetDatabase", "glue:GetTable"],
                "Resource": [
                    "arn:aws:glue:us-west-1:123:catalog",
                    "arn:aws:glue:us-west-1:123:database/wilvor_dev_historical_facts",
                    "arn:aws:glue:us-west-1:123:table/wilvor_dev_historical_facts/encounter",
                    "arn:aws:glue:us-west-1:123:table/wilvor_dev_historical_facts/risk",
                    "arn:aws:glue:us-west-1:123:table/wilvor_dev_historical_facts/hazard_version",
                ],
            },
            {
                "Sid": "CanonicalObjectsRead",
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::facts/dataset=encounter/*"],
            },
        ]
    }
    proof = runner.structural_policy_proof(document)
    assert proof["athena_actions"] == [
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:StartQueryExecution",
        "athena:StopQueryExecution",
    ]
    assert proof["has_resource_star"] is False
    assert proof["has_iam"] is False
    assert proof["has_get_workgroup"] is False
    assert proof["has_hazard_geometry"] is False

    config = runner.RuntimeConfig(
        profile="wilvor-dev",
        region="us-west-1",
        name_prefix="wilvor-dev",
        account_id="123",
        facts_bucket="facts",
        results_bucket="results",
        workgroup="wilvor-dev-historical-analytics",
        database="wilvor_dev_historical_facts",
        results_prefix="s3://results/athena-results/",
        query_policy_name="wilvor-dev-historical-analytics-query",
        query_policy_arn=None,
    )
    names = {case["name"] for case in runner.iam_simulation_cases(document, config)}
    for required in (
        "allow_athena_start_dedicated_workgroup",
        "allow_canonical_list_metadata_epoch",
        "deny_athena_get_workgroup",
        "deny_glue_hazard_geometry",
        "deny_canonical_put",
        "deny_results_delete",
        "deny_iam_pass_role",
        "deny_sts_assume_role",
    ):
        assert required in names


def test_recording_athena_client_does_not_store_sql(runner: ModuleType):
    class Inner:
        def start_query_execution(self, **kwargs):
            return {"QueryExecutionId": "qid-1"}

        def get_query_execution(self, **kwargs):
            return {
                "QueryExecution": {
                    "QueryExecutionId": "qid-1",
                    "WorkGroup": "wilvor-dev-historical-analytics",
                    "QueryExecutionContext": {"Database": "wilvor_dev_historical_facts"},
                    "ResultConfiguration": {
                        "OutputLocation": "s3://results/athena-results/qid-1"
                    },
                    "Statistics": {
                        "DataScannedInBytes": 12,
                        "ResultReuseInformation": {"ReusedPreviousResult": False},
                    },
                    "Status": {"State": "SUCCEEDED"},
                }
            }

    recorder = runner.RecordingAthenaClient(Inner())
    recorder.start_query_execution(
        QueryString="SELECT 1",
        QueryExecutionContext={"Database": "wilvor_dev_historical_facts"},
        WorkGroup="wilvor-dev-historical-analytics",
        ResultReuseConfiguration={
            "ResultReuseByAgeConfiguration": {"Enabled": False}
        },
    )
    recorder.get_query_execution(QueryExecutionId="qid-1")
    assert recorder.start_payloads[0]["ResultReuseByAgeConfiguration.Enabled"] is False
    assert recorder.start_payloads[0]["QueryString_present"] is True
    assert "SELECT" not in json.dumps(recorder.start_payloads)
    assert recorder.execution_snapshots[0]["OutputLocation"].startswith(
        "s3://results/athena-results/"
    )


def test_closed_operations_match_runtime_catalog(runner: ModuleType):
    from wilvor_historical_query.operations import V1_OPERATION_METHODS

    assert tuple(runner.CLOSED_OPERATIONS) == V1_OPERATION_METHODS
    assert "summarize_historical_risks_by_level" not in runner.CLOSED_OPERATIONS


def test_compare_snapshot_action_does_not_create_clients(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        runner,
        "create_aws_clients",
        lambda _config: (_ for _ in ()).throw(AssertionError("no clients")),
    )
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    payload = {"objects": [{"Key": "k", "Size": 1, "ETag": "e"}]}
    before.write_text(json.dumps(payload), encoding="utf-8")
    after.write_text(json.dumps(payload), encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = runner.main(
        [
            "--action",
            "compare-snapshot",
            "--before",
            str(before),
            "--after",
            str(after),
        ]
    )
    assert code == 0
    assert json.loads(stdout.getvalue())["blocker"] is False

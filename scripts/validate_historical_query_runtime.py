"""Operator live-validation runner for Phase 2B deterministic historical analytics.

This script may import boto3. Production packages do not. It injects AWS
clients into CoverageStore / CoverageGate / AthenaExecutor /
HistoricalAnalyticsOperations and calls only the four closed V1 operations.

It is not a generic Athena or SQL tool. There is no --sql / --query-string
/ --table / --group-by / --order-by / --raw-athena interface.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from wilvor_historical.query_contracts import (  # noqa: E402
    HAZARD_VERSION_WINDOW_LIMITATION,
    HistoricalOperation,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
)
from wilvor_historical.time import HistoricalTimeError, canonicalize_utc_z, parse_utc_datetime  # noqa: E402
from wilvor_historical_query import (  # noqa: E402
    AthenaExecutor,
    AthenaExecutorConfig,
    CoverageGate,
    CoverageStore,
    CoverageStoreConfig,
    HistoricalAnalyticsOperations,
)
from wilvor_historical_query.operations import OPERATION_DATASETS  # noqa: E402


DEFAULT_PROFILE = "wilvor-dev"
DEFAULT_REGION = "us-west-1"
DEFAULT_NAME_PREFIX = "wilvor-dev"
ATHENA_RESULTS_PREFIX = "athena-results/"
CANONICAL_SNAPSHOT_PREFIXES = (
    "dataset=encounter/",
    "dataset=risk/",
    "dataset=hazard_version/",
    "dataset=hazard_geometry/",
    "metadata/epoch/",
    "metadata/activation/",
    "metadata/deactivation/",
    "metadata/coverage/",
    "metadata/gaps/",
    "metadata/resolutions/",
    "metadata/incidents/",
)
FORBIDDEN_CLI_FLAGS = (
    "--sql",
    "--query-string",
    "--table",
    "--group-by",
    "--order-by",
    "--raw-athena",
    "--query",
)
CLOSED_OPERATIONS = tuple(item.value for item in HistoricalOperation)
ACTIONS = (
    "dry-run",
    "inspect-coverage",
    "run-operation",
    "snapshot-canonical",
    "compare-snapshot",
    "iam-proof",
    "list-results",
)


class ValidationRunnerError(Exception):
    """Operator-script failure. Does not mutate historical facts."""


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str
    region: str
    name_prefix: str
    account_id: str | None
    facts_bucket: str
    results_bucket: str
    workgroup: str
    database: str
    results_prefix: str
    query_policy_name: str
    query_policy_arn: str | None


def utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def derive_resource_names(
    *,
    name_prefix: str,
    account_id: str,
    region: str,
) -> dict[str, str]:
    results_bucket = (
        f"{name_prefix}-historical-athena-results-{account_id}-{region}"
    )
    return {
        "facts_bucket": f"{name_prefix}-historical-facts-{account_id}-{region}",
        "results_bucket": results_bucket,
        "workgroup": f"{name_prefix}-historical-analytics",
        "database": f"{name_prefix.replace('-', '_')}_historical_facts",
        "query_policy_name": f"{name_prefix}-historical-analytics-query",
        "results_prefix": f"s3://{results_bucket}/{ATHENA_RESULTS_PREFIX}",
    }


def parse_operation(value: str) -> HistoricalOperation:
    if value not in CLOSED_OPERATIONS:
        raise ValidationRunnerError(
            "operation must be one of: " + ", ".join(CLOSED_OPERATIONS)
        )
    return HistoricalOperation(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_historical_query_runtime.py",
        description=(
            "Validate the deterministic historical analytics runtime. "
            "Closed four-operation catalog only. Not a generic SQL tool."
        ),
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=ACTIONS,
        help="Closed validation action.",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX)
    parser.add_argument("--account-id")
    parser.add_argument("--facts-bucket")
    parser.add_argument("--results-bucket")
    parser.add_argument("--workgroup")
    parser.add_argument("--database")
    parser.add_argument("--results-prefix")
    parser.add_argument("--query-policy-name")
    parser.add_argument("--query-policy-arn")
    parser.add_argument(
        "--operation",
        choices=CLOSED_OPERATIONS,
        help="One HistoricalOperation value.",
    )
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--as-of-utc")
    parser.add_argument("--aircraft-id")
    parser.add_argument("--hazard-id")
    parser.add_argument("--hazard-type")
    parser.add_argument("--product-type")
    parser.add_argument("--encounter-id")
    parser.add_argument("--risk-level")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument(
        "--execution-id",
        action="append",
        dest="execution_ids",
        default=[],
        help="Optional QueryExecutionId to correlate with results-bucket objects.",
    )
    return parser


def resolve_config(args: argparse.Namespace, *, account_id: str | None) -> RuntimeConfig:
    derived: dict[str, str] = {}
    if account_id:
        derived = derive_resource_names(
            name_prefix=args.name_prefix,
            account_id=account_id,
            region=args.region,
        )
    facts_bucket = args.facts_bucket or derived.get("facts_bucket")
    results_bucket = args.results_bucket or derived.get("results_bucket")
    workgroup = args.workgroup or derived.get("workgroup")
    database = args.database or derived.get("database")
    results_prefix = args.results_prefix or derived.get("results_prefix")
    query_policy_name = args.query_policy_name or derived.get("query_policy_name")
    missing = [
        name
        for name, value in (
            ("facts_bucket", facts_bucket),
            ("results_bucket", results_bucket),
            ("workgroup", workgroup),
            ("database", database),
            ("results_prefix", results_prefix),
            ("query_policy_name", query_policy_name),
        )
        if not value
    ]
    if missing:
        raise ValidationRunnerError(
            "missing derived names "
            + ", ".join(missing)
            + "; pass --account-id or explicit resource names"
        )
    if not str(results_prefix).endswith(f"/{ATHENA_RESULTS_PREFIX}"):
        raise ValidationRunnerError(
            "results prefix must end with /athena-results/"
        )
    return RuntimeConfig(
        profile=args.profile,
        region=args.region,
        name_prefix=args.name_prefix,
        account_id=account_id,
        facts_bucket=str(facts_bucket),
        results_bucket=str(results_bucket),
        workgroup=str(workgroup),
        database=str(database),
        results_prefix=str(results_prefix),
        query_policy_name=str(query_policy_name),
        query_policy_arn=args.query_policy_arn,
    )


def build_request(args: argparse.Namespace) -> Any:
    if not args.operation:
        raise ValidationRunnerError("--operation is required")
    if not args.start_utc or not args.end_utc:
        raise ValidationRunnerError("--start-utc and --end-utc are required")
    if not args.as_of_utc:
        raise ValidationRunnerError("--as-of-utc is required")
    operation = parse_operation(args.operation)
    try:
        if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS:
            return SummarizeHistoricalEncountersRequest(
                start_utc=args.start_utc,
                end_utc=args.end_utc,
                aircraft_id=args.aircraft_id,
                hazard_id=args.hazard_id,
                hazard_type=args.hazard_type,
            )
        if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS:
            return SummarizeHistoricalRisksRequest(
                start_utc=args.start_utc,
                end_utc=args.end_utc,
                aircraft_id=args.aircraft_id,
                hazard_id=args.hazard_id,
                encounter_id=args.encounter_id,
                risk_level=args.risk_level,
            )
        if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS:
            return SummarizeHistoricalHazardVersionsRequest(
                start_utc=args.start_utc,
                end_utc=args.end_utc,
                hazard_id=args.hazard_id,
                hazard_type=args.hazard_type,
                product_type=args.product_type,
            )
        return ListHistoricalEncountersRequest(
            start_utc=args.start_utc,
            end_utc=args.end_utc,
            aircraft_id=args.aircraft_id,
            hazard_id=args.hazard_id,
            limit=args.limit if args.limit is not None else 5,
        )
    except Exception as exc:
        raise ValidationRunnerError(str(exc)) from exc


def canonicalize_as_of(as_of_utc: str) -> str:
    try:
        return canonicalize_utc_z(parse_utc_datetime(as_of_utc))
    except HistoricalTimeError as exc:
        raise ValidationRunnerError("invalid as_of_utc") from exc


def refuse_future_as_of(as_of_utc: str, *, now_utc: str) -> None:
    if canonicalize_as_of(as_of_utc) > now_utc:
        raise ValidationRunnerError(
            "as_of_utc must not be in the future relative to validation time"
        )


def public_config_dict(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "profile": config.profile,
        "region": config.region,
        "name_prefix": config.name_prefix,
        "account_id": config.account_id,
        "facts_bucket": config.facts_bucket,
        "results_bucket": config.results_bucket,
        "workgroup": config.workgroup,
        "database": config.database,
        "results_prefix": config.results_prefix,
        "query_policy_name": config.query_policy_name,
        "query_policy_arn": config.query_policy_arn,
        "claims": {
            "operator_sso_does_not_prove_query_policy": True,
            "iam_simulation_is_identity_policy_logic_only": True,
            "no_current_dynamodb_enrichment": True,
        },
    }


def response_evidence(response: Any) -> dict[str, Any]:
    payload = response.to_dict()
    payload["result_reuse_claimed_by_response"] = False
    return payload


def emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def dry_run(args: argparse.Namespace) -> dict[str, Any]:
    config = resolve_config(args, account_id=args.account_id or "ACCOUNT_ID")
    request = build_request(args)
    as_of = canonicalize_as_of(args.as_of_utc)
    operation = parse_operation(args.operation)
    return {
        "action": "dry-run",
        "aws_calls": 0,
        "operation": operation.value,
        "as_of_utc": as_of,
        "requested_scope": request.to_dict(),
        "datasets": list(OPERATION_DATASETS[operation]),
        "config": public_config_dict(config),
        "sql_printed": False,
        "limitations": (
            [HAZARD_VERSION_WINDOW_LIMITATION]
            if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS
            else []
        ),
    }


class RecordingAthenaClient:
    """Delegates to a real Athena client and records start-payload proof."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.start_payloads: list[dict[str, Any]] = []
        self.execution_snapshots: list[dict[str, Any]] = []

    def start_query_execution(self, **kwargs: Any) -> Any:
        context = kwargs.get("QueryExecutionContext") or {}
        reuse = (
            (kwargs.get("ResultReuseConfiguration") or {})
            .get("ResultReuseByAgeConfiguration")
            or {}
        )
        self.start_payloads.append(
            {
                "WorkGroup": kwargs.get("WorkGroup"),
                "Database": context.get("Database") if isinstance(context, Mapping) else None,
                "ResultReuseByAgeConfiguration.Enabled": reuse.get("Enabled"),
                "ResultConfiguration_present": "ResultConfiguration" in kwargs,
                "QueryString_present": "QueryString" in kwargs,
            }
        )
        return self._inner.start_query_execution(**kwargs)

    def get_query_execution(self, **kwargs: Any) -> Any:
        response = self._inner.get_query_execution(**kwargs)
        execution = response.get("QueryExecution") if isinstance(response, Mapping) else None
        if isinstance(execution, Mapping):
            result_config = execution.get("ResultConfiguration") or {}
            context = execution.get("QueryExecutionContext") or {}
            statistics = execution.get("Statistics") or {}
            reuse_info = statistics.get("ResultReuseInformation")
            self.execution_snapshots.append(
                {
                    "QueryExecutionId": execution.get("QueryExecutionId"),
                    "WorkGroup": execution.get("WorkGroup"),
                    "Database": context.get("Database") if isinstance(context, Mapping) else None,
                    "OutputLocation": (
                        result_config.get("OutputLocation")
                        if isinstance(result_config, Mapping)
                        else None
                    ),
                    "DataScannedInBytes": (
                        statistics.get("DataScannedInBytes")
                        if isinstance(statistics, Mapping)
                        else None
                    ),
                    "ResultReuseInformation": reuse_info,
                    "Status": (
                        (execution.get("Status") or {}).get("State")
                        if isinstance(execution.get("Status"), Mapping)
                        else None
                    ),
                }
            )
        return response

    def get_query_results(self, **kwargs: Any) -> Any:
        return self._inner.get_query_results(**kwargs)

    def stop_query_execution(self, **kwargs: Any) -> Any:
        return self._inner.stop_query_execution(**kwargs)


def create_aws_clients(config: RuntimeConfig) -> dict[str, Any]:
    import boto3

    session = boto3.Session(profile_name=config.profile, region_name=config.region)
    return {
        "session": session,
        "sts": session.client("sts"),
        "s3": session.client("s3"),
        "athena": session.client("athena"),
        "iam": session.client("iam"),
    }


def build_operations(s3_client: Any, athena_client: Any, config: RuntimeConfig) -> HistoricalAnalyticsOperations:
    store = CoverageStore(
        s3_client=s3_client,
        config=CoverageStoreConfig(bucket_name=config.facts_bucket),
    )
    return HistoricalAnalyticsOperations(
        coverage_gate=CoverageGate(store=store),
        executor=AthenaExecutor(
            athena_client=athena_client,
            config=AthenaExecutorConfig(
                workgroup=config.workgroup,
                database=config.database,
                expected_results_prefix=config.results_prefix,
            ),
        ),
    )


def run_operation(args: argparse.Namespace, config: RuntimeConfig, clients: Mapping[str, Any]) -> dict[str, Any]:
    now_utc = utc_now_z()
    refuse_future_as_of(args.as_of_utc, now_utc=now_utc)
    request = build_request(args)
    operation = parse_operation(args.operation)
    recorder = RecordingAthenaClient(clients["athena"])
    ops = build_operations(clients["s3"], recorder, config)
    method = getattr(ops, operation.value)
    response = method(request, as_of_utc=canonicalize_as_of(args.as_of_utc))
    payload = {
        "action": "run-operation",
        "validation_time_utc": now_utc,
        "operation": operation.value,
        "response": response_evidence(response),
        "athena_start_payloads": recorder.start_payloads,
        "athena_execution_snapshots": recorder.execution_snapshots,
        "operator_sso_does_not_prove_query_policy": True,
        "config": public_config_dict(config),
    }
    if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS:
        limitations = list(payload["response"].get("limitations") or [])
        if HAZARD_VERSION_WINDOW_LIMITATION not in limitations:
            limitations.append(HAZARD_VERSION_WINDOW_LIMITATION)
        payload["response"]["limitations"] = limitations
    return payload


def summarize_coverage_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "epoch_ids": [item.collection_epoch_id for item in snapshot.epochs],
        "activations": [
            {
                "collection_epoch_id": item.collection_epoch_id,
                "enabled_at_utc": item.enabled_at_utc,
            }
            for item in snapshot.activations
        ],
        "deactivations": [
            {
                "collection_epoch_id": item.collection_epoch_id,
                "deactivated_at_utc": item.deactivated_at_utc,
            }
            for item in snapshot.deactivations
        ],
        "coverage_intervals": [
            {
                "collection_epoch_id": item.collection_epoch_id,
                "stream": item.stream.value,
                "interval_start_utc": item.interval_start_utc,
                "interval_end_utc": item.interval_end_utc,
            }
            for item in snapshot.coverage_intervals
        ],
        "gap_count": len(snapshot.gaps),
        "resolution_count": len(snapshot.resolutions),
        "incident_count": len(snapshot.incidents),
        "metadata_object_count": len(snapshot.object_keys),
    }


def inspect_coverage(args: argparse.Namespace, config: RuntimeConfig, clients: Mapping[str, Any]) -> dict[str, Any]:
    store = CoverageStore(
        s3_client=clients["s3"],
        config=CoverageStoreConfig(bucket_name=config.facts_bucket),
    )
    snapshot = store.load()
    payload: dict[str, Any] = {
        "action": "inspect-coverage",
        "facts_bucket": config.facts_bucket,
        "metadata": summarize_coverage_snapshot(snapshot),
        "athena_calls": 0,
    }
    if args.start_utc and args.end_utc and args.as_of_utc:
        refuse_future_as_of(args.as_of_utc, now_utc=utc_now_z())
        operation = parse_operation(
            args.operation or HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS.value
        )
        gate = CoverageGate(store=store)
        result = gate.evaluate(
            datasets=OPERATION_DATASETS[operation],
            start_utc=args.start_utc,
            end_utc=args.end_utc,
            as_of_utc=canonicalize_as_of(args.as_of_utc),
        )
        payload["window"] = {
            "operation": operation.value,
            "start_utc": args.start_utc,
            "end_utc": args.end_utc,
            "as_of_utc": canonicalize_as_of(args.as_of_utc),
            "allowed_to_query": result.allowed_to_query,
            "evaluability": (
                None if result.evaluability is None else result.evaluability.value
            ),
            "coverage": result.coverage.to_dict(),
            "slice_evaluations": [
                {
                    "collection_epoch_id": item.collection_epoch_id,
                    "start_utc": item.start_utc,
                    "end_utc": item.end_utc,
                    "dataset": item.dataset,
                    "evaluability": item.evaluability.value,
                    "reason": item.reason,
                    "required_horizon_seconds": item.required_horizon_seconds,
                    "required_streams": list(item.required_streams),
                }
                for item in result.slice_evaluations
            ],
        }
    return payload


def list_prefix_objects(s3_client: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token is not None:
            kwargs["ContinuationToken"] = token
        response = s3_client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = item.get("Key")
            if not isinstance(key, str) or not key or key.endswith("/"):
                continue
            objects.append(
                {
                    "Key": key,
                    "Size": item.get("Size"),
                    "ETag": item.get("ETag"),
                }
            )
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not token:
            raise ValidationRunnerError("truncated listing missing NextContinuationToken")
    objects.sort(key=lambda item: item["Key"])
    return objects


def snapshot_canonical(config: RuntimeConfig, clients: Mapping[str, Any], output: Path) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for prefix in CANONICAL_SNAPSHOT_PREFIXES:
        for item in list_prefix_objects(clients["s3"], config.facts_bucket, prefix):
            seen[item["Key"]] = item
    records = [seen[key] for key in sorted(seen)]
    payload = {
        "action": "snapshot-canonical",
        "bucket": config.facts_bucket,
        "captured_at_utc": utc_now_z(),
        "object_count": len(records),
        "objects": records,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "action": "snapshot-canonical",
        "bucket": config.facts_bucket,
        "output": str(output),
        "object_count": len(records),
        "mutated_canonical_bucket": False,
    }


def compare_snapshots(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    before_map = {item["Key"]: item for item in before["objects"]}
    after_map = {item["Key"]: item for item in after["objects"]}
    missing = sorted(set(before_map) - set(after_map))
    added = sorted(set(after_map) - set(before_map))
    changed = sorted(
        key
        for key in set(before_map) & set(after_map)
        if before_map[key] != after_map[key]
    )
    return {
        "action": "compare-snapshot",
        "before": str(before_path),
        "after": str(after_path),
        "preexisting_unchanged": not missing and not changed,
        "missing_preexisting": missing,
        "changed_preexisting": changed,
        "appended_after_validation": added,
        "appended_count": len(added),
        "blocker": bool(missing or changed),
    }


def list_results(config: RuntimeConfig, clients: Mapping[str, Any], execution_ids: list[str]) -> dict[str, Any]:
    objects = list_prefix_objects(
        clients["s3"],
        config.results_bucket,
        ATHENA_RESULTS_PREFIX,
    )
    outside = [
        item
        for item in list_prefix_objects(clients["s3"], config.results_bucket, "")
        if not str(item["Key"]).startswith(ATHENA_RESULTS_PREFIX)
    ]
    correlated = [
        item
        for item in objects
        if any(execution_id in str(item["Key"]) for execution_id in execution_ids)
    ]
    return {
        "action": "list-results",
        "results_bucket": config.results_bucket,
        "approved_prefix": ATHENA_RESULTS_PREFIX,
        "object_count": len(objects),
        "objects_outside_approved_prefix": outside,
        "correlated_execution_objects": correlated,
        "canonical_bucket_not_used_for_results": True,
    }


def policy_actions(document: Mapping[str, Any]) -> set[str]:
    actions: set[str] = set()
    for statement in document.get("Statement") or []:
        value = statement.get("Action")
        if isinstance(value, str):
            actions.add(value)
        elif isinstance(value, list):
            actions.update(str(item) for item in value)
    return actions


def policy_resources(document: Mapping[str, Any], sid: str) -> list[str]:
    for statement in document.get("Statement") or []:
        if statement.get("Sid") == sid:
            value = statement.get("Resource")
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [str(item) for item in value]
    return []


def iam_simulation_cases(document: Mapping[str, Any], config: RuntimeConfig) -> list[dict[str, Any]]:
    workgroups = policy_resources(document, "AthenaFixedQueries")
    glue_resources = policy_resources(document, "GlueCatalogRead")
    encounter_table = next(
        (item for item in glue_resources if item.endswith("table/encounter") or ":table/" in item and item.endswith("/encounter")),
        None,
    )
    if encounter_table is None:
        encounter_table = next((item for item in glue_resources if "encounter" in item), "")
    risk_table = next((item for item in glue_resources if item.endswith("/risk") or item.endswith(":risk")), "")
    hazard_version_table = next(
        (item for item in glue_resources if "hazard_version" in item),
        "",
    )
    catalog = next((item for item in glue_resources if item.endswith(":catalog")), "")
    workgroup_arn = workgroups[0] if workgroups else ""
    other_workgroup = workgroup_arn.replace(
        config.workgroup,
        f"{config.name_prefix}-other-workgroup",
    )
    partition = "aws"
    region = config.region
    account = config.account_id or "ACCOUNT"
    database = config.database
    geometry_table = (
        f"arn:{partition}:glue:{region}:{account}:table/{database}/hazard_geometry"
    )
    facts = f"arn:aws:s3:::{config.facts_bucket}"
    results = f"arn:aws:s3:::{config.results_bucket}"
    return [
        {
            "name": "allow_athena_start_dedicated_workgroup",
            "expect": "allowed",
            "ActionNames": ["athena:StartQueryExecution"],
            "ResourceArns": [workgroup_arn],
        },
        {
            "name": "allow_athena_get_results_dedicated_workgroup",
            "expect": "allowed",
            "ActionNames": ["athena:GetQueryResults"],
            "ResourceArns": [workgroup_arn],
        },
        {
            "name": "allow_glue_get_table_encounter",
            "expect": "allowed",
            "ActionNames": ["glue:GetTable"],
            "ResourceArns": [item for item in (catalog, encounter_table) if item],
        },
        {
            "name": "allow_glue_get_table_risk",
            "expect": "allowed",
            "ActionNames": ["glue:GetTable"],
            "ResourceArns": [item for item in (catalog, risk_table) if item],
        },
        {
            "name": "allow_glue_get_table_hazard_version",
            "expect": "allowed",
            "ActionNames": ["glue:GetTable"],
            "ResourceArns": [item for item in (catalog, hazard_version_table) if item],
        },
        {
            "name": "allow_canonical_get_encounter_object",
            "expect": "allowed",
            "ActionNames": ["s3:GetObject"],
            "ResourceArns": [f"{facts}/dataset=encounter/year=2026/month=09/day=11/object.jsonl.gz"],
        },
        {
            "name": "allow_canonical_get_metadata_gap",
            "expect": "allowed",
            "ActionNames": ["s3:GetObject"],
            "ResourceArns": [f"{facts}/metadata/gaps/gap.json"],
        },
        {
            "name": "allow_canonical_list_metadata_epoch",
            "expect": "allowed",
            "ActionNames": ["s3:ListBucket"],
            "ResourceArns": [facts],
            "ContextEntries": [
                {
                    "ContextKeyName": "s3:prefix",
                    "ContextKeyValues": ["metadata/epoch/"],
                    "ContextKeyType": "string",
                }
            ],
        },
        {
            "name": "allow_results_put_athena_results",
            "expect": "allowed",
            "ActionNames": ["s3:PutObject"],
            "ResourceArns": [f"{results}/{ATHENA_RESULTS_PREFIX}qid/results.csv"],
        },
        {
            "name": "deny_athena_other_workgroup",
            "expect": "implicitDeny",
            "ActionNames": ["athena:StartQueryExecution"],
            "ResourceArns": [other_workgroup],
        },
        {
            "name": "deny_athena_get_workgroup",
            "expect": "implicitDeny",
            "ActionNames": ["athena:GetWorkGroup"],
            "ResourceArns": [workgroup_arn],
        },
        {
            "name": "deny_glue_hazard_geometry",
            "expect": "implicitDeny",
            "ActionNames": ["glue:GetTable"],
            "ResourceArns": [geometry_table],
        },
        {
            "name": "deny_canonical_hazard_geometry_object",
            "expect": "implicitDeny",
            "ActionNames": ["s3:GetObject"],
            "ResourceArns": [f"{facts}/dataset=hazard_geometry/year=2026/month=09/day=11/object.jsonl.gz"],
        },
        {
            "name": "deny_canonical_put",
            "expect": "implicitDeny",
            "ActionNames": ["s3:PutObject"],
            "ResourceArns": [f"{facts}/dataset=encounter/year=2026/month=09/day=11/object.jsonl.gz"],
        },
        {
            "name": "deny_canonical_delete",
            "expect": "implicitDeny",
            "ActionNames": ["s3:DeleteObject"],
            "ResourceArns": [f"{facts}/dataset=encounter/year=2026/month=09/day=11/object.jsonl.gz"],
        },
        {
            "name": "deny_results_delete",
            "expect": "implicitDeny",
            "ActionNames": ["s3:DeleteObject"],
            "ResourceArns": [f"{results}/{ATHENA_RESULTS_PREFIX}qid/results.csv"],
        },
        {
            "name": "deny_results_outside_prefix",
            "expect": "implicitDeny",
            "ActionNames": ["s3:PutObject"],
            "ResourceArns": [f"{results}/other-prefix/object"],
        },
        {
            "name": "deny_iam_pass_role",
            "expect": "implicitDeny",
            "ActionNames": ["iam:PassRole"],
            "ResourceArns": [f"arn:aws:iam::{account}:role/{config.name_prefix}-any"],
        },
        {
            "name": "deny_sts_assume_role",
            "expect": "implicitDeny",
            "ActionNames": ["sts:AssumeRole"],
            "ResourceArns": [f"arn:aws:iam::{account}:role/{config.name_prefix}-any"],
        },
    ]


def structural_policy_proof(document: Mapping[str, Any]) -> dict[str, Any]:
    actions = policy_actions(document)
    athena = sorted(item for item in actions if item.startswith("athena:"))
    glue = sorted(item for item in actions if item.startswith("glue:"))
    serialized = json.dumps(document)
    return {
        "athena_actions": athena,
        "glue_actions": glue,
        "has_resource_star": '"Resource":"*"' in serialized.replace(" ", "")
        or '"Resource": "*"' in serialized,
        "has_action_star": any(item.endswith(":*") for item in actions),
        "has_iam": any(item.startswith("iam:") for item in actions),
        "has_sts": any(item.startswith("sts:") for item in actions),
        "has_kms": any(item.startswith("kms:") for item in actions),
        "has_lambda_invoke": "lambda:InvokeFunction" in actions,
        "has_get_workgroup": "athena:GetWorkGroup" in actions,
        "has_get_partitions": "glue:GetPartitions" in actions,
        "has_hazard_geometry": "hazard_geometry" in serialized,
        "has_canonical_put": "PutObject" in serialized
        and any(
            "historical-facts" in str(statement.get("Resource"))
            and "PutObject" in str(statement.get("Action"))
            for statement in document.get("Statement") or []
        ),
    }


def iam_proof(config: RuntimeConfig, clients: Mapping[str, Any]) -> dict[str, Any]:
    iam = clients["iam"]
    arn = config.query_policy_arn
    if not arn:
        listed = iam.list_policies(Scope="Local", PathPrefix="/")
        matches = [
            item
            for item in listed.get("Policies") or []
            if item.get("PolicyName") == config.query_policy_name
        ]
        if not matches:
            raise ValidationRunnerError(
                f"managed policy not found: {config.query_policy_name}"
            )
        arn = matches[0]["Arn"]
    policy = iam.get_policy(PolicyArn=arn)["Policy"]
    version_id = policy["DefaultVersionId"]
    version = iam.get_policy_version(PolicyArn=arn, VersionId=version_id)
    document = version["PolicyVersion"]["Document"]
    if isinstance(document, str):
        document = json.loads(document)
    structure = structural_policy_proof(document)
    cases = iam_simulation_cases(document, config)
    simulations: list[dict[str, Any]] = []
    simulation_available = True
    simulation_error: str | None = None
    for case in cases:
        kwargs: dict[str, Any] = {
            "PolicyInputList": [json.dumps(document)],
            "ActionNames": case["ActionNames"],
            "ResourceArns": case["ResourceArns"],
        }
        if case.get("ContextEntries"):
            kwargs["ContextEntries"] = case["ContextEntries"]
        try:
            response = iam.simulate_custom_policy(**kwargs)
        except Exception as exc:
            simulation_available = False
            simulation_error = str(exc)
            simulations.append(
                {
                    "name": case["name"],
                    "expect": case["expect"],
                    "decision": "SIMULATOR_UNAVAILABLE",
                    "error": simulation_error,
                }
            )
            break
        results = response.get("EvaluationResults") or []
        decision = results[0].get("EvalDecision") if results else "unknown"
        simulations.append(
            {
                "name": case["name"],
                "expect": case["expect"],
                "decision": decision,
                "matched": _simulation_matched(case["expect"], decision),
                "action": case["ActionNames"][0],
                "resources": case["ResourceArns"],
            }
        )
    return {
        "action": "iam-proof",
        "policy_arn": arn,
        "policy_name": config.query_policy_name,
        "default_version": version_id,
        "attached_to_operator": False,
        "structure": structure,
        "simulation_available": simulation_available,
        "simulation_error": simulation_error,
        "simulations": simulations,
        "limitations": [
            "IAM simulation proves identity-policy document logic only.",
            "It does not prove SCPs, permissions boundaries, resource policies, or a future Lambda role.",
            "Operator SSO execution does not prove this managed policy.",
        ],
    }


def _simulation_matched(expect: str, decision: str) -> bool:
    normalized = decision.replace(" ", "").lower()
    if expect == "allowed":
        return normalized == "allowed"
    return normalized in {"implicitdeny", "explicitdeny", "denied"}


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "dry-run":
        return dry_run(args)
    if args.action == "compare-snapshot":
        if not args.before or not args.after:
            raise ValidationRunnerError("--before and --after are required")
        return compare_snapshots(Path(args.before), Path(args.after))

    account_id = args.account_id
    clients: dict[str, Any] | None = None
    if account_id is None or args.action != "dry-run":
        preview = RuntimeConfig(
            profile=args.profile,
            region=args.region,
            name_prefix=args.name_prefix,
            account_id=account_id,
            facts_bucket=args.facts_bucket or "pending",
            results_bucket=args.results_bucket or "pending",
            workgroup=args.workgroup or "pending",
            database=args.database or "pending",
            results_prefix=args.results_prefix or f"s3://pending/{ATHENA_RESULTS_PREFIX}",
            query_policy_name=args.query_policy_name or "pending",
            query_policy_arn=args.query_policy_arn,
        )
        clients = create_aws_clients(preview)
        if account_id is None:
            identity = clients["sts"].get_caller_identity()
            account_id = identity["Account"]
    config = resolve_config(args, account_id=account_id)
    if clients is None:
        clients = create_aws_clients(config)

    if args.action == "inspect-coverage":
        return inspect_coverage(args, config, clients)
    if args.action == "run-operation":
        return run_operation(args, config, clients)
    if args.action == "snapshot-canonical":
        if not args.output:
            raise ValidationRunnerError("--output is required")
        return snapshot_canonical(config, clients, Path(args.output))
    if args.action == "iam-proof":
        return iam_proof(config, clients)
    if args.action == "list-results":
        return list_results(config, clients, list(args.execution_ids or []))
    raise ValidationRunnerError(f"unsupported action: {args.action}")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    for flag in FORBIDDEN_CLI_FLAGS:
        if flag in raw:
            print(f"forbidden flag: {flag}", file=sys.stderr)
            return 2
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
        payload = dispatch(args)
    except ValidationRunnerError as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    emit(payload)
    if payload.get("blocker") is True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

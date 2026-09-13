"""Static contract tests for the unattached 2B.5 query IAM policy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_analytics"
DEV_MAIN = REPO_ROOT / "envs" / "dev" / "main.tf"
DEV_OUTPUTS = REPO_ROOT / "envs" / "dev" / "outputs.tf"
DATA_PLANE_MAIN = REPO_ROOT / "envs" / "dev-historical-data" / "main.tf"
EXECUTOR = (
    REPO_ROOT / "functions" / "shared" / "wilvor_historical_query" / "executor.py"
)
COVERAGE_STORE = (
    REPO_ROOT / "functions" / "shared" / "wilvor_historical_query" / "coverage_store.py"
)
QUERY_CONTRACTS = (
    REPO_ROOT / "functions" / "shared" / "wilvor_historical" / "query_contracts.py"
)
GLUE = MODULE_DIR / "glue.tf"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def module_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_DIR.glob("*.tf"))
    )


def _statement(iam: str, sid: str) -> str:
    marker = f'sid    = "{sid}"'
    assert marker in iam, sid
    after = iam.split(marker, 1)[1]
    next_sid = after.find("sid    = ")
    if next_sid == -1:
        return after
    return after[:next_sid]


def test_query_policy_is_one_unattached_managed_policy():
    iam = read(MODULE_DIR / "iam.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    outputs = read(MODULE_DIR / "outputs.tf")
    text = module_text()
    assert iam.count('resource "aws_iam_policy"') == 1
    assert 'resource "aws_iam_policy" "query"' in iam
    assert "count = local.enabled ? 1 : 0" in iam
    assert (
        'query_policy_name      = "${var.name_prefix}-historical-analytics-query"'
        in locals_text
    )
    assert "name        = local.query_policy_name" in iam
    assert "aws_iam_role" not in text
    assert "aws_iam_role_policy" not in text
    assert "aws_iam_policy_attachment" not in text
    assert "aws_iam_role_policy_attachment" not in text
    assert "assume_role" not in iam.lower()
    assert 'output "query_policy_arn"' in outputs
    assert "aws_iam_policy.query[0].arn" in outputs
    assert 'output "query_policy_name"' in outputs
    assert "query_role" not in outputs


def test_athena_actions_are_the_four_executor_calls_on_the_workgroup():
    iam = read(MODULE_DIR / "iam.tf")
    executor = read(EXECUTOR)
    statement = _statement(iam, "AthenaFixedQueries")
    actions = re.findall(r'"athena:[A-Za-z]+"', statement)
    assert actions == [
        '"athena:StartQueryExecution"',
        '"athena:GetQueryExecution"',
        '"athena:GetQueryResults"',
        '"athena:StopQueryExecution"',
    ]
    assert "start_query_execution" in executor
    assert "get_query_execution" in executor
    assert "get_query_results" in executor
    assert "stop_query_execution" in executor
    assert "get_work_group" not in executor
    assert "athena:GetWorkGroup" not in iam
    assert "aws_athena_workgroup.historical_analytics[0].arn" in statement
    assert '"*"' not in statement
    assert "athena:*" not in iam
    assert "ListWorkGroups" not in iam
    assert "namedquery" not in iam.lower()
    assert "preparedstatement" not in iam.lower()


def test_glue_reads_only_v1_queried_tables():
    iam = read(MODULE_DIR / "iam.tf")
    glue = read(GLUE)
    statement = _statement(iam, "GlueCatalogRead")
    assert '"glue:GetDatabase"' in statement
    assert '"glue:GetTable"' in statement
    assert "glue:GetPartitions" not in iam
    assert "glue:GetTables" not in iam
    assert "glue:Create" not in iam
    assert "glue:Update" not in iam
    assert "glue:Delete" not in iam
    assert "crawler" not in iam.lower()
    assert "aws_glue_catalog_database.historical_facts[0].arn" in statement
    assert 'aws_glue_catalog_table.structured["encounter"].arn' in statement
    assert 'aws_glue_catalog_table.structured["risk"].arn' in statement
    assert 'aws_glue_catalog_table.structured["hazard_version"].arn' in statement
    assert "hazard_geometry" not in statement
    assert "/*" not in statement
    assert '"*"' not in statement
    assert "glue:*" not in iam
    locals_text = read(MODULE_DIR / "locals.tf")
    assert '"projection.enabled"      = "true"' in locals_text
    assert "local.partition_projection_parameters" in glue


def test_canonical_s3_is_read_only_and_prefix_constrained():
    iam = read(MODULE_DIR / "iam.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    contracts = read(QUERY_CONTRACTS)
    store = read(COVERAGE_STORE)
    list_statement = _statement(iam, "CanonicalBucketList")
    object_statement = _statement(iam, "CanonicalObjectsRead")
    location_statement = _statement(iam, "CanonicalBucketLocation")

    assert '"s3:ListBucket"' in list_statement
    assert "data.aws_s3_bucket.historical_facts[0].arn" in list_statement
    assert 'variable = "s3:prefix"' in list_statement
    assert "local.query_canonical_list_prefixes" in list_statement

    for prefix in (
        "metadata/epoch/",
        "metadata/activation/",
        "metadata/deactivation/",
        "metadata/coverage/",
        "metadata/gaps/",
        "metadata/resolutions/",
        "metadata/incidents/",
    ):
        assert f'"{prefix}"' in locals_text
        assert prefix in contracts
        assert "COVERAGE_STORE_METADATA_PREFIXES" in store or "metadata/epoch/" in store

    assert '"dataset=encounter/"' in locals_text or "dataset=${dataset}/" in locals_text
    assert "hazard_geometry" not in locals_text.split("query_v1_datasets")[1].split("]")[0]
    assert "dataset=hazard_geometry" not in iam
    assert '"s3:GetObject"' in object_statement
    assert '"s3:PutObject"' not in object_statement
    assert "DeleteObject" not in iam
    assert "s3:PutObject" not in object_statement
    assert "s3:PutObject" not in list_statement
    assert '"s3:GetBucketLocation"' in location_statement
    assert "data.aws_s3_bucket.historical_facts[0].arn" in location_statement
    assert "s3:prefix" not in location_statement

    metadata_prefixes = (
        "metadata/epoch/",
        "metadata/activation/",
        "metadata/deactivation/",
        "metadata/coverage/",
        "metadata/gaps/",
        "metadata/resolutions/",
        "metadata/incidents/",
    )
    metadata_block = locals_text.split(
        "query_coverage_metadata_prefixes"
    )[1].split("]")[0]
    datasets_block = locals_text.split("query_v1_datasets")[1].split("]")[0]
    list_block = locals_text.split("query_canonical_list_prefixes")[1].split(
        "query_canonical_object_prefixes"
    )[0]
    object_block = locals_text.split("query_canonical_object_prefixes")[1].split(
        "query_results_list_prefixes"
    )[0]
    assert datasets_block.count('"') == 6
    assert '"encounter"' in datasets_block
    assert '"risk"' in datasets_block
    assert '"hazard_version"' in datasets_block
    assert "hazard_geometry" not in datasets_block
    for prefix in metadata_prefixes:
        assert f'"{prefix}"' in metadata_block
        assert f'"{prefix}"' in store
        assert f'"{prefix}"' in contracts
    assert "dataset=${dataset}/" in list_block
    assert "dataset=${dataset}/*" in list_block
    assert "prefix," in list_block
    assert '"${prefix}*"' in list_block
    assert "dataset=${dataset}/*" in object_block
    assert '"${prefix}*"' in object_block
    assert "Prefix" in store
    assert "list_objects_v2" in store


def test_results_bucket_permissions_stay_under_athena_results():
    iam = read(MODULE_DIR / "iam.tf")
    objects = _statement(iam, "AthenaResultsObjectsAccess")
    listing = _statement(iam, "AthenaResultsBucketList")
    location = _statement(iam, "AthenaResultsBucketLocation")
    assert "aws_s3_bucket.athena_results[0].arn" in objects
    assert "aws_s3_bucket.athena_results[0].arn" in listing
    assert "aws_s3_bucket.athena_results[0].arn" in location
    assert "local.athena_results_prefix" in objects
    assert "local.query_results_list_prefixes" in listing
    assert '"s3:GetObject"' in objects
    assert '"s3:PutObject"' in objects
    assert '"s3:AbortMultipartUpload"' in objects
    assert '"s3:ListMultipartUploadParts"' in objects
    assert "DeleteObject" not in objects
    assert "DeleteBucket" not in iam
    assert "PutBucketPolicy" not in iam
    assert "data.aws_s3_bucket.historical_facts" not in objects
    assert "historical-facts-" not in objects


def test_policy_has_no_wildcards_or_escalation():
    iam = read(MODULE_DIR / "iam.tf")
    text = module_text()
    assert 'resources = ["*"]' not in iam
    assert 'resources = [\n      "*"\n    ]' not in iam
    assert re.search(r'actions = \[\s*"\*"|:\*"', iam) is None
    assert "iam:" not in iam
    assert "sts:" not in iam
    assert "kms:" not in iam
    assert "lambda:InvokeFunction" not in iam
    assert "cloudwatch:" not in iam
    assert "logs:" not in iam
    assert "events:" not in iam
    assert "aws_iam_role" not in text
    assert 'sse_algorithm = "AES256"' in read(MODULE_DIR / "results_bucket.tf")
    assert 'encryption_option = "SSE_S3"' in read(MODULE_DIR / "athena.tf")


def test_policy_is_disposable_and_not_on_operational_api_or_data_plane():
    api = read(REPO_ROOT / "modules" / "operational_api" / "api.tf")
    data_plane = read(DATA_PLANE_MAIN)
    dev_main = read(DEV_MAIN)
    assert "athena:StartQueryExecution" not in api
    assert "query_policy" not in api
    assert "historical_analytics" not in data_plane
    assert "aws_iam_role_policy_attachment" not in module_text()
    assert "aws_iam_policy_attachment" not in module_text()
    assert "query_policy_arn" not in dev_main
    assert 'output "historical_analytics_query_policy_arn"' in read(DEV_OUTPUTS)

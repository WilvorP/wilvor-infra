from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_analytics"
DATA_MODULE_DIR = REPO_ROOT / "modules" / "historical_facts_data"
TRANSPORT_DIR = REPO_ROOT / "modules" / "historical_facts"
DEV_MAIN = REPO_ROOT / "envs" / "dev" / "main.tf"
DEV_OUTPUTS = REPO_ROOT / "envs" / "dev" / "outputs.tf"
DEV_DOWN = REPO_ROOT / "scripts" / "dev-down.ps1"
DEV_RESET = REPO_ROOT / "scripts" / "dev-reset.ps1"
DATA_PLANE_MAIN = REPO_ROOT / "envs" / "dev-historical-data" / "main.tf"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def module_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_DIR.glob("*.tf"))
    )


def _dev_module_block(text: str, module_name: str) -> str:
    marker = f'module "{module_name}" {{'
    assert marker in text
    after = text.split(marker, 1)[1]
    next_module = after.find('\nmodule "')
    if next_module == -1:
        return after
    return after[:next_module]


def test_module_exists_and_is_disabled_by_default():
    assert MODULE_DIR.is_dir()
    assert (MODULE_DIR / "variables.tf").exists()
    assert (MODULE_DIR / "locals.tf").exists()
    assert (MODULE_DIR / "results_bucket.tf").exists()
    assert (MODULE_DIR / "outputs.tf").exists()
    assert (MODULE_DIR / "README.md").exists()
    assert not (MODULE_DIR / "glue.tf").exists()
    assert not (MODULE_DIR / "athena.tf").exists()
    assert not (MODULE_DIR / "monitoring.tf").exists()
    assert not (MODULE_DIR / "iam.tf").exists()
    variables = read(MODULE_DIR / "variables.tf")
    assert "variable \"enable_historical_analytics\"" in variables
    assert "default     = false" in variables
    assert "default = false" in variables or "default     = false" in variables


def test_dev_wires_analytics_disabled_and_collection_enabled():
    text = read(DEV_MAIN)
    analytics = _dev_module_block(text, "historical_analytics")
    historical_facts = _dev_module_block(text, "historical_facts")
    operational_api = _dev_module_block(text, "operational_api")

    assert 'source = "../../modules/historical_analytics"' in analytics
    assert "enable_historical_analytics = false" in analytics
    assert "enable_historical_analytics = true" not in analytics
    assert "enable_historical_facts = true" in historical_facts
    assert "enable_historical_facts = false" not in historical_facts
    assert "enable_historical_facts = true" in operational_api
    assert "enable_historical_facts = false" not in text


def test_dev_outputs_are_safe_when_disabled():
    outputs = read(DEV_OUTPUTS)
    assert 'output "enable_historical_analytics"' in outputs
    assert "module.historical_analytics.enable_historical_analytics" in outputs
    assert 'output "historical_analytics_results_bucket_name"' in outputs
    assert "module.historical_analytics.results_bucket_name" in outputs
    assert "athena_workgroup" not in outputs
    assert "query_role" not in outputs
    assert "glue_database" not in outputs


def test_result_bucket_naming_is_separate_from_canonical_facts():
    locals_text = read(MODULE_DIR / "locals.tf")
    assert (
        'historical_bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert (
        'results_bucket_name    = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"'
        in locals_text
        or 'results_bucket_name = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert "historical-athena-results" in locals_text
    assert local_names_differ(locals_text)


def local_names_differ(locals_text: str) -> bool:
    return (
        "historical-facts-" in locals_text
        and "historical-athena-results-" in locals_text
        and locals_text.count("historical-facts-") >= 1
    )


def test_results_bucket_is_disposable_and_hardened():
    bucket = read(MODULE_DIR / "results_bucket.tf")
    variables = read(MODULE_DIR / "variables.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    assert "count = local.enabled ? 1 : 0" in bucket
    assert "force_destroy = true" in bucket
    assert "force_destroy = false" not in bucket
    assert 'sse_algorithm = "AES256"' in bucket
    assert "block_public_acls       = true" in bucket
    assert "block_public_policy     = true" in bucket
    assert "ignore_public_acls      = true" in bucket
    assert "restrict_public_buckets = true" in bucket
    assert 'object_ownership = "BucketOwnerEnforced"' in bucket
    assert "aws_s3_bucket_versioning" not in bucket
    assert "versioning_configuration" not in bucket
    assert "days = var.result_retention_days" in bucket
    assert "default     = 3" in variables
    assert "result_retention_days must be greater than 0" in variables
    assert "days_after_initiation = 1" in bucket
    assert 'DataType  = "historical-analytics-derived"' in locals_text
    assert 'DataType  = "historical-facts"' not in locals_text
    assert 'Component = "historical-analytics"' in locals_text


def test_module_does_not_own_the_persistent_historical_bucket():
    text = module_text()
    results = read(MODULE_DIR / "results_bucket.tf")
    assert 'resource "aws_s3_bucket" "historical_facts"' not in text
    assert 'resource "aws_s3_bucket" "athena_results"' in results
    assert "aws_s3_bucket_public_access_block" in results
    assert "aws_s3_bucket_ownership_controls" in results
    assert "aws_s3_bucket_server_side_encryption_configuration" in results
    assert "aws_s3_bucket_lifecycle_configuration" in results
    assert 'data "aws_s3_bucket" "historical_facts"' in read(
        MODULE_DIR / "locals.tf"
    )
    assert "count = local.enabled ? 1 : 0" in read(MODULE_DIR / "locals.tf")
    data_plane = read(DATA_MODULE_DIR / "bucket.tf")
    assert "force_destroy = false" in data_plane
    assert "force_destroy = true" not in data_plane


def test_historical_bucket_lookup_is_gated():
    locals_text = read(MODULE_DIR / "locals.tf")
    assert 'data "aws_s3_bucket" "historical_facts"' in locals_text
    lookup = locals_text.split('data "aws_s3_bucket" "historical_facts"')[1]
    assert "count = local.enabled ? 1 : 0" in lookup
    assert "local.historical_bucket_name" in lookup
    assert "terraform_remote_state" not in module_text()


def test_no_glue_athena_query_role_or_sql_tool():
    text = module_text().lower()
    assert "aws_glue_catalog_database" not in text
    assert "aws_glue_catalog_table" not in text
    assert "aws_athena_workgroup" not in text
    assert "aws_glue_crawler" not in text
    assert "aws_iam_role" not in text
    assert "aws_dynamodb" not in text
    assert "execute_sql" not in text
    assert "run_query" not in text
    assert "query_athena" not in text
    transport = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TRANSPORT_DIR.glob("*.tf")
    ).lower()
    assert "aws_glue" not in transport
    assert "athena" not in transport


def test_data_plane_root_is_unchanged():
    main = read(DATA_PLANE_MAIN)
    assert 'source = "../../modules/historical_facts_data"' in main
    assert "historical_analytics" not in main
    assert "enable_historical_analytics" not in main


def test_dev_down_still_protects_data_plane():
    down = read(DEV_DOWN)
    reset = read(DEV_RESET)
    assert "must not target the persistent historical data plane" in down
    assert "DEACTIVATE" in down
    assert "dev-historical-data" in reset
    assert not (REPO_ROOT / "scripts" / "historical-data-down.ps1").exists()


def test_readme_documents_ownership_and_lifecycle():
    readme = read(MODULE_DIR / "README.md")
    for needle in (
        "envs/dev-historical-data",
        "force_destroy = false",
        "force_destroy = true",
        "3-day",
        "dev-down",
        "DEACTIVATE",
        "does **not** create Glue",
        "historical-data-down.ps1",
        "enable_historical_analytics",
    ):
        assert needle in readme

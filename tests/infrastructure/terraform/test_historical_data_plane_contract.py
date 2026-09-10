from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_facts_data"
TRANSPORT_DIR = REPO_ROOT / "modules" / "historical_facts"
ENV_DIR = REPO_ROOT / "envs" / "dev-historical-data"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_data_plane_exclusively_owns_bucket_configuration():
    bucket = read(MODULE_DIR / "bucket.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert (
        'bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert "force_destroy = false" in bucket
    assert "block_public_acls       = true" in bucket
    assert "block_public_policy     = true" in bucket
    assert "ignore_public_acls      = true" in bucket
    assert "restrict_public_buckets = true" in bucket
    assert 'object_ownership = "BucketOwnerEnforced"' in bucket
    assert 'sse_algorithm = "AES256"' in bucket
    assert 'status = "Enabled"' in bucket
    assert 'prefix = "dataset="' in bucket
    assert 'prefix = "errors/"' in bucket
    assert 'prefix = "metadata/"' in bucket
    assert "days = var.historical_fact_retention_days" in bucket
    assert "days = var.historical_fact_error_retention_days" in bucket
    assert "days = var.historical_fact_metadata_retention_days" in bucket
    assert (
        "noncurrent_days = var.historical_fact_noncurrent_version_retention_days"
        in bucket
    )
    assert "days_after_initiation = 1" in bucket
    assert "default = 365" in variables
    assert "historical_fact_metadata_retention_days must be greater than 0" in variables
    assert "var.historical_fact_metadata_retention_days" in bucket
    assert (
        "var.historical_fact_metadata_retention_days"
        in read(MODULE_DIR / "bucket.tf")
    )
    assert (
        ">= var.historical_fact_retention_days" in bucket
    )
    assert 'check "metadata_retention_covers_facts"' in bucket
    assert "precondition" in bucket
    assert "enable_historical_facts" not in variables


def test_metadata_retention_cannot_be_shorter_than_fact_retention():
    bucket = read(MODULE_DIR / "bucket.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert "historical_fact_metadata_retention_days must be >= historical_fact_retention_days" in bucket
    assert "var.historical_fact_metadata_retention_days" in bucket
    assert ">= var.historical_fact_retention_days" in bucket
    assert 'check "metadata_retention_covers_facts"' in bucket
    assert "lifecycle {" in bucket
    assert "precondition" in bucket
    assert ">= 365" not in variables
    assert "default = 365" in variables
    assert "aws_s3_bucket_lifecycle_configuration" in bucket
    transport = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TRANSPORT_DIR.glob("*.tf")
    )
    assert "aws_s3_bucket_lifecycle_configuration" not in transport


def test_transport_module_has_no_bucket_lifecycle_resources():
    transport = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TRANSPORT_DIR.glob("*.tf")
    )
    assert 'resource "aws_s3_bucket"' not in transport
    assert "aws_s3_bucket_lifecycle_configuration" not in transport
    assert "aws_s3_bucket_versioning" not in transport


def test_data_plane_root_is_separate_and_ungated():
    main = read(ENV_DIR / "main.tf")
    assert 'source = "../../modules/historical_facts_data"' in main
    assert "enable_historical_facts" not in main
    assert (ENV_DIR / "providers.tf").exists()
    assert (ENV_DIR / "terraform.tfvars").exists()


def test_no_historical_data_down_script():
    assert not (REPO_ROOT / "scripts" / "historical-data-down.ps1").exists()
    assert (REPO_ROOT / "scripts" / "historical-data-up.ps1").exists()

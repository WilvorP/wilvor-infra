from __future__ import annotations

from typing import Any

import pytest


pytestmark = pytest.mark.infrastructure


STREAM_OUTPUTS = [
    "aircraft_raw_stream_name",
    "aircraft_clean_stream_name",
    "sigmet_raw_stream_name",
    "metar_raw_stream_name",
    "taf_raw_stream_name",
]

TABLE_OUTPUTS = [
    "aircraft_current_state_table_name",
    "active_hazards_table_name",
    "hazard_cells_table_name",
    "metar_latest_table_name",
    "taf_latest_table_name",
]

BUCKET_OUTPUTS = [
    "aircraft_archive_bucket_name",
    "sigmet_archive_bucket_name",
    "metar_archive_bucket_name",
    "taf_archive_bucket_name",
]


@pytest.mark.parametrize("output_name", STREAM_OUTPUTS)
def test_kinesis_stream_is_active(
    output_name,
    terraform_outputs,
    aws_client,
):
    stream_name = terraform_outputs[output_name]
    response = aws_client("kinesis").describe_stream_summary(
        StreamName=stream_name
    )
    summary = response["StreamDescriptionSummary"]

    assert summary["StreamName"] == stream_name
    assert summary["StreamStatus"] == "ACTIVE"
    assert summary["OpenShardCount"] >= 1


@pytest.mark.parametrize("output_name", TABLE_OUTPUTS)
def test_dynamodb_table_is_active(
    output_name,
    terraform_outputs,
    aws_client,
):
    table_name = terraform_outputs[output_name]
    table = aws_client("dynamodb").describe_table(
        TableName=table_name
    )["Table"]

    assert table["TableName"] == table_name
    assert table["TableStatus"] == "ACTIVE"
    assert table["KeySchema"]


@pytest.mark.parametrize("output_name", BUCKET_OUTPUTS)
def test_archive_bucket_exists_and_blocks_public_access(
    output_name,
    terraform_outputs,
    aws_client,
):
    bucket_name = terraform_outputs[output_name]
    s3 = aws_client("s3")

    s3.head_bucket(Bucket=bucket_name)

    public_access = s3.get_public_access_block(
        Bucket=bucket_name
    )["PublicAccessBlockConfiguration"]

    assert all(
        public_access[key] is True
        for key in (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
    )


@pytest.mark.parametrize("output_name", BUCKET_OUTPUTS)
def test_archive_bucket_has_server_side_encryption(
    output_name,
    terraform_outputs,
    aws_client,
):
    bucket_name = terraform_outputs[output_name]
    response = aws_client("s3").get_bucket_encryption(
        Bucket=bucket_name
    )

    rules = response[
        "ServerSideEncryptionConfiguration"
    ]["Rules"]

    algorithms = {
        rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        for rule in rules
    }

    assert algorithms & {"AES256", "aws:kms"}

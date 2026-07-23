from __future__ import annotations

import pytest


pytestmark = pytest.mark.infrastructure


def test_opensky_secret_exists_without_reading_its_value(
    terraform_outputs,
    aws_client,
):
    secret_name = terraform_outputs[
        "opensky_credentials_secret_name"
    ]
    expected_arn = terraform_outputs[
        "opensky_credentials_secret_arn"
    ]

    metadata = aws_client("secretsmanager").describe_secret(
        SecretId=secret_name
    )

    assert metadata["Name"] == secret_name
    assert metadata["ARN"] == expected_arn

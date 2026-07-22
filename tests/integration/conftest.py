"""Shared deployed-environment fixtures for integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import boto3
import pytest
from botocore.exceptions import ProfileNotFound


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS_DIR = REPO_ROOT / "tests" / "helpers"
sys.path.insert(0, str(HELPERS_DIR))

from integration_support import CleanupRegistry  # noqa: E402


TERRAFORM_DIR = REPO_ROOT / os.getenv(
    "WILVOR_TERRAFORM_DIR",
    "envs/dev",
)


def _terraform_outputs() -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=TERRAFORM_DIR,
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(
            "Unable to read Terraform outputs from envs/dev.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    raw = json.loads(result.stdout)
    return {
        name: metadata.get("value")
        for name, metadata in raw.items()
    }


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def terraform_outputs() -> dict[str, Any]:
    outputs = _terraform_outputs()

    assert outputs.get("environment") == "dev", (
        "Integration tests may only target environment=dev."
    )
    assert str(outputs.get("name_prefix", "")).endswith("-dev"), (
        "Integration tests may only target a dev name prefix."
    )

    return outputs


@pytest.fixture(scope="session")
def integration_timeout() -> float:
    return float(
        os.getenv("WILVOR_INTEGRATION_TIMEOUT_SECONDS", "120")
    )


@pytest.fixture(scope="session")
def integration_interval() -> float:
    return float(
        os.getenv("WILVOR_INTEGRATION_POLL_SECONDS", "2")
    )


@pytest.fixture(scope="session")
def aws_profile() -> str | None:
    return (
        os.getenv("WILVOR_AWS_PROFILE")
        or os.getenv("AWS_PROFILE")
    )


@pytest.fixture(scope="session")
def aws_region(terraform_outputs) -> str:
    return (
        os.getenv("WILVOR_AWS_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or terraform_outputs.get("aws_region")
        or "us-west-1"
    )


@pytest.fixture(scope="session")
def aws_session(
    aws_profile: str | None,
    aws_region: str,
) -> boto3.Session:
    try:
        return boto3.Session(
            profile_name=aws_profile,
            region_name=aws_region,
        )
    except ProfileNotFound as error:
        pytest.fail(str(error))


@pytest.fixture(scope="session")
def aws_client(aws_session):
    clients: dict[str, Any] = {}

    def get_client(service_name: str):
        if service_name not in clients:
            clients[service_name] = aws_session.client(service_name)
        return clients[service_name]

    return get_client


@pytest.fixture(scope="session")
def aws_resource(aws_session):
    resources: dict[str, Any] = {}

    def get_resource(service_name: str):
        if service_name not in resources:
            resources[service_name] = aws_session.resource(service_name)
        return resources[service_name]

    return get_resource


@pytest.fixture(scope="session", autouse=True)
def verify_dev_identity(terraform_outputs, aws_client):
    identity = aws_client("sts").get_caller_identity()
    account_id = identity.get("Account")
    assert account_id, "AWS caller identity is unavailable."

    reference_arn = (
        terraform_outputs.get("sigmet_processor_lambda_arn")
        or terraform_outputs.get("metar_processor_lambda_arn")
        or terraform_outputs.get("taf_processor_lambda_arn")
    )

    if isinstance(reference_arn, str) and reference_arn.startswith("arn:"):
        expected_account = reference_arn.split(":")[4]
        assert account_id == expected_account, (
            "The authenticated AWS profile does not match the account "
            "recorded by the dev Terraform outputs."
        )

    return identity


@pytest.fixture
def cleanup_registry() -> CleanupRegistry:
    registry = CleanupRegistry(
        keep_artifacts=(
            os.getenv(
                "WILVOR_KEEP_INTEGRATION_ARTIFACTS",
                "",
            ).strip().lower()
            in {"1", "true", "yes"}
        )
    )

    yield registry
    registry.run()


@pytest.fixture(scope="session")
def lambda_environment(
    aws_client,
    terraform_outputs,
) -> Callable[[str], dict[str, str]]:
    cache: dict[str, dict[str, str]] = {}

    def read(output_name: str) -> dict[str, str]:
        function_name = terraform_outputs[output_name]

        if function_name not in cache:
            configuration = aws_client(
                "lambda"
            ).get_function_configuration(
                FunctionName=function_name
            )
            cache[function_name] = configuration.get(
                "Environment",
                {},
            ).get("Variables", {})

        return cache[function_name]

    return read

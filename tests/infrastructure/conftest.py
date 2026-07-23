"""Shared fixtures for Wilvor infrastructure tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound


REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIR = REPO_ROOT / os.getenv(
    "WILVOR_TERRAFORM_DIR",
    "envs/dev",
)


def run_command(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a local command and return text output."""

    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )

    if check and result.returncode != 0:
        rendered = " ".join(command)
        raise AssertionError(
            f"Command failed ({result.returncode}): {rendered}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    return result


def unwrap_terraform_outputs(
    raw_outputs: dict[str, Any],
) -> dict[str, Any]:
    """Convert Terraform's output metadata objects into plain values."""

    return {
        name: metadata.get("value")
        for name, metadata in raw_outputs.items()
    }


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def terraform_dir() -> Path:
    assert TERRAFORM_DIR.is_dir(), (
        f"Terraform directory does not exist: {TERRAFORM_DIR}"
    )
    return TERRAFORM_DIR


@pytest.fixture(scope="session")
def terraform_binary() -> str:
    binary = shutil.which("terraform")
    assert binary, "Terraform is not installed or is not on PATH."
    return binary


@pytest.fixture(scope="session")
def terraform_outputs(
    terraform_binary: str,
    terraform_dir: Path,
) -> dict[str, Any]:
    result = run_command(
        [terraform_binary, "output", "-json"],
        cwd=terraform_dir,
        check=False,
    )

    if result.returncode != 0:
        pytest.fail(
            "Unable to read Terraform outputs. Deploy the dev environment "
            "and run these tests from the repository containing its state.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    try:
        raw_outputs = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        pytest.fail(f"Terraform output was not valid JSON: {error}")

    outputs = unwrap_terraform_outputs(raw_outputs)

    if not outputs:
        pytest.fail("Terraform returned no outputs.")

    return outputs


@pytest.fixture(scope="session")
def aws_profile() -> str | None:
    return os.getenv("WILVOR_AWS_PROFILE") or os.getenv("AWS_PROFILE")


@pytest.fixture(scope="session")
def aws_region(terraform_outputs: dict[str, Any]) -> str:
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
def aws_client(aws_session: boto3.Session):
    clients: dict[str, Any] = {}

    def _client(service_name: str):
        if service_name not in clients:
            clients[service_name] = aws_session.client(service_name)
        return clients[service_name]

    return _client


@pytest.fixture(scope="session")
def caller_identity(aws_client) -> dict[str, Any]:
    try:
        return aws_client("sts").get_caller_identity()
    except (BotoCoreError, ClientError) as error:
        pytest.fail(
            "AWS authentication failed. Authenticate the wilvor-dev "
            f"profile before running deployed-AWS tests: {error}"
        )


@pytest.fixture(scope="session")
def name_prefix(terraform_outputs: dict[str, Any]) -> str:
    value = terraform_outputs.get("name_prefix")
    assert isinstance(value, str) and value, (
        "Terraform output 'name_prefix' is missing."
    )
    return value

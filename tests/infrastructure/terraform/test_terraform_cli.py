from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.infrastructure.conftest import run_command


pytestmark = pytest.mark.infrastructure


def test_terraform_formatting(
    terraform_binary,
    repo_root: Path,
):
    result = run_command(
        [terraform_binary, "fmt", "-recursive", "-check"],
        cwd=repo_root,
        check=False,
    )

    assert result.returncode == 0, (
        "Terraform files are not formatted. Run:\n"
        "terraform fmt -recursive\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_terraform_configuration_validates(
    terraform_binary,
    terraform_dir: Path,
):
    result = run_command(
        [terraform_binary, "validate", "-json"],
        cwd=terraform_dir,
        check=False,
    )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "terraform validate did not return JSON.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    diagnostics = payload.get("diagnostics", [])
    errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.get("severity") == "error"
    ]

    assert payload.get("valid") is True and not errors, (
        "Terraform validation failed:\n"
        + json.dumps(errors, indent=2)
    )


def test_terraform_version_meets_root_constraint(terraform_binary):
    result = run_command(
        [terraform_binary, "version", "-json"],
    )
    payload = json.loads(result.stdout)
    version = payload["terraform_version"]

    numbers = tuple(
        int(part)
        for part in re.match(r"^(\d+)\.(\d+)\.(\d+)", version).groups()
    )

    assert numbers >= (1, 6, 0), (
        f"Terraform {version} is older than the required >= 1.6.0."
    )

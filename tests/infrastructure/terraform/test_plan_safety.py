from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.infrastructure.conftest import run_command


pytestmark = pytest.mark.infrastructure


@pytest.fixture(scope="module")
def plan_json(terraform_binary, terraform_dir: Path):
    configured_path = os.getenv("WILVOR_TFPLAN_PATH")

    if not configured_path:
        pytest.skip(
            "No plan supplied. Pass -PlanFile to run plan-safety tests."
        )

    plan_path = Path(configured_path)

    if not plan_path.is_absolute():
        plan_path = (terraform_dir / plan_path).resolve()

    assert plan_path.is_file(), f"Terraform plan not found: {plan_path}"

    result = run_command(
        [terraform_binary, "show", "-json", str(plan_path)],
        cwd=terraform_dir,
    )
    return json.loads(result.stdout)


def test_plan_contains_no_unapproved_destroy_actions(plan_json):
    allow_destroy = (
        os.getenv("WILVOR_ALLOW_DESTROY", "").strip().lower()
        in {"1", "true", "yes"}
    )

    destructive_changes: list[str] = []

    for change in plan_json.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])

        if "delete" in actions:
            destructive_changes.append(
                f"{change.get('address')}: {actions}"
            )

    if allow_destroy:
        return

    assert not destructive_changes, (
        "Terraform plan contains delete or replacement actions. "
        "Review them or explicitly set WILVOR_ALLOW_DESTROY=true:\n"
        + "\n".join(destructive_changes)
    )


def test_plan_targets_only_dev_named_resources(plan_json):
    suspicious: list[str] = []

    for change in plan_json.get("resource_changes", []):
        after = change.get("change", {}).get("after")

        if not isinstance(after, dict):
            continue

        for field in (
            "name",
            "function_name",
            "bucket",
            "stream_name",
            "table_name",
            "rule",
        ):
            value = after.get(field)

            if (
                isinstance(value, str)
                and value.startswith("wilvor-")
                and "wilvor-dev" not in value
            ):
                suspicious.append(
                    f"{change.get('address')} {field}={value}"
                )

    assert not suspicious, (
        "Plan contains Wilvor resources outside the dev prefix:\n"
        + "\n".join(suspicious)
    )

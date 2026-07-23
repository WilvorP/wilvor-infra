from __future__ import annotations

import re

import pytest


pytestmark = pytest.mark.infrastructure


def test_tests_are_targeting_dev_environment(terraform_outputs):
    assert terraform_outputs["environment"] == "dev"
    assert terraform_outputs["name_prefix"].endswith("-dev")


def test_terraform_region_matches_test_region(
    terraform_outputs,
    aws_region,
):
    assert terraform_outputs["aws_region"] == aws_region


def test_authenticated_aws_identity_is_valid(caller_identity):
    assert re.fullmatch(r"\d{12}", caller_identity["Account"])
    assert caller_identity["Arn"].startswith("arn:aws:")

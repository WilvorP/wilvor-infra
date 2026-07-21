from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hcl2
import pytest


pytestmark = pytest.mark.infrastructure


EXPECTED_MODULES = {
    "aircraft_foundation": "../../modules/aircraft_foundation",
    "weather_events": "../../modules/weather_events",
    "sigmet": "../../modules/sigmet",
    "metar": "../../modules/metar",
    "taf": "../../modules/taf",
}

EXPECTED_OUTPUTS = {
    "environment",
    "aws_region",
    "name_prefix",
    "aircraft_raw_stream_name",
    "aircraft_clean_stream_name",
    "aircraft_archive_bucket_name",
    "aircraft_current_state_table_name",
    "opensky_poller_lambda_name",
    "opensky_poller_schedule_name",
    "opensky_poller_schedule_state",
    "opensky_credentials_secret_name",
    "opensky_credentials_secret_arn",
    "aircraft_raw_processor_lambda_name",
    "aircraft_raw_processor_lambda_arn",
    "aircraft_current_state_writer_lambda_name",
    "aircraft_current_state_writer_lambda_arn",
    "sigmet_raw_stream_name",
    "active_hazards_table_name",
    "hazard_cells_table_name",
    "sigmet_poller_function_name",
    "sigmet_processor_lambda_name",
    "sigmet_processor_lambda_arn",
    "sigmet_archive_bucket_name",
    "sigmet_poller_schedule_name",
    "sigmet_poller_schedule_state",
    "sigmet_dashboard_name",
    "metar_archive_bucket_name",
    "metar_raw_stream_name",
    "metar_latest_table_name",
    "metar_poller_function_name",
    "metar_processor_lambda_name",
    "metar_processor_lambda_arn",
    "metar_processor_event_source_mapping_uuid",
    "metar_poller_schedule_name",
    "metar_poller_schedule_state",
    "metar_dashboard_name",
    "taf_archive_bucket_name",
    "taf_raw_stream_name",
    "taf_raw_stream_arn",
    "taf_latest_table_name",
    "taf_poller_function_name",
    "taf_processor_lambda_name",
    "taf_processor_lambda_arn",
    "taf_poller_schedule_name",
    "taf_poller_schedule_state",
    "taf_dashboard_name",
    "weather_changed_log_group_name",
    "weather_events_dashboard_name",
}

EXPECTED_VARIABLES = {
    "aws_region",
    "aws_profile",
    "project_name",
    "environment",
}


def normalize_hcl(value: Any) -> Any:
    """Normalize python-hcl2 output across supported parser versions.

    Some python-hcl2 releases preserve quotes around block labels and
    string literals, while newer releases return the unquoted values.
    Infrastructure assertions should not depend on that parser detail.
    """

    if isinstance(value, str):
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            return value[1:-1]

        return value

    if isinstance(value, list):
        return [normalize_hcl(item) for item in value]

    if isinstance(value, dict):
        return {
            normalize_hcl(key): normalize_hcl(item)
            for key, item in value.items()
        }

    return value


def load_hcl(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return normalize_hcl(hcl2.load(handle))


def named_blocks(
    configuration: dict[str, Any],
    block_type: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for block in configuration.get(block_type, []):
        result.update(block)

    return result


@pytest.fixture(scope="module")
def root_main(terraform_dir: Path):
    return load_hcl(terraform_dir / "main.tf")


@pytest.fixture(scope="module")
def root_outputs(terraform_dir: Path):
    return load_hcl(terraform_dir / "outputs.tf")


@pytest.fixture(scope="module")
def root_variables(terraform_dir: Path):
    return load_hcl(terraform_dir / "variables.tf")


@pytest.fixture(scope="module")
def root_providers(terraform_dir: Path):
    return load_hcl(terraform_dir / "providers.tf")


def test_expected_root_modules_are_declared(root_main):
    modules = named_blocks(root_main, "module")

    assert set(modules) == set(EXPECTED_MODULES)

    for module_name, expected_source in EXPECTED_MODULES.items():
        assert modules[module_name]["source"] == expected_source


def test_module_source_directories_exist(
    root_main,
    terraform_dir: Path,
):
    modules = named_blocks(root_main, "module")

    for module_name, module in modules.items():
        source = module["source"]
        source_path = (terraform_dir / source).resolve()

        assert source_path.is_dir(), (
            f"Module {module_name!r} points to missing directory "
            f"{source_path}"
        )


@pytest.mark.parametrize(
    ("module_name", "switch_name"),
    [
        ("aircraft_foundation", "enable_opensky_poller_schedule"),
        ("sigmet", "enable_sigmet_poller_schedule"),
        ("metar", "enable_metar_poller_schedule"),
        ("taf", "enable_taf_poller_schedule"),
    ],
)
def test_dev_poller_schedules_are_disabled(
    root_main,
    module_name,
    switch_name,
):
    modules = named_blocks(root_main, "module")
    assert modules[module_name][switch_name] is False


def test_expected_root_outputs_are_declared(root_outputs):
    outputs = named_blocks(root_outputs, "output")
    missing = sorted(EXPECTED_OUTPUTS - set(outputs))

    assert not missing, f"Missing Terraform outputs: {missing}"


def test_expected_root_variables_are_declared(root_variables):
    variables = named_blocks(root_variables, "variable")
    assert set(variables) == EXPECTED_VARIABLES

    for variable_name, definition in variables.items():
        assert definition.get("type") == "string", (
            f"Root variable {variable_name!r} should be a string."
        )


def test_required_terraform_and_aws_provider_constraints(
    root_providers,
):
    terraform_blocks = root_providers.get("terraform", [])
    assert len(terraform_blocks) == 1

    terraform_block = terraform_blocks[0]
    assert terraform_block["required_version"] == ">= 1.6.0"

    required_providers = terraform_block["required_providers"][0]
    aws_provider = required_providers["aws"]

    assert aws_provider["source"] == "hashicorp/aws"
    assert aws_provider["version"] == "~> 5.0"


def test_aws_provider_has_required_default_tags(root_providers):
    providers = named_blocks(root_providers, "provider")
    aws_provider = providers["aws"]
    default_tags = aws_provider["default_tags"][0]["tags"]

    assert default_tags["Project"] == "${var.project_name}"
    assert default_tags["Environment"] == "${var.environment}"
    assert default_tags["ManagedBy"] == "Terraform"


def test_root_module_uses_default_event_bus(root_main):
    modules = named_blocks(root_main, "module")

    for module_name in ("sigmet", "taf"):
        module = modules[module_name]
        assert module["event_bus_name"] == (
            "${local.default_event_bus_name}"
        )
        assert module["event_bus_arn"] == (
            "${local.default_event_bus_arn}"
        )


def test_terraform_does_not_contain_hardcoded_credentials(
    repo_root: Path,
):
    forbidden_patterns = {
        "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "AWS secret assignment": re.compile(
            r"(?i)(aws_secret_access_key|secret_access_key)\s*="
        ),
        "private key": re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY"),
    }

    violations: list[str] = []

    for path in repo_root.rglob("*.tf"):
        if ".terraform" in path.parts:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")

        for description, pattern in forbidden_patterns.items():
            if pattern.search(text):
                violations.append(
                    f"{path.relative_to(repo_root)}: {description}"
                )

    assert not violations, (
        "Potential credentials were found in Terraform files:\n"
        + "\n".join(violations)
    )

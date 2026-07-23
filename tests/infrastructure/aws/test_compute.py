from __future__ import annotations

from dataclasses import dataclass

import pytest


pytestmark = pytest.mark.infrastructure


@dataclass(frozen=True)
class LambdaContract:
    output_name: str
    required_environment_keys: frozenset[str]


LAMBDA_CONTRACTS = [
    LambdaContract(
        "opensky_poller_lambda_name",
        frozenset(
            {
                "OPENSKY_SECRET_ARN",
                "AIRCRAFT_ARCHIVE_BUCKET",
                "AIRCRAFT_RAW_STREAM_NAME",
            }
        ),
    ),
    LambdaContract(
        "aircraft_raw_processor_lambda_name",
        frozenset(
            {
                "AIRCRAFT_ARCHIVE_BUCKET",
                "AIRCRAFT_CLEAN_STREAM_NAME",
            }
        ),
    ),
    LambdaContract(
        "aircraft_current_state_writer_lambda_name",
        frozenset({"AIRCRAFT_CURRENT_STATE_TABLE_NAME"}),
    ),
    LambdaContract(
        "sigmet_poller_function_name",
        frozenset(
            {
                "NOAA_SIGMET_URL",
                "ARCHIVE_BUCKET_NAME",
                "SIGMET_RAW_STREAM_NAME",
            }
        ),
    ),
    LambdaContract(
        "sigmet_processor_lambda_name",
        frozenset(
            {
                "ACTIVE_HAZARDS_TABLE_NAME",
                "HAZARD_CELLS_TABLE_NAME",
                "EVENT_BUS_NAME",
            }
        ),
    ),
    LambdaContract(
        "metar_poller_function_name",
        frozenset(
            {
                "NOAA_METAR_URL",
                "ARCHIVE_BUCKET_NAME",
                "METAR_RAW_STREAM_NAME",
            }
        ),
    ),
    LambdaContract(
        "metar_processor_lambda_name",
        frozenset(
            {
                "METAR_LATEST_TABLE_NAME",
                "EVENT_BUS_NAME",
            }
        ),
    ),
    LambdaContract(
        "taf_poller_function_name",
        frozenset(
            {
                "NOAA_TAF_URL",
                "ARCHIVE_BUCKET_NAME",
                "TAF_RAW_STREAM_NAME",
            }
        ),
    ),
    LambdaContract(
        "taf_processor_lambda_name",
        frozenset(
            {
                "TAF_LATEST_TABLE_NAME",
                "EVENT_BUS_NAME",
            }
        ),
    ),
]

EVENT_CONSUMER_OUTPUTS = [
    "aircraft_raw_processor_lambda_name",
    "aircraft_current_state_writer_lambda_name",
    "sigmet_processor_lambda_name",
    "metar_processor_lambda_name",
    "taf_processor_lambda_name",
]


@pytest.mark.parametrize(
    "contract",
    LAMBDA_CONTRACTS,
    ids=lambda contract: contract.output_name,
)
def test_lambda_configuration_matches_contract(
    contract,
    terraform_outputs,
    aws_client,
):
    function_name = terraform_outputs[contract.output_name]
    configuration = aws_client("lambda").get_function_configuration(
        FunctionName=function_name
    )

    assert configuration["FunctionName"] == function_name
    assert configuration["State"] == "Active"
    assert configuration["PackageType"] == "Zip"
    assert configuration["Runtime"].startswith("python")
    assert configuration["Timeout"] > 0
    assert configuration["MemorySize"] >= 128
    assert configuration["Role"].startswith("arn:aws:iam::")

    variables = configuration.get("Environment", {}).get(
        "Variables",
        {},
    )
    missing = contract.required_environment_keys - set(variables)

    assert not missing, (
        f"{function_name} is missing environment variables: "
        f"{sorted(missing)}"
    )


@pytest.mark.parametrize("output_name", EVENT_CONSUMER_OUTPUTS)
def test_event_consumer_has_enabled_mapping(
    output_name,
    terraform_outputs,
    aws_client,
):
    function_name = terraform_outputs[output_name]
    mappings = aws_client("lambda").list_event_source_mappings(
        FunctionName=function_name
    )["EventSourceMappings"]

    assert mappings, (
        f"{function_name} has no Lambda event-source mappings."
    )

    assert any(
        mapping["State"] in {"Enabled", "Enabling"}
        for mapping in mappings
    ), f"{function_name} has no enabled event-source mapping."


@pytest.mark.parametrize(
    "contract",
    LAMBDA_CONTRACTS,
    ids=lambda contract: contract.output_name,
)
def test_lambda_execution_role_exists(
    contract,
    terraform_outputs,
    aws_client,
):
    function_name = terraform_outputs[contract.output_name]
    configuration = aws_client("lambda").get_function_configuration(
        FunctionName=function_name
    )
    role_name = configuration["Role"].rsplit("/", 1)[-1]

    role = aws_client("iam").get_role(RoleName=role_name)["Role"]
    assert role["RoleName"] == role_name

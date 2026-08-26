import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3


REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ENV_DIR = REPO_ROOT / "envs" / "dev"
SHARED_CODE_DIR = REPO_ROOT / "functions" / "shared"

sys.path.insert(0, str(SHARED_CODE_DIR))

METRIC_NAMESPACE = "Wilvor/Pipeline"


def terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=DEV_ENV_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def configure_lambda_like_environment() -> None:
    aws_region = terraform_output("aws_region")

    os.environ.setdefault("AWS_PROFILE", "wilvor-dev")
    os.environ.setdefault("AWS_REGION", aws_region)
    os.environ.setdefault("AWS_DEFAULT_REGION", aws_region)

    os.environ["AIRCRAFT_RAW_STREAM_NAME"] = terraform_output("aircraft_raw_stream_name")
    os.environ["AIRCRAFT_ARCHIVE_BUCKET"] = terraform_output("aircraft_archive_bucket_name")

    # Use secret name for local restore workflow.
    # AWS Secrets Manager accepts the secret name as SecretId.
    os.environ["OPENSKY_SECRET_ARN"] = terraform_output("opensky_credentials_secret_name")

    os.environ["OPENSKY_TOKEN_URL"] = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/"
        "protocol/openid-connect/token"
    )
    os.environ["OPENSKY_STATES_URL"] = "https://opensky-network.org/api/states/all"

    # Same test box currently configured in Terraform for the Lambda.
    os.environ["OPENSKY_LAMIN"] = "24.0"
    os.environ["OPENSKY_LOMIN"] = "-125.0"
    os.environ["OPENSKY_LAMAX"] = "50.0"
    os.environ["OPENSKY_LOMAX"] = "-66.0"

    os.environ["ENVIRONMENT"] = "dev"
    os.environ["MODE"] = "local-opensky-poller"


def configure_aws_for_metrics_safely() -> None:
    os.environ.setdefault("AWS_PROFILE", "wilvor-dev")
    os.environ.setdefault("AWS_REGION", "us-west-1")
    os.environ.setdefault("AWS_DEFAULT_REGION", os.environ["AWS_REGION"])
    os.environ.setdefault("ENVIRONMENT", "dev")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2), flush=True)


def publish_local_poller_metrics(result: dict[str, Any]) -> None:
    configure_aws_for_metrics_safely()

    environment = os.environ.get("ENVIRONMENT", "dev")

    poll_success = 1 if result.get("ok") else 0
    poll_failure = 0 if result.get("ok") else 1

    states_count = int(result.get("states_count") or 0)
    published_to_kinesis = int(result.get("published_to_kinesis") or 0)
    failed_kinesis_records = int(result.get("failed_kinesis_records") or 0)
    raw_archive_success = 1 if result.get("raw_s3_key") else 0

    dimensions = [
        {"Name": "Environment", "Value": environment},
        {"Name": "Pipeline", "Value": "aircraft"},
        {"Name": "Component", "Value": "opensky_poller"},
        {"Name": "Stage", "Value": "poll"},
    ]

    metric_values = {
        "PollSuccess": poll_success,
        "PollFailure": poll_failure,
        "StatesCount": states_count,
        "PublishedToKinesis": published_to_kinesis,
        "FailedKinesisRecords": failed_kinesis_records,
        "RawArchiveSuccess": raw_archive_success,
    }

    metric_data = [
        {
            "MetricName": metric_name,
            "Dimensions": dimensions,
            "Unit": "Count",
            "Value": float(metric_value),
        }
        for metric_name, metric_value in metric_values.items()
    ]

    try:
        cloudwatch = boto3.client("cloudwatch")
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=metric_data,
        )

        print_json(
            {
                "ok": True,
                "event": "local_opensky_poller_metrics_published",
                "namespace": METRIC_NAMESPACE,
                "environment": environment,
                "pipeline": "aircraft",
                "component": "opensky_poller",
                "stage": "poll",
                "metrics": metric_values,
            }
        )

    except Exception as exc:
        # Do not fail the poller just because monitoring failed.
        print_json(
            {
                "ok": False,
                "event": "local_opensky_poller_metric_publish_failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )


def publish_local_poller_failure_metrics(error_type: str) -> None:
    publish_local_poller_metrics(
        {
            "ok": False,
            "states_count": 0,
            "published_to_kinesis": 0,
            "failed_kinesis_records": 0,
            "raw_s3_key": None,
            "error_type": error_type,
        }
    )


def run_once() -> int:
    configure_lambda_like_environment()

    # Important:
    # app.py creates boto3 clients at import time, so env vars must be set before this import.
    import app

    result = app.handler(
        {
            "source": "local-runner",
            "runtime": "local",
        },
        None,
    )

    print_json(result)

    publish_local_poller_metrics(result)

    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run existing OpenSky poller locally")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=30)

    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    while True:
        try:
            exit_code = run_once()

        except Exception as exc:
            print_json(
                {
                    "ok": False,
                    "event": "local_opensky_poller_failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

            publish_local_poller_failure_metrics(type(exc).__name__)

            exit_code = 1

        if args.once:
            return exit_code

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
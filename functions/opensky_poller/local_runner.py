import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ENV_DIR = REPO_ROOT / "envs" / "dev"


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
    os.environ["OPENSKY_SECRET_ARN"] = terraform_output("opensky_credentials_secret_arn")

    os.environ["OPENSKY_TOKEN_URL"] = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/"
        "protocol/openid-connect/token"
    )
    os.environ["OPENSKY_STATES_URL"] = "https://opensky-network.org/api/states/all"

    # Same test box currently configured in Terraform for the Lambda.
    os.environ["OPENSKY_LAMIN"] = "37.0"
    os.environ["OPENSKY_LOMIN"] = "-123.0"
    os.environ["OPENSKY_LAMAX"] = "38.5"
    os.environ["OPENSKY_LOMAX"] = "-121.5"

    os.environ["ENVIRONMENT"] = "dev"
    os.environ["MODE"] = "local-opensky-poller"


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2), flush=True)


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
            exit_code = 1

        if args.once:
            return exit_code

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
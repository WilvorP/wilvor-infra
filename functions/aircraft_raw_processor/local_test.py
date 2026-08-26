import base64
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import boto3


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
    os.environ["EVENT_BUS_NAME"] = "default"

    os.environ["AIRCRAFT_ARCHIVE_BUCKET"] = terraform_output(
        "aircraft_archive_bucket_name"
    )
    os.environ["AIRCRAFT_CLEAN_STREAM_NAME"] = terraform_output(
        "aircraft_clean_stream_name"
    )


def get_latest_raw_opensky_object() -> tuple[str, dict]:
    bucket = os.environ["AIRCRAFT_ARCHIVE_BUCKET"]
    s3 = boto3.client("s3")

    response = s3.list_objects_v2(
        Bucket=bucket,
        Prefix="raw/source=opensky/",
    )

    objects = response.get("Contents") or []
    if not objects:
        raise RuntimeError("No raw OpenSky files found in S3")

    latest = max(objects, key=lambda item: item["LastModified"])
    key = latest["Key"]

    obj = s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(obj["Body"].read().decode("utf-8"))

    return key, payload


def build_fake_kinesis_event(
    *,
    s3_key: str,
    opensky_response: dict,
    limit: int = 10,
) -> dict:
    states = opensky_response.get("states") or []
    response_time = opensky_response.get("time")
    poll_id = Path(s3_key).stem

    records = []

    for index, state_vector in enumerate(states[:limit]):
        raw_event = {
            "schema_version": "opensky_aircraft_raw.v1",
            "source": "opensky",
            "poll_id": poll_id,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_s3_bucket": os.environ["AIRCRAFT_ARCHIVE_BUCKET"],
            "raw_s3_key": s3_key,
            "opensky_response_time": response_time,
            "raw_index": index,
            "raw_state_vector": state_vector,
        }

        encoded = base64.b64encode(
            json.dumps(raw_event).encode("utf-8")
        ).decode("utf-8")

        records.append(
            {
                "kinesis": {
                    "sequenceNumber": f"local-test-{index}",
                    "data": encoded,
                }
            }
        )

    return {"Records": records}


def main() -> None:
    configure_lambda_like_environment()

    # Import after env vars are set because app.py creates boto3 clients at import time.
    import app

    s3_key, opensky_response = get_latest_raw_opensky_object()

    print(f"Using raw file: {s3_key}")
    print(f"Total raw states in file: {len(opensky_response.get('states') or [])}")

    event = build_fake_kinesis_event(
        s3_key=s3_key,
        opensky_response=opensky_response,
        limit=10,
    )

    result = app.handler(
        event,
        SimpleNamespace(aws_request_id="local-aircraft-raw-processor-test"),
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
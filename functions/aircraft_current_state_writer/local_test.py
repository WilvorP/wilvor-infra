import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import boto3


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_CODE_DIR = REPO_ROOT / "functions" / "shared"
DEV_ENV_DIR = REPO_ROOT / "envs" / "dev"

sys.path.insert(0, str(SHARED_CODE_DIR))


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

    os.environ["AIRCRAFT_CURRENT_STATE_TABLE_NAME"] = terraform_output(
        "aircraft_current_state_table_name"
    )


def get_latest_clean_records(limit: int = 10) -> list[dict]:
    clean_stream_name = terraform_output("aircraft_clean_stream_name")
    kinesis = boto3.client("kinesis")

    stream = kinesis.describe_stream(StreamName=clean_stream_name)
    shard_id = stream["StreamDescription"]["Shards"][0]["ShardId"]

    iterator_response = kinesis.get_shard_iterator(
        StreamName=clean_stream_name,
        ShardId=shard_id,
        ShardIteratorType="TRIM_HORIZON",
    )

    records_response = kinesis.get_records(
        ShardIterator=iterator_response["ShardIterator"],
        Limit=limit,
    )

    clean_records = []

    for record in records_response.get("Records", []):
        clean_records.append(json.loads(record["Data"].decode("utf-8")))

    return clean_records


def build_fake_kinesis_event(clean_records: list[dict]) -> dict:
    records = []

    for index, clean_record in enumerate(clean_records):
        encoded = base64.b64encode(
            json.dumps(clean_record).encode("utf-8")
        ).decode("utf-8")

        records.append(
            {
                "kinesis": {
                    "sequenceNumber": f"local-clean-test-{index}",
                    "data": encoded,
                }
            }
        )

    return {"Records": records}


def main() -> None:
    configure_lambda_like_environment()

    import app

    clean_records = get_latest_clean_records(limit=10)

    if not clean_records:
        raise RuntimeError(
            "No clean records found. Run opensky local_runner first, then raw processor should publish to clean stream."
        )

    print(f"Clean records loaded: {len(clean_records)}")
    print("First clean record:")
    print(json.dumps(clean_records[0], indent=2))

    event = build_fake_kinesis_event(clean_records)

    result = app.handler(
        event,
        SimpleNamespace(aws_request_id="local-current-state-writer-test"),
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
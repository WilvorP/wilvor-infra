import json
import os
import time
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3


kinesis = boto3.client("kinesis")
s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

_cached_secret: dict[str, str] | None = None
_cached_token: str | None = None
_cached_token_expires_at: int = 0


def test_public_internet() -> dict:
    request = urllib.request.Request(
        "https://example.com",
        method="GET",
        headers={"User-Agent": "wilvor-lambda-connectivity-test"},
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        return {
            "ok": True,
            "status": response.status,
            "message": "Lambda can reach public internet"
        }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def get_secret() -> dict[str, str]:
    global _cached_secret

    if _cached_secret:
        return _cached_secret

    secret_arn = os.environ["OPENSKY_SECRET_ARN"]
    response = secrets.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])

    # Support both formats:
    # {"client_id": "...", "client_secret": "..."}
    # {"clientId": "...", "clientSecret": "..."}
    client_id = secret.get("client_id") or secret.get("clientId")
    client_secret = secret.get("client_secret") or secret.get("clientSecret")

    if not client_id or not client_secret:
        raise ValueError("OpenSky secret must contain client_id/client_secret or clientId/clientSecret")

    _cached_secret = {
        "client_id": client_id,
        "client_secret": client_secret,
    }

    return _cached_secret


def get_access_token() -> str:
    global _cached_token
    global _cached_token_expires_at

    now_epoch = int(time.time())

    if _cached_token and now_epoch < (_cached_token_expires_at - 60):
        return _cached_token

    creds = get_secret()

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode("utf-8")

    request = urllib.request.Request(
        os.environ["OPENSKY_TOKEN_URL"],
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        token_response = json.loads(response.read().decode("utf-8"))

    _cached_token = token_response["access_token"]
    _cached_token_expires_at = now_epoch + int(token_response.get("expires_in", 1800))

    return _cached_token


def fetch_opensky_states() -> dict[str, Any]:
    token = get_access_token()

    params = urllib.parse.urlencode({
        "lamin": os.environ["OPENSKY_LAMIN"],
        "lomin": os.environ["OPENSKY_LOMIN"],
        "lamax": os.environ["OPENSKY_LAMAX"],
        "lomax": os.environ["OPENSKY_LOMAX"],
    })

    url = f"{os.environ['OPENSKY_STATES_URL']}?{params}"

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def archive_raw_response(poll_id: str, response_body: dict[str, Any]) -> str:
    bucket = os.environ["AIRCRAFT_ARCHIVE_BUCKET"]
    now = now_utc()

    key = (
        "raw/source=opensky/"
        f"year={now.year:04d}/"
        f"month={now.month:02d}/"
        f"day={now.day:02d}/"
        f"hour={now.hour:02d}/"
        f"{poll_id}.json"
    )

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(response_body).encode("utf-8"),
        ContentType="application/json",
    )

    return key


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def publish_raw_records(
    *,
    poll_id: str,
    opensky_response: dict[str, Any],
    fetched_at_utc: str,
) -> tuple[int, int]:
    stream_name = os.environ["AIRCRAFT_RAW_STREAM_NAME"]
    states = opensky_response.get("states") or []
    response_time = opensky_response.get("time")

    records = []

    for index, state_vector in enumerate(states):
        icao24 = None

        if isinstance(state_vector, list) and len(state_vector) > 0:
            icao24 = state_vector[0]

        partition_key = str(icao24 or f"unknown-{index}")

        raw_event = {
            "schema_version": "opensky_aircraft_raw.v1",
            "source": "opensky",
            "poll_id": poll_id,
            "fetched_at_utc": fetched_at_utc,
            "opensky_response_time": response_time,
            "raw_index": index,
            "raw_state_vector": state_vector,
        }

        records.append({
            "PartitionKey": partition_key,
            "Data": json.dumps(raw_event).encode("utf-8"),
        })

    published = 0
    failed = 0

    for batch in chunked(records, 500):
        result = kinesis.put_records(
            StreamName=stream_name,
            Records=batch,
        )

        failed += int(result.get("FailedRecordCount", 0))
        published += len(batch) - int(result.get("FailedRecordCount", 0))

    return published, failed


def handler(event, context):


    if isinstance(event, dict) and event.get("test") == "internet":
        return test_public_internet()

    if isinstance(event, dict) and event.get("test") == "opensky-token":
        token = get_access_token()
        return {
            "ok": True,
            "message": "OpenSky token fetched",
            "token_prefix": token[:8],
            "token_length": len(token)
        }    

    poll_id = str(uuid.uuid4())
    fetched_at_utc = now_utc_iso()

    opensky_response = fetch_opensky_states()

    s3_key = archive_raw_response(
        poll_id=poll_id,
        response_body=opensky_response,
    )

    published_count, failed_count = publish_raw_records(
        poll_id=poll_id,
        opensky_response=opensky_response,
        fetched_at_utc=fetched_at_utc,
    )

    states_count = len(opensky_response.get("states") or [])

    return {
        "ok": True,
        "mode": "real-opensky-poller",
        "poll_id": poll_id,
        "states_count": states_count,
        "published_to_kinesis": published_count,
        "failed_kinesis_records": failed_count,
        "raw_s3_key": s3_key,
    }
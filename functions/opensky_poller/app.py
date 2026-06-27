import json
import os
import time
import uuid
from datetime import datetime, timezone

import boto3


kinesis = boto3.client("kinesis")


def handler(event, context):
    """
    Temporary plumbing-test Lambda.

    This does NOT call OpenSky yet.
    It publishes one test raw aircraft event into the aircraft-raw Kinesis stream.
    """

    stream_name = os.environ["AIRCRAFT_RAW_STREAM_NAME"]

    now = datetime.now(timezone.utc)
    poll_id = str(uuid.uuid4())

    raw_event = {
        "schema_version": "opensky_aircraft_raw.v1",
        "source": "wilvor-plumbing-test",
        "poll_id": poll_id,
        "fetched_at": now.isoformat(),
        "opensky_response_time": int(time.time()),
        "request": {
            "endpoint": "/states/all",
            "mode": "plumbing-test"
        },
        "raw_index": 0,
        "raw_state_vector": [
            "testicao24",
            "WILVOR1 ",
            "United States",
            int(time.time()),
            int(time.time()),
            -122.4194,
            37.7749,
            10000.0,
            False,
            230.0,
            270.0,
            0.0,
            None,
            10200.0,
            "1200",
            False,
            0,
            3
        ]
    }

    response = kinesis.put_record(
        StreamName=stream_name,
        PartitionKey="testicao24",
        Data=json.dumps(raw_event).encode("utf-8")
    )

    return {
        "ok": True,
        "message": "Published test OpenSky raw event to Kinesis.",
        "stream_name": stream_name,
        "poll_id": poll_id,
        "shard_id": response.get("ShardId"),
        "sequence_number": response.get("SequenceNumber")
    }
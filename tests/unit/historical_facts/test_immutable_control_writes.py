"""Create-once immutable historical control-plane PutObject semantics."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any, Callable

import pytest
from botocore.exceptions import ClientError


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


class VersionedFakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.versions: dict[str, list[bytes]] = {}
        self.put_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        body = kwargs["Body"]
        if not isinstance(body, (bytes, bytearray)):
            body = bytes(body)
        self.put_calls.append(dict(kwargs, Body=body))
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed", 412)
        self.objects[key] = body
        self.versions.setdefault(key, []).append(body)
        return {}

    def get_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key])}


@pytest.fixture
def writer(
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    return load_repo_module(
        "historical_facts_gap_writer_immutable",
        "functions/historical_facts/runtime/gap_writer.py",
    )


def test_first_immutable_write_succeeds(writer):
    s3 = VersionedFakeS3()
    result = writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key="metadata/epoch/epoch-1.json",
        payload={"record_type": "COLLECTION_EPOCH", "collection_epoch_id": "epoch-1"},
    )
    assert result.outcome == writer.CREATED
    assert result.key == "metadata/epoch/epoch-1.json"
    assert s3.put_calls[0]["IfNoneMatch"] == "*"
    assert len(s3.versions[result.key]) == 1


def test_repeated_identical_write_is_412_idempotent_and_not_an_overwrite(writer):
    s3 = VersionedFakeS3()
    payload = {"record_type": "COLLECTION_EPOCH", "collection_epoch_id": "epoch-1"}
    first = writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key="metadata/epoch/epoch-1.json",
        payload=payload,
    )
    second = writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key="metadata/epoch/epoch-1.json",
        payload=payload,
    )
    assert first.outcome == writer.CREATED
    assert second.outcome == writer.ALREADY_PERSISTED
    assert len(s3.versions[first.key]) == 1
    assert all(call["IfNoneMatch"] == "*" for call in s3.put_calls)
    assert not any(call.get("IfNoneMatch") in (None, "") for call in s3.put_calls)


def test_412_conflicting_content_fails_closed(writer):
    s3 = VersionedFakeS3()
    s3.objects["metadata/gaps/year=2026/month=07/day=18/bound|epoch-1|x.json"] = (
        b'{"record_type":"COLLECTION_GAP","reason":"OTHER"}'
    )
    with pytest.raises(writer.ImmutableControlConflictError, match="conflict"):
        writer.put_immutable_json(
            s3_client=s3,
            bucket_name="test-historical",
            key="metadata/gaps/year=2026/month=07/day=18/bound|epoch-1|x.json",
            payload={
                "record_type": "COLLECTION_GAP",
                "reason": "PRODUCER_PUT_EVENTS_FAILURE",
                "dedup_id": "bound|epoch-1|x",
            },
        )
    assert "metadata/gaps/year=2026/month=07/day=18/bound|epoch-1|x.json" not in s3.versions


def test_409_retries_then_creates(writer):
    class ConflictThenCreate(VersionedFakeS3):
        def __init__(self) -> None:
            super().__init__()
            self._conflicts = 1

        def put_object(self, **kwargs):
            if self._conflicts:
                self._conflicts -= 1
                self.put_calls.append(dict(kwargs))
                raise _client_error("ConditionalRequestConflict", 409)
            return super().put_object(**kwargs)

    s3 = ConflictThenCreate()
    result = writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key="metadata/incidents/year=2026/month=07/day=18/a.json",
        payload={"record_type": "UNBOUND_COLLECTION_INCIDENT", "dedup_id": "a"},
        max_conflict_retries=3,
    )
    assert result.outcome == writer.CREATED
    assert len(s3.versions[result.key]) == 1
    assert s3.put_calls[0]["IfNoneMatch"] == "*"
    assert s3.put_calls[1]["IfNoneMatch"] == "*"


def test_409_is_not_silent_success(writer):
    class AlwaysConflict(VersionedFakeS3):
        def put_object(self, **kwargs):
            self.put_calls.append(dict(kwargs))
            raise _client_error("ConditionalRequestConflict", 409)

    s3 = AlwaysConflict()
    with pytest.raises(writer.ImmutableControlWriteError, match="retries exhausted"):
        writer.put_immutable_json(
            s3_client=s3,
            bucket_name="test-historical",
            key="metadata/epoch/epoch-1.json",
            payload={"record_type": "COLLECTION_EPOCH"},
            max_conflict_retries=2,
        )
    assert len(s3.put_calls) == 3
    assert s3.objects == {}


def test_unexpected_s3_error_is_not_swallowed(writer):
    class Boom(VersionedFakeS3):
        def put_object(self, **kwargs):
            raise _client_error("InternalError", 500)

    with pytest.raises(ClientError) as caught:
        writer.put_immutable_json(
            s3_client=Boom(),
            bucket_name="test-historical",
            key="metadata/epoch/epoch-1.json",
            payload={"record_type": "COLLECTION_EPOCH"},
        )
    assert caught.value.response["ResponseMetadata"]["HTTPStatusCode"] == 500


def test_coverage_retry_with_new_created_at_is_already_persisted(writer):
    s3 = VersionedFakeS3()
    key = "metadata/coverage/stream=facts/year=2026/month=07/day=18/coverage|facts|t.json"
    first = {
        "record_type": "COVERAGE_INTERVAL",
        "collection_epoch_id": "epoch-1",
        "stream": "facts",
        "interval_start_utc": "2026-07-18T12:00:00Z",
        "interval_end_utc": "2026-07-18T12:15:00Z",
        "created_at_utc": "2026-07-18T12:16:00Z",
        "dedup_id": "coverage|facts|t",
    }
    writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key=key,
        payload=first,
    )
    later = dict(first)
    later["created_at_utc"] = "2026-07-18T12:46:00Z"
    result = writer.put_immutable_json(
        s3_client=s3,
        bucket_name="test-historical",
        key=key,
        payload=later,
    )
    assert result.outcome == writer.ALREADY_PERSISTED
    assert len(s3.versions[key]) == 1
    stored = json_loads(s3.objects[key])
    assert stored["created_at_utc"] == "2026-07-18T12:16:00Z"


def json_loads(raw: bytes) -> dict[str, Any]:
    import json

    return json.loads(raw.decode("utf-8"))

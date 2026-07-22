from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[
            tuple[str, str],
            bytes,
        ] = {}

        self.uploads: list[
            dict[str, Any]
        ] = []

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> None:
        body = Path(filename).read_bytes()

        self.objects[(bucket, key)] = body

        self.uploads.append(
            {
                "filename": filename,
                "bucket": bucket,
                "key": key,
                "extra_args": ExtraArgs or {},
            }
        )

    def download_file(
        self,
        bucket: str,
        key: str,
        filename: str,
    ) -> None:
        Path(filename).write_bytes(
            self.objects[(bucket, key)]
        )

    def put_object(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        body = kwargs["Body"]

        if hasattr(body, "read"):
            body = body.read()

        self.objects[
            (
                kwargs["Bucket"],
                kwargs["Key"],
            )
        ] = bytes(body)

        return {"ETag": "fake"}


class FakeBatchWriter(
    AbstractContextManager["FakeBatchWriter"]
):
    def __init__(
        self,
        table: "FakeTable",
    ) -> None:
        self.table = table

    def __enter__(self) -> "FakeBatchWriter":
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        return None

    def put_item(
        self,
        *,
        Item: dict[str, Any],
    ) -> None:
        self.table.put_item(Item=Item)

    def delete_item(
        self,
        *,
        Key: dict[str, Any],
    ) -> None:
        self.table.items.pop(
            (
                Key["airport_id"],
                Key["record_id"],
            ),
            None,
        )


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

    def get_item(
        self,
        *,
        Key: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        item = self.items.get(
            (
                Key["airport_id"],
                Key["record_id"],
            )
        )

        if item:
            return {"Item": dict(item)}

        return {}

    def put_item(
        self,
        *,
        Item: dict[str, Any],
    ) -> dict[str, Any]:
        self.items[
            (
                Item["airport_id"],
                Item["record_id"],
            )
        ] = dict(Item)

        return {}

    def query(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        airport_id = (
            kwargs[
                "ExpressionAttributeValues"
            ][":airport_id"]
        )

        return {
            "Items": [
                dict(item)
                for (
                    item_airport_id,
                    _,
                ), item in sorted(
                    self.items.items()
                )
                if item_airport_id == airport_id
            ]
        }

    def batch_writer(
        self,
        **kwargs: Any,
    ) -> FakeBatchWriter:
        return FakeBatchWriter(self)


class FakeEventsClient:
    def __init__(self) -> None:
        self.entries: list[
            dict[str, Any]
        ] = []

        self.failed_entry_count = 0

    def put_events(
        self,
        *,
        Entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.entries.extend(Entries)

        return {
            "FailedEntryCount": (
                self.failed_entry_count
            ),
            "Entries": [
                {}
                for _ in Entries
            ],
        }
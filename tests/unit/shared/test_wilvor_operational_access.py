"""Direct tests for shared operational DynamoDB access primitives."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from boto3.dynamodb.conditions import Key

from wilvor_operational import access


REPO_ROOT = Path(__file__).resolve().parents[3]
ACCESS_PATH = (
    REPO_ROOT
    / "functions"
    / "shared"
    / "wilvor_operational"
    / "access.py"
)
NOW = 1_788_661_800


class RecordingTable:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])
        self._index = 0

    def _next(self, default):
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return default

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self._next({})

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._next({"Items": []})

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return self._next({"Items": []})


def condition_shape(expr):
    if expr is None:
        return None

    if hasattr(expr, "get_expression"):
        built = expr.get_expression()
        values = []
        for value in built["values"]:
            if hasattr(value, "get_expression"):
                values.append(condition_shape(value))
            elif hasattr(value, "name") and not isinstance(value, str):
                values.append(("name", value.name))
            else:
                values.append(value)
        return (built["operator"],) + tuple(values)

    return expr


def test_get_item_returns_none_when_missing():
    table = RecordingTable([{}])

    item = access.get_item(
        table,
        {"aircraft_id": "abc123"},
        consistent_read=True,
    )

    assert item is None
    assert table.calls == [
        (
            "get_item",
            {
                "Key": {"aircraft_id": "abc123"},
                "ConsistentRead": True,
            },
        )
    ]


def test_get_item_returns_retained_record():
    retained = {"aircraft_id": "abc123", "expires_at_epoch": NOW}
    table = RecordingTable([{"Item": retained}])

    item = access.get_item(
        table,
        {"aircraft_id": "abc123"},
        consistent_read=True,
    )

    assert item is retained


def test_query_latest_is_newest_first_single_page():
    table = RecordingTable(
        [
            {
                "Items": [{"risk_id": "r1"}],
                "LastEvaluatedKey": {"risk_id": "r1"},
            },
            {"Items": [{"risk_id": "r2"}]},
        ]
    )

    items = access.query_latest(
        table,
        "encounter_id-generated_at_epoch-index",
        "encounter_id",
        "encounter-1",
        limit=1,
        key=Key,
    )

    assert items == [{"risk_id": "r1"}]
    assert len(table.calls) == 1
    kwargs = table.calls[0][1]
    assert kwargs["IndexName"] == "encounter_id-generated_at_epoch-index"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 1
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "encounter_id"),
        "encounter-1",
    )


def test_query_all_and_scan_all_drain_pages():
    query_table = RecordingTable(
        [
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}]},
        ]
    )
    scan_table = RecordingTable(
        [
            {"Items": [{"id": "c"}], "LastEvaluatedKey": {"id": "c"}},
            {"Items": [{"id": "d"}]},
        ]
    )

    queried = access.query_all(
        query_table,
        KeyConditionExpression=Key("hazard_version_key").eq("h#v"),
        ScanIndexForward=True,
        ConsistentRead=True,
    )
    scanned = access.scan_all(scan_table)

    assert queried == [{"id": "a"}, {"id": "b"}]
    assert scanned == [{"id": "c"}, {"id": "d"}]
    assert query_table.calls[1][1]["ExclusiveStartKey"] == {"id": "a"}
    assert scan_table.calls[1][1]["ExclusiveStartKey"] == {"id": "c"}


def test_query_page_and_scan_page_do_not_drain():
    query_table = RecordingTable(
        [
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}]},
        ]
    )
    scan_table = RecordingTable(
        [
            {"Items": [{"id": "c"}], "LastEvaluatedKey": {"id": "c"}},
            {"Items": [{"id": "d"}]},
        ]
    )

    query_page = access.query_page(query_table, Limit=1)
    scan_page = access.scan_page(scan_table, Limit=1)

    assert query_page == {
        "items": [{"id": "a"}],
        "last_evaluated_key": {"id": "a"},
    }
    assert scan_page == {
        "items": [{"id": "c"}],
        "last_evaluated_key": {"id": "c"},
    }
    assert len(query_table.calls) == 1
    assert len(scan_table.calls) == 1


def test_access_source_has_no_env_clock_cache_or_current_set():
    source = ACCESS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "current_set" not in source
    assert "os.environ" not in source
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "boto3.resource" not in source
    assert "boto3.client" not in source
    assert "scan_count" not in source
    assert "query_count" not in source

    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    assert "wilvor_operational.current_set" not in imported
    assert "current_set" not in imported
    assert "wilvor_ai" not in imported


def test_access_module_does_not_import_current_set():
    assert "current_set" not in access.__dict__
    assert "wilvor_operational.current_set" not in inspect.getmodule(
        access
    ).__dict__
    assert access.get_item.__module__ == "wilvor_operational.access"

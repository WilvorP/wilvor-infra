"""Readers for a stored recommendation and its airport-assessment rows."""

from __future__ import annotations

import inspect

from wilvor_operational import readers


class RecordingTable:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)
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
        raise AssertionError("scan")

    def put_item(self, **kwargs):
        raise AssertionError("put_item")


def condition_shape(expr):
    built = expr.get_expression()
    values = []
    for value in built["values"]:
        if hasattr(value, "name") and not isinstance(value, str):
            values.append(("name", value.name))
        else:
            values.append(value)
    return (built["operator"],) + tuple(values)


def test_recommendation_get_uses_primary_key_and_consistent_read():
    table = RecordingTable([{"Item": {"recommendation_id": "rec-1"}}])

    found = readers.get_recommendation_record(table, "rec-1")
    missing = readers.get_recommendation_record(RecordingTable([{}]), "rec-2")

    assert found == {"recommendation_id": "rec-1"}
    assert missing is None
    operation, kwargs = table.calls[0]
    assert operation == "get_item"
    assert kwargs["Key"] == {"recommendation_id": "rec-1"}
    assert kwargs["ConsistentRead"] is True
    assert "FilterExpression" not in kwargs
    assert "put_item" not in inspect.getsource(readers.get_recommendation_record)
    assert "scan" not in inspect.getsource(readers.get_recommendation_record)


def test_evaluation_query_uses_partition_key_and_paginates():
    table = RecordingTable(
        [
            {
                "Items": [{"evaluation_id": "eval-1", "airport_id": "KDEN"}],
                "LastEvaluatedKey": {"evaluation_id": "eval-1", "airport_id": "KDEN"},
            },
            {"Items": [{"evaluation_id": "eval-1", "airport_id": "KSEA"}]},
        ]
    )

    rows = readers.query_airport_assessments_for_evaluation(table, "eval-1")

    assert [row["airport_id"] for row in rows] == ["KDEN", "KSEA"]
    assert len(table.calls) == 2
    first = table.calls[0][1]
    second = table.calls[1][1]
    assert first["ConsistentRead"] is True
    assert second["ConsistentRead"] is True
    assert condition_shape(first["KeyConditionExpression"]) == (
        "=",
        ("name", "evaluation_id"),
        "eval-1",
    )
    assert second["ExclusiveStartKey"]["airport_id"] == "KDEN"
    assert "scan" not in inspect.getsource(readers.query_airport_assessments_for_evaluation)
    assert "put_item" not in inspect.getsource(
        readers.query_airport_assessments_for_evaluation
    )

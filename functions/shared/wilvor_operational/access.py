"""Narrow DynamoDB page and lookup primitives for Wilvor operational reads.

Callers supply table handles. This module does not create AWS resources,
read environment variables, acquire wall-clock time, cache results, or
apply current-set semantics.
"""

from boto3.dynamodb.conditions import Key


def get_item(table, key, *, consistent_read=False):
    response = table.get_item(
        Key=key,
        ConsistentRead=consistent_read,
    )
    return response.get("Item")


def query_page(table, **kwargs):
    response = table.query(**kwargs)
    return {
        "items": response.get("Items", []),
        "last_evaluated_key": response.get("LastEvaluatedKey"),
    }


def scan_page(table, **kwargs):
    response = table.scan(**kwargs)
    return {
        "items": response.get("Items", []),
        "last_evaluated_key": response.get("LastEvaluatedKey"),
    }


def query_all(table, **kwargs):
    items = []

    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            return items

        kwargs["ExclusiveStartKey"] = last_key


def scan_all(table, **kwargs):
    items = []

    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            return items

        kwargs["ExclusiveStartKey"] = last_key


def query_latest(
    table,
    index_name,
    partition_name,
    partition_value,
    limit=10,
    *,
    key=Key,
):
    response = table.query(
        IndexName=index_name,
        KeyConditionExpression=key(partition_name).eq(partition_value),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])

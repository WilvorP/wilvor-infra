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


def query_bounded(table, *, max_items, **kwargs):
    """Follow query pages only until ``max_items`` rows or the key is exhausted.

    This is not Scan. A remaining ``LastEvaluatedKey`` means the page is
    truncated, not that the matching set is empty or a verified zero.
    """

    if type(max_items) is not int or isinstance(max_items, bool) or max_items < 1:
        raise TypeError("max_items must be a positive int")

    items = []
    last_key = kwargs.get("ExclusiveStartKey")

    while len(items) < max_items:
        page_kwargs = dict(kwargs)
        page_kwargs["Limit"] = max_items - len(items)
        if last_key:
            page_kwargs["ExclusiveStartKey"] = last_key
        else:
            page_kwargs.pop("ExclusiveStartKey", None)
        response = table.query(**page_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return {
                "items": items,
                "last_evaluated_key": None,
                "truncated": False,
                "rows_returned": len(items),
            }

    truncated = last_key is not None
    bounded = items[:max_items]
    return {
        "items": bounded,
        "last_evaluated_key": last_key if truncated else None,
        "truncated": truncated,
        "rows_returned": len(bounded),
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

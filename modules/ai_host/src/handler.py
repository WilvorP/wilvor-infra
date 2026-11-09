"""Closed development entry point for the isolated Wilvor AI host.

The function exists so the development account can hold a Lambda that is
not the operational API. It does not serve POST /ai/query. The event and
the Lambda context are discarded and are not authority. This package does
not call DynamoDB, AWS APIs, or a model.
"""


def lambda_handler(event, context):
    del event, context

    return {
        "statusCode": 503,
        "headers": {
            "content-type": "application/json",
        },
        "body": (
            "{"
            '"schema":"wilvor.ai.host.closed.v1",'
            '"admitted":false,'
            '"production_issuance_available":false,'
            '"ai_query_route_enabled":false,'
            '"ledger_writes_enabled":false,'
            '"reason":"ai_host_invoke_closed"'
            "}"
        ),
    }

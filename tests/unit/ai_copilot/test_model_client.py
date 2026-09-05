import json

import pytest
from botocore.exceptions import ClientError

from model_client import (
    BedrockConverseClient,
    ModelThrottled,
)


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def converse(self, **kwargs):
        self.request = kwargs
        if self.error:
            raise self.error
        return self.response


def client(fake):
    return BedrockConverseClient(
        model_id="us.test-model",
        max_output_tokens=1200,
        temperature=0.1,
        client=fake,
    )


def test_structured_response_and_strict_tools():
    fake = FakeBedrock(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": '{"answer":"ok"}'}
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 5,
            },
            "metrics": {"latencyMs": 22},
        }
    )
    result = client(fake).converse(
        [{"role": "user", "content": [{"text": "x"}]}],
        tools=[
            {
                "toolSpec": {
                    "name": "get_data",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        }
                    },
                }
            }
        ],
    )

    assert result.text == '{"answer":"ok"}'
    assert fake.request["toolConfig"]["tools"][0][
        "toolSpec"
    ]["strict"] is True
    output_schema = json.loads(
        fake.request["outputConfig"]["textFormat"][
            "structure"
        ]["jsonSchema"]["schema"]
    )
    assert output_schema["additionalProperties"] is False


def test_tool_request_is_normalized():
    fake = FakeBedrock(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "use-1",
                                "name": "get_aircraft_context",
                                "input": {
                                    "aircraft_id": "a1"
                                },
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
        }
    )

    result = client(fake).converse(
        [{"role": "user", "content": [{"text": "x"}]}]
    )

    assert result.tool_requests[0].name == (
        "get_aircraft_context"
    )


def test_bedrock_throttling_is_sanitized():
    error = ClientError(
        {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "secret details",
            }
        },
        "Converse",
    )
    with pytest.raises(ModelThrottled) as caught:
        client(FakeBedrock(error=error)).converse([])
    assert "secret" not in str(caught.value)

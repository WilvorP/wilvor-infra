from agent import AgentOrchestrator
from model_client import ModelResponse, ToolRequest
from tools import ToolExecution
from tools import ToolRegistry


class FakeModel:
    model_id = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def converse(self, messages, *, tools=None):
        self.calls += 1
        return self.responses.pop(0)


class FakeRegistry:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def definitions(self):
        return []

    def execute(self, *, tool_use_id, name, value):
        self.calls.append((name, value))
        if self.fail:
            return ToolExecution(
                tool_use_id,
                name,
                "FAILURE",
                1,
                {"error": "failed"},
            )
        return ToolExecution(
            tool_use_id,
            name,
            "SUCCESS",
            1,
            {
                "value": "HIGH",
                "limitations": ["Fuel unavailable."],
                "dataFreshnessWarnings": [
                    "OPENSKY data freshness is STALE."
                ],
                "evidenceCatalog": [
                    {
                        "evidenceId": "risk.r1.level",
                        "label": "Risk level",
                    }
                ],
            },
        )


def text_response():
    return ModelResponse(
        message={
            "role": "assistant",
            "content": [{"text": "{}"}],
        },
        text="{}",
        input_tokens=1,
        output_tokens=1,
    )


def tool_response(*names):
    requests = [
        ToolRequest(
            tool_use_id=f"use-{index}",
            name=name,
            input={},
        )
        for index, name in enumerate(names)
    ]
    return ModelResponse(
        message={
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": request.tool_use_id,
                        "name": request.name,
                        "input": request.input,
                    }
                }
                for request in requests
            ],
        },
        text=None,
        tool_requests=requests,
    )


def orchestrator(model, registry, rounds=4):
    return AgentOrchestrator(
        model_client=model,
        tool_registry=registry,
        max_tool_rounds=rounds,
        max_tool_result_bytes=4096,
    )


def test_zero_tool_question():
    model = FakeModel([text_response()])
    result = orchestrator(
        model,
        FakeRegistry(),
    ).run([])
    assert result.output == "{}"
    assert result.tool_calls == []


def test_grounded_mode_rejects_initial_no_tool_answer():
    model = FakeModel(
        [
            text_response(),
            tool_response("one"),
            text_response(),
        ]
    )
    result = orchestrator(
        model,
        FakeRegistry(),
    ).run(
        [],
        require_tools=True,
        required_tool_name="one",
    )

    assert model.calls == 3
    assert result.tool_calls[0]["status"] == "SUCCESS"
    assert len(result.evidence_catalog) == 1


def test_one_tool_and_multiple_parallel_tools():
    registry = FakeRegistry()
    model = FakeModel(
        [
            tool_response("one", "two"),
            text_response(),
        ]
    )
    result = orchestrator(model, registry).run([])

    assert [call[0] for call in registry.calls] == [
        "one",
        "two",
    ]
    assert len(result.tool_calls) == 2
    assert result.limitations == ["Fuel unavailable."]
    assert len(result.evidence_catalog) == 1


def test_failed_tool_is_reported_to_model():
    registry = FakeRegistry(fail=True)
    result = orchestrator(
        FakeModel([tool_response("one"), text_response()]),
        registry,
    ).run([])
    assert result.tool_calls[0]["status"] == "FAILURE"


def test_max_tool_rounds_returns_controlled_output():
    result = orchestrator(
        FakeModel(
            [
                tool_response("one"),
                tool_response("one"),
            ]
        ),
        FakeRegistry(),
        rounds=1,
    ).run([])

    assert result.output["confidence"] == "UNKNOWN"
    assert "could not be completed" in result.output[
        "answer"
    ]


def test_unknown_tool_fails_safely():
    registry = ToolRegistry(
        client=object(),
        builders=object(),
    )
    result = registry.execute(
        tool_use_id="use-unknown",
        name="shell",
        value={},
    )
    assert result.status == "FAILURE"
    assert result.result == {
        "error": "Unknown or unapproved tool"
    }

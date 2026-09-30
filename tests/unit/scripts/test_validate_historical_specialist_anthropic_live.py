"""Offline tests for the 3A.5 direct Anthropic historical specialist harness."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from wilvor_ai.contracts import ToolResultStatus
from wilvor_ai.historical_specialist import (
    AI_LIST_DEFAULT,
    AI_LIST_MAX,
    MAX_IDENTICAL_TOOL_CALL_EXECUTIONS,
    MAX_INVALID_TOOL_CALL_CORRECTIONS,
    MAX_MODEL_TURNS,
    MAX_TOOL_CALLS,
)
from wilvor_ai.providers.anthropic_messages import DEFAULT_MODEL_ID
from wilvor_historical.query_contracts import (
    HAZARD_VERSION_WINDOW_LIMITATION,
    HistoricalQueryStatus,
)
from wilvor_historical.coverage_contracts import Evaluability


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "validate_historical_specialist_anthropic_live.py"


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_historical_specialist_anthropic_live",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return load_runner()


class FakeMessage:
    def __init__(self, payload, request_id="req_test"):
        self._payload = payload
        self._request_id = request_id
        self.to_dict_calls: list[dict[str, object]] = []

    def to_dict(self, mode=None):
        self.to_dict_calls.append({"mode": mode})
        return dict(self._payload)


class FakeSdkClient:
    def __init__(self, message=None):
        self.calls: list[dict[str, object]] = []
        self.messages = self
        self._message = message or FakeMessage(
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        )

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self._message


class CountingInner:
    def __init__(self):
        self.calls = 0

    def messages_create(self, **kwargs):
        self.calls += 1
        return {
            "id": f"msg_{self.calls}",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "{}"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }


def test_safety_invariants_unchanged():
    assert MAX_MODEL_TURNS == 3
    assert MAX_TOOL_CALLS == 2
    assert MAX_INVALID_TOOL_CALL_CORRECTIONS == 1
    assert MAX_IDENTICAL_TOOL_CALL_EXECUTIONS == 1
    assert AI_LIST_DEFAULT == 20
    assert AI_LIST_MAX == 25
    assert DEFAULT_MODEL_ID == "claude-sonnet-4-6"


def test_script_source_isolation(runner: ModuleType):
    source = SCRIPT.read_text(encoding="utf-8")
    assert "tests.fakes" not in source
    assert "tests.contracts" not in source
    assert "adapt_temperature_for_sdk" in source
    assert "duplicate_temperature_authority" in source
    assert "for key in sdk_kwargs" not in source
    assert "dotenv" not in source
    assert "with_options" not in source
    assert "max_retries=LIVE_MAX_RETRIES" in source
    assert "LIVE_MAX_RETRIES = 0" in source
    assert "timeout=LIVE_TIMEOUT_SECONDS" in source
    assert "LIVE_TIMEOUT_SECONDS = 240.0" in source
    assert 'to_dict(mode="json")' in source
    assert "MAX_LIVE_ANTHROPIC_CALLS = 40" in source
    assert "import anthropic" in source
    assert source.index("def import_anthropic_sdk") < source.index("import anthropic")
    assert "from anthropic import" not in source


def test_dry_run_does_not_read_api_key(runner: ModuleType, monkeypatch: pytest.MonkeyPatch):
    def fail_key(*_args, **_kwargs):
        raise AssertionError("dry-run must not read ANTHROPIC_API_KEY")

    monkeypatch.setattr(runner, "read_live_api_key", fail_key)
    monkeypatch.setattr(
        runner,
        "construct_live_sdk_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not construct SDK client")
        ),
    )
    monkeypatch.setattr(
        runner,
        "import_anthropic_sdk",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run must not import anthropic")),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = runner.main(["--action", "dry-run"])
    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["sdk_imported"] is False
    assert payload["aws_used"] is False
    assert payload["athena_used"] is False


def test_dry_run_succeeds_without_constructing_client(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        runner,
        "construct_live_sdk_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run constructed SDK client")
        ),
    )
    payload = runner.dry_run()
    assert payload["action"] == "dry-run"
    assert payload["max_live_anthropic_calls"] == 40
    assert payload["scenario_ids"][0] == "D1"
    assert payload["instruction_ref"] == "wilvor.historical.specialist.v2"
    assert payload["targeted_scenario_ids"] == [
        "A-R1",
        "B-R1",
        "B-R2",
        "B-R3",
        "C-R1",
        "C-R2",
        "C-R3",
        "K-R1",
        "K-R2",
        "H-R1",
        "J-R1",
    ]


def test_run_tier1_without_opt_in_blocks_before_key_read(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("WILVOR_RUN_LIVE_ANTHROPIC", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_key(*_args, **_kwargs):
        raise AssertionError("key read without opt-in")

    monkeypatch.setattr(runner, "read_live_api_key", fail_key)
    with pytest.raises(runner.LiveHarnessError, match="opt-in"):
        runner.run_tier1()


def test_missing_key_after_opt_in_blocks_before_network(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("WILVOR_RUN_LIVE_ANTHROPIC", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        runner,
        "construct_live_sdk_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing key constructed client")
        ),
    )
    with pytest.raises(runner.LiveHarnessError, match="missing ANTHROPIC_API_KEY"):
        runner.run_tier1()


def test_run_targeted_without_opt_in_blocks_before_key_read(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("WILVOR_RUN_LIVE_ANTHROPIC", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_key(*_args, **_kwargs):
        raise AssertionError("key read without opt-in")

    monkeypatch.setattr(runner, "read_live_api_key", fail_key)
    with pytest.raises(runner.LiveHarnessError, match="opt-in"):
        runner.run_targeted()


def test_run_targeted_requires_v2_before_live_opt_in(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        runner,
        "HISTORICAL_SPECIALIST_INSTRUCTION_REF",
        "wilvor.historical.specialist.v1",
    )

    def fail_key(*_args, **_kwargs):
        raise AssertionError("v2 check must precede key read")

    monkeypatch.setattr(runner, "read_live_api_key", fail_key)
    monkeypatch.setattr(
        runner,
        "require_live_opt_in",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("v2 check must precede live opt-in")
        ),
    )
    with pytest.raises(runner.LiveHarnessError, match="wilvor.historical.specialist.v2"):
        runner.run_targeted()


def test_blank_key_blocks(runner: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WILVOR_RUN_LIVE_ANTHROPIC", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setattr(
        runner,
        "construct_live_sdk_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("blank key constructed client")
        ),
    )
    with pytest.raises(runner.LiveHarnessError, match="blank"):
        runner.run_tier1()


def test_sdk_wrapper_one_create_json_mode_and_request_id(runner: ModuleType):
    message = FakeMessage(
        {
            "id": "msg_keep",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_secret",
                    "name": "summarize_historical_encounters",
                    "input": {"start_utc": "2026-09-11T10:00:00Z"},
                }
            ],
        },
        request_id="req_018EeWyXxfu5pfWkrYcMdjWG",
    )
    sdk = FakeSdkClient(message)
    wrapper = runner.AnthropicSDKMessagesClient(sdk)
    kwargs = {
        "model": "claude-sonnet-4-6",
        "temperature": 0,
        "max_tokens": 2048,
        "tools": [],
        "output_config": {"format": {"type": "json_schema"}},
    }
    original = dict(kwargs)
    mapped = wrapper.messages_create(**kwargs)
    assert kwargs == original
    assert len(sdk.calls) == 1
    assert "temperature" not in sdk.calls[0]
    assert sdk.calls[0]["extra_body"] == {"temperature": 0}
    assert sdk.calls[0]["model"] == original["model"]
    assert sdk.calls[0]["max_tokens"] == original["max_tokens"]
    assert sdk.calls[0]["tools"] == original["tools"]
    assert sdk.calls[0]["output_config"] == original["output_config"]
    assert message.to_dict_calls == [{"mode": "json"}]
    assert isinstance(mapped, dict)
    assert mapped["id"] == "msg_keep"
    assert "_request_id" not in mapped
    assert wrapper.last_request_id == "req_018EeWyXxfu5pfWkrYcMdjWG"


def test_temperature_moves_to_extra_body_without_mutating_input(runner: ModuleType):
    sdk = FakeSdkClient()
    wrapper = runner.AnthropicSDKMessagesClient(sdk)
    kwargs = {
        "model": "claude-sonnet-4-6",
        "temperature": 0,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "q"}]}],
        "tools": [{"name": "summarize_historical_encounters", "strict": True}],
        "tool_choice": {"type": "auto"},
        "output_config": {"format": {"type": "json_schema", "schema": {}}},
    }
    original = {
        "model": kwargs["model"],
        "temperature": kwargs["temperature"],
        "max_tokens": kwargs["max_tokens"],
        "messages": kwargs["messages"],
        "tools": kwargs["tools"],
        "tool_choice": kwargs["tool_choice"],
        "output_config": kwargs["output_config"],
    }
    wrapper.messages_create(**kwargs)
    assert kwargs == original
    received = sdk.calls[0]
    assert "temperature" not in received
    assert received["extra_body"] == {"temperature": 0}
    assert received["model"] == original["model"]
    assert received["max_tokens"] == original["max_tokens"]
    assert received["messages"] == original["messages"]
    assert received["tools"] == original["tools"]
    assert received["tool_choice"] == original["tool_choice"]
    assert received["output_config"] == original["output_config"]


def test_unrelated_extra_body_fields_are_preserved(runner: ModuleType):
    sdk = FakeSdkClient()
    wrapper = runner.AnthropicSDKMessagesClient(sdk)
    extra = {"metadata": {"source": "harness-test"}}
    kwargs = {
        "model": "claude-sonnet-4-6",
        "temperature": 0,
        "extra_body": extra,
    }
    wrapper.messages_create(**kwargs)
    assert kwargs["extra_body"] == {"metadata": {"source": "harness-test"}}
    assert sdk.calls[0]["extra_body"] == {
        "metadata": {"source": "harness-test"},
        "temperature": 0,
    }
    assert extra == {"metadata": {"source": "harness-test"}}


def test_duplicate_temperature_authority_fails_closed(runner: ModuleType):
    sdk = FakeSdkClient()
    wrapper = runner.AnthropicSDKMessagesClient(sdk)
    with pytest.raises(runner.LiveHarnessError, match="duplicate_temperature_authority"):
        wrapper.messages_create(
            temperature=0,
            extra_body={"temperature": 1},
        )
    assert sdk.calls == []


def test_no_generic_unknown_field_migration(runner: ModuleType):
    source = SCRIPT.read_text(encoding="utf-8")
    adapter = source.split("def adapt_temperature_for_sdk", 1)[1].split(
        "class BudgetedRecordingMessagesClient",
        1,
    )[0]
    assert "temperature" in adapter
    assert "output_config" not in adapter
    assert "tool_choice" not in adapter
    assert "max_tokens" not in adapter
    assert "unknown" not in adapter


def test_budget_forwards_forty_and_blocks_forty_first(runner: ModuleType):
    inner = CountingInner()
    client = runner.BudgetedRecordingMessagesClient(inner)
    kwargs = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "q"}]}],
    }
    for _ in range(40):
        client.messages_create(**kwargs)
    assert inner.calls == 40
    assert client.current_call_count == 40
    with pytest.raises(runner.CallBudgetExceeded):
        client.messages_create(**kwargs)
    assert inner.calls == 40
    assert client.last_transport_classification == "CALL_BUDGET_EXCEEDED"


def test_no_retry_constants(runner: ModuleType):
    assert runner.LIVE_MAX_RETRIES == 0
    assert runner.LIVE_TIMEOUT_SECONDS == 240.0
    source = SCRIPT.read_text(encoding="utf-8")
    assert "max_retries=0" not in source.replace("LIVE_MAX_RETRIES = 0", "")
    assert source.count("max_retries") == 2


def test_canned_production_contracts(runner: ModuleType):
    exact = runner.build_exact_count_response()
    zero = runner.build_verified_zero_response()
    truncated = runner.build_truncated_list_response()
    hazard = runner.build_hazard_limitation_response()
    assert exact.status is HistoricalQueryStatus.SUCCEEDED
    assert exact.coverage.evaluability is Evaluability.EVALUABLE
    assert exact.evidence.semantic_match_count == 2
    assert zero.status is HistoricalQueryStatus.VERIFIED_ZERO
    assert zero.coverage.evaluability is Evaluability.EVALUABLE
    assert zero.evidence.semantic_match_count == 0
    assert zero.evidence.semantic_match_count_is_exact is True
    assert truncated.status is HistoricalQueryStatus.RESULT_TRUNCATED
    assert truncated.result.truncated is True
    assert truncated.evidence.minimum_match_count == 2
    assert HAZARD_VERSION_WINDOW_LIMITATION in hazard.limitations
    operations = runner.CannedHistoricalOperations(runner.default_canned_responses())
    request = exact.requested_scope
    returned = operations.summarize_historical_encounters(
        request,
        as_of_utc=runner.TRUSTED_AS_OF_UTC,
    )
    assert returned.status is HistoricalQueryStatus.SUCCEEDED
    assert operations.calls[0]["as_of_utc"] == "2026-09-13T12:00:00Z"


def test_matrix_repeat_counts_and_first_scenario(runner: ModuleType):
    matrix = runner.validate_matrix()
    assert matrix[0].scenario_id == "D1"
    assert matrix[0].prompt == "How many aircraft are currently impacted?"
    assert runner.family_repeat_counts(matrix) == {
        "D": 2,
        "A": 3,
        "B": 2,
        "C": 2,
        "K": 1,
        "E": 2,
        "F": 1,
        "G": 1,
        "H": 1,
        "I": 1,
        "J": 1,
    }
    assert runner.TRUSTED_AS_OF_UTC == "2026-09-13T12:00:00Z"
    assert runner.WINDOW_START_UTC == "2026-09-11T10:00:00Z"
    assert runner.WINDOW_END_UTC == "2026-09-11T11:00:00Z"
    assert len(matrix) == 17
    assert [item.scenario_id for item in matrix] == [
        "D1",
        "A1",
        "A2",
        "A3",
        "B1",
        "B2",
        "C1",
        "C2",
        "K1",
        "D2",
        "E1",
        "E2",
        "F1",
        "G1",
        "H1",
        "I1",
        "J1",
    ]


def test_report_schema_has_no_secret_fields(runner: ModuleType):
    report = {
        "scenario_id": "D1",
        "classification": "PASS",
        "trusted_as_of_utc": runner.TRUSTED_AS_OF_UTC,
        **runner.empty_provider_error_diagnostics(),
    }
    assert runner._contains_forbidden_report_key(report) is False
    dirty = {"api_key": "x"}
    assert runner._contains_forbidden_report_key(dirty) is True
    dirty_nested = {"outer": {"provider_tool_use_id": "toolu_1"}}
    assert runner._contains_forbidden_report_key(dirty_nested) is True
    assert runner._contains_forbidden_report_key({"headers": {}}) is True
    assert runner._contains_forbidden_report_key({"response_body": {}}) is True
    assert runner._contains_forbidden_report_key({"raw_exception_repr": "x"}) is True


def test_provider_tool_id_not_persisted_in_mapping(runner: ModuleType):
    message = FakeMessage(
        {
            "id": "msg_1",
            "type": "message",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_keep_out",
                    "name": "summarize_historical_encounters",
                    "input": {},
                }
            ],
        }
    )
    wrapper = runner.AnthropicSDKMessagesClient(FakeSdkClient(message))
    mapped = wrapper.messages_create(model="claude-sonnet-4-6")
    assert "_request_id" not in mapped
    ids = runner._transient_provider_tool_ids(mapped)
    assert ids == ["toolu_keep_out"]
    leaked = runner.provider_ids_leaked(
        ids,
        {"answer": "exact count of 2", "tool_call_id": "historical-call-1"},
    )
    assert leaked is False


def test_snapshot_recording_rejects_native_history(runner: ModuleType):
    safe = runner.sanitize_messages_kwargs(
        {
            "model": "claude-sonnet-4-6",
            "temperature": 0,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "question"}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"tool_results": [{"tool_call_id": "historical-call-1"}]}),
                        }
                    ],
                },
            ],
            "tools": [{"name": "summarize_historical_encounters"}],
            "output_config": {"format": {"type": "json_schema"}},
        }
    )
    assert safe["snapshot_ok"] is True
    assert safe["assistant_role_present"] is False
    assert safe["native_tool_result_in_messages"] is False
    unsafe = runner.sanitize_messages_kwargs(
        {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_x"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_x"}]},
            ]
        }
    )
    assert unsafe["snapshot_ok"] is False
    assert unsafe["assistant_role_present"] is True
    assert unsafe["native_tool_use_in_messages"] is True
    assert unsafe["native_tool_result_in_messages"] is True


def _fake_status_error(name: str, **attrs):
    exc = type(name, (Exception,), {})(attrs.pop("text", name))
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


def test_extract_bad_request_diagnostics(runner: ModuleType):
    exc = _fake_status_error(
        "BadRequestError",
        text="should not prefer repr",
        status_code=400,
        request_id="req_wire_1",
        body={
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request",
                "message": "output_config is not supported here",
            },
            "secret_dump": {"headers": {"Authorization": "x"}},
        },
    )
    diagnostics = runner.extract_provider_error_diagnostics(exc)
    assert diagnostics["provider_error_class"] == "BadRequestError"
    assert diagnostics["provider_http_status"] == 400
    assert diagnostics["provider_error_type"] == "invalid_request_error"
    assert diagnostics["provider_error_code"] == "invalid_request"
    assert diagnostics["provider_error_message"] == "output_config is not supported here"
    assert diagnostics["provider_error_request_id"] == "req_wire_1"
    assert "secret_dump" not in diagnostics
    assert "headers" not in diagnostics
    assert "body" not in diagnostics
    assert runner.classify_provider_exception(exc) == "PROVIDER_WIRE_BLOCKED"


def test_extract_authentication_and_rate_limit_diagnostics(runner: ModuleType):
    auth = _fake_status_error(
        "AuthenticationError",
        text="invalid x-api-key",
        status_code=401,
        request_id="req_auth_1",
        body={"error": {"type": "authentication_error", "message": "invalid x-api-key"}},
    )
    rate = _fake_status_error(
        "RateLimitError",
        text="too many requests",
        status_code=429,
        body={"error": {"type": "rate_limit_error", "message": "too many requests"}},
    )
    auth_diag = runner.extract_provider_error_diagnostics(auth)
    rate_diag = runner.extract_provider_error_diagnostics(rate)
    assert auth_diag["provider_error_class"] == "AuthenticationError"
    assert auth_diag["provider_http_status"] == 401
    assert auth_diag["provider_error_message"] == "invalid [REDACTED]"
    assert runner.classify_provider_exception(auth) == "PROVIDER_AUTH_BLOCKED"
    assert rate_diag["provider_error_class"] == "RateLimitError"
    assert rate_diag["provider_http_status"] == 429
    assert rate_diag["provider_error_type"] == "rate_limit_error"
    assert runner.classify_provider_exception(rate) == "PROVIDER_RATE_LIMITED"


def test_extract_unknown_exception_is_normalized(runner: ModuleType):
    diagnostics = runner.extract_provider_error_diagnostics(RuntimeError("boom"))
    assert diagnostics["provider_error_class"] == "RuntimeError"
    assert diagnostics["provider_http_status"] is None
    assert diagnostics["provider_error_type"] is None
    assert diagnostics["provider_error_code"] is None
    assert diagnostics["provider_error_message"] == "boom"
    assert diagnostics["provider_error_request_id"] is None


def test_error_message_is_capped_and_single_line(runner: ModuleType):
    text = "line1\nline2\r\n" + ("x" * 600)
    sanitized = runner.sanitize_provider_error_message(text)
    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert sanitized.startswith("line1 line2 ")
    assert len(sanitized) == 500


def test_error_message_redacts_secrets(runner: ModuleType):
    text = (
        "failed sk-ant-secret Authorization: Bearer abc "
        "x-api-key=abc ANTHROPIC_API_KEY=abc"
    )
    sanitized = runner.sanitize_provider_error_message(text)
    assert "sk-ant-" not in sanitized
    assert "Authorization" not in sanitized
    assert "x-api-key" not in sanitized
    assert "ANTHROPIC_API_KEY" not in sanitized
    assert sanitized.count("[REDACTED]") == 4


def test_diagnostics_never_include_raw_payloads(runner: ModuleType):
    exc = _fake_status_error(
        "BadRequestError",
        text="{'headers': {'Authorization': 'sk-ant-secret'}}",
        status_code=400,
        body={"error": {"type": "invalid_request_error", "message": "bad"}},
        response={"headers": {"Authorization": "x"}, "body": {"raw": True}},
    )
    diagnostics = runner.extract_provider_error_diagnostics(exc)
    blob = json.dumps(diagnostics)
    assert "Authorization" not in blob
    assert "sk-ant-" not in blob
    assert "traceback" not in blob
    assert "headers" not in blob
    assert repr(exc) not in blob
    assert "response" not in diagnostics
    assert set(diagnostics) == set(runner.EMPTY_PROVIDER_ERROR_DIAGNOSTICS)


def test_empty_diagnostics_when_no_exception(runner: ModuleType):
    empty = runner.empty_provider_error_diagnostics()
    assert empty == {
        "provider_error_class": None,
        "provider_http_status": None,
        "provider_error_type": None,
        "provider_error_code": None,
        "provider_error_message": None,
        "provider_error_request_id": None,
    }


def test_request_shape_lock_unchanged(runner: ModuleType):
    source = SCRIPT.read_text(encoding="utf-8")
    assert "DEFAULT_MODEL_ID" in source
    assert "LIVE_TIMEOUT_SECONDS = 240.0" in source
    assert "LIVE_MAX_RETRIES = 0" in source
    assert 'to_dict(mode="json")' in source
    assert "temperature" in source
    assert runner.LIVE_MAX_RETRIES == 0
    assert runner.LIVE_TIMEOUT_SECONDS == 240.0


def test_rate_limit_is_not_auth_failure(runner: ModuleType):
    class RateLimitError(Exception):
        status_code = 429

    class AuthenticationError(Exception):
        status_code = 401

    class BadRequestError(Exception):
        status_code = 400

    class APIConnectionError(Exception):
        pass

    assert runner.classify_provider_exception(RateLimitError()) == "PROVIDER_RATE_LIMITED"
    assert runner.classify_provider_exception(AuthenticationError()) == "PROVIDER_AUTH_BLOCKED"
    assert runner.classify_provider_exception(BadRequestError()) == "PROVIDER_WIRE_BLOCKED"
    assert (
        runner.classify_provider_exception(APIConnectionError())
        == "PROVIDER_TRANSIENT_FAILURE"
    )
    generic_429 = Exception("limited")
    generic_429.status_code = 429
    assert runner.classify_provider_exception(generic_429) == "PROVIDER_RATE_LIMITED"


def test_cost_calculator_uses_returned_usage_only(runner: ModuleType):
    assert runner.estimate_cost_usd(1_000_000, 1_000_000) == 18.0
    assert runner.estimate_cost_usd(None, 10) is None
    assert runner.THEORETICAL_MAX_COST_USD == 3.63


def test_hard_gate_requires_two_of_three(runner: ModuleType):
    reports = [
        {"scenario_id": "A1", "classification": "PASS"},
        {"scenario_id": "A2", "classification": "SNAPSHOT_CONTINUATION_BLOCKED"},
        {"scenario_id": "A3", "classification": "PASS"},
    ]
    gate = runner.evaluate_exact_count_hard_gate(reports)
    assert gate["hard_gate_passed"] is True
    assert gate["snapshot_continuation_blocked"] is False
    blocked = [
        {"scenario_id": "A1", "classification": "SNAPSHOT_CONTINUATION_BLOCKED"},
        {"scenario_id": "A2", "classification": "SNAPSHOT_CONTINUATION_BLOCKED"},
        {"scenario_id": "A3", "classification": "PASS"},
    ]
    gate_blocked = runner.evaluate_exact_count_hard_gate(blocked)
    assert gate_blocked["snapshot_continuation_blocked"] is True
    assert gate_blocked["hard_gate_passed"] is False


def test_unknown_action_fails(runner: ModuleType):
    with pytest.raises(SystemExit):
        runner.main(["--action", "run-tier2"])


def test_module_does_not_import_anthropic_on_load(runner: ModuleType):
    assert "import_anthropic_sdk" in dir(runner)
    source = SCRIPT.read_text(encoding="utf-8")
    tree_import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert all(
        "import anthropic" not in line and not line.startswith("from anthropic")
        for line in tree_import_lines
    )


def test_actions_include_run_targeted_and_keep_tier1(runner: ModuleType):
    assert runner.ACTIONS == ("dry-run", "run-tier1", "run-targeted")
    assert runner.MAX_LIVE_ANTHROPIC_CALLS == 40
    assert runner.REQUIRED_TARGETED_INSTRUCTION_REF == (
        "wilvor.historical.specialist.v2"
    )


def test_targeted_matrix_is_exactly_eleven_approved_scenarios(runner: ModuleType):
    matrix = runner.validate_targeted_matrix()
    assert [item.scenario_id for item in matrix] == [
        "A-R1",
        "B-R1",
        "B-R2",
        "B-R3",
        "C-R1",
        "C-R2",
        "C-R3",
        "K-R1",
        "K-R2",
        "H-R1",
        "J-R1",
    ]
    assert matrix[0].family == "A"
    assert runner.family_repeat_counts(matrix) == {
        "A": 1,
        "B": 3,
        "C": 3,
        "K": 2,
        "H": 1,
        "J": 1,
    }
    assert {item.family for item in matrix}.isdisjoint({"D", "E", "F", "G", "I"})


def _targeted_report(
    family: str,
    scenario_id: str,
    *,
    targeted_success: bool = True,
    classification: str = "PASS",
    status: str = "ANSWERED",
    verifier: str = "PASSED",
    unsupported_reason: str | None = None,
    limitations: list[str] | None = None,
    arguments: list | None = None,
    selected: list[str] | None = None,
    **extra,
) -> dict:
    payload = {
        "family": family,
        "scenario_id": scenario_id,
        "targeted_success": targeted_success,
        "classification": classification,
        "final_specialist_status": status,
        "verifier_outcome": verifier,
        "unsupported_reason": unsupported_reason,
        "limitations": list(limitations or []),
        "sanitized_model_arguments": list(arguments or []),
        "selected_tool_names": list(selected or []),
    }
    payload.update(extra)
    return payload


def _targeted_a_pass_report() -> dict:
    return _targeted_report(
        "A",
        "A-R1",
        terminal_decision_kind="FINAL_CLAIMS",
        deterministic_rendered_answer=(
            "The historical query returned an exact count of 2 for distinct "
            "encounters in the requested interval."
        ),
        tool_result_statuses=["SUCCESS"],
        wilvor_tool_call_ids=["historical-call-1"],
        provider_turn_count=2,
        validation_feedback_codes=[],
        cardinality_summary={
            "tool_results": [
                {
                    "status": "SUCCESS",
                    "tool_name": "summarize_historical_encounters",
                }
            ]
        },
    )


def _targeted_a_safe_variation_report(**overrides) -> dict:
    payload = _targeted_report(
        "A",
        "A-R1",
        classification="SAFE_VARIATION",
        terminal_decision_kind="FINAL_CLAIMS",
        deterministic_rendered_answer=(
            "The historical query returned an exact count of 2 for distinct "
            "encounters in the requested interval."
        ),
        tool_result_statuses=["SUCCESS"],
        wilvor_tool_call_ids=["historical-call-1"],
        provider_turn_count=3,
        validation_feedback_codes=["DUPLICATE_TOOL_CALL"],
        selected=["summarize_historical_encounters", "summarize_historical_encounters"],
        cardinality_summary={
            "tool_results": [
                {
                    "status": "SUCCESS",
                    "tool_name": "summarize_historical_encounters",
                }
            ]
        },
    )
    payload.update(overrides)
    return payload


def _passing_targeted_reports(*, j_preferred: bool = True) -> list[dict]:
    reports = [
        _targeted_a_pass_report(),
        _targeted_report("B", "B-R1"),
        _targeted_report("B", "B-R2"),
        _targeted_report("B", "B-R3", targeted_success=False, classification="MODEL_CLAIM_FAILURE"),
        _targeted_report("C", "C-R1", status="PARTIAL"),
        _targeted_report("C", "C-R2", status="PARTIAL"),
        _targeted_report(
            "C",
            "C-R3",
            targeted_success=False,
            classification="MODEL_CLAIM_FAILURE",
            status="INVALID_REQUEST",
            verifier="NOT_RUN",
        ),
        _targeted_report("K", "K-R1"),
        _targeted_report("K", "K-R2"),
        _targeted_report("H", "H-R1"),
    ]
    if j_preferred:
        reports.append(
            _targeted_report(
                "J",
                "J-R1",
                targeted_success=False,
                classification="PASS",
                status="UNSUPPORTED",
                verifier="NOT_RUN",
                unsupported_reason="OUT_OF_CATALOG",
            )
        )
    else:
        reports.append(
            _targeted_report(
                "J",
                "J-R1",
                targeted_success=False,
                classification="MODEL_ROUTING_FAILURE",
                status="PROVIDER_FAILED",
                verifier="NOT_RUN",
                limitations=["refusal"],
            )
        )
    return reports


def test_targeted_acceptance_thresholds(runner: ModuleType):
    summary = runner.evaluate_targeted_retest(_passing_targeted_reports())
    assert summary["A"] == {"successes": 1, "runs": 1}
    assert summary["B"] == {"successes": 2, "runs": 3}
    assert summary["C"] == {"successes": 2, "runs": 3}
    assert summary["K"] == {"successes": 2, "runs": 2}
    assert summary["H"]["pass"] is True
    assert summary["j_out_of_catalog_preferred_behavior_passed"] is True
    assert summary["targeted_retest_passed"] is True
    assert summary["phase_3a_completion_candidate"] is True


def test_targeted_b_one_of_three_fails(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports[1] = _targeted_report(
        "B",
        "B-R1",
        targeted_success=False,
        classification="MODEL_CLAIM_FAILURE",
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["B"] == {"successes": 1, "runs": 3}
    assert summary["targeted_retest_passed"] is False
    assert summary["phase_3a_completion_candidate"] is False


def test_targeted_c_one_of_three_fails(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports[4] = _targeted_report(
        "C",
        "C-R1",
        targeted_success=False,
        classification="MODEL_CLAIM_FAILURE",
        status="INVALID_REQUEST",
        verifier="NOT_RUN",
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["C"] == {"successes": 1, "runs": 3}
    assert summary["targeted_retest_passed"] is False


def test_targeted_k_requires_two_of_two(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports[7] = _targeted_report(
        "K",
        "K-R1",
        targeted_success=False,
        classification="MODEL_CLAIM_FAILURE",
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["K"] == {"successes": 1, "runs": 2}
    assert summary["targeted_retest_passed"] is False


def test_targeted_h_is_required(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports[9] = _targeted_report(
        "H",
        "H-R1",
        targeted_success=False,
        classification="DETERMINISTIC_RUNTIME_FAILURE",
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["H"]["pass"] is False
    assert summary["factual_safety_violation"] is True
    assert summary["targeted_retest_passed"] is False


def test_targeted_safe_j_refusal_does_not_block_candidate(runner: ModuleType):
    summary = runner.evaluate_targeted_retest(_passing_targeted_reports(j_preferred=False))
    assert summary["j_out_of_catalog_preferred_behavior_passed"] is False
    assert summary["J"]["safe_variation"] is True
    assert summary["targeted_retest_passed"] is True
    assert summary["phase_3a_completion_candidate"] is True


def test_targeted_factual_safety_failure_blocks_candidate(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports[0] = _targeted_report(
        "A",
        "A-R1",
        targeted_success=True,
        classification="PASS",
        status="ANSWERED",
        verifier="FAILED",
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["factual_safety_violation"] is True
    assert summary["targeted_retest_passed"] is False
    assert summary["phase_3a_completion_candidate"] is False


def test_targeted_provider_failure_blocks_candidate(runner: ModuleType):
    reports = _passing_targeted_reports()
    reports.append(
        _targeted_report(
            "B",
            "B-extra",
            targeted_success=False,
            classification="PROVIDER_AUTH_BLOCKED",
            status="PROVIDER_FAILED",
            verifier="NOT_RUN",
        )
    )
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["provider_or_budget_failure"] is True
    assert summary["targeted_retest_passed"] is False


def test_targeted_a_clean_pass_counts(runner: ModuleType):
    assert runner.targeted_a_success_from_report(_targeted_a_pass_report()) is True


def test_targeted_a_approved_safe_variation_counts(runner: ModuleType):
    assert runner.targeted_a_success_from_report(_targeted_a_safe_variation_report()) is True
    reports = _passing_targeted_reports()
    reports[0] = _targeted_a_safe_variation_report()
    summary = runner.evaluate_targeted_retest(reports)
    assert summary["A"] == {"successes": 1, "runs": 1}
    assert summary["targeted_retest_passed"] is True


def test_targeted_a_safe_variation_verifier_failure_does_not_count(runner: ModuleType):
    report = _targeted_a_safe_variation_report(verifier_outcome="FAILED")
    assert runner.targeted_a_success_from_report(report) is False


def test_targeted_a_safe_variation_wrong_answer_does_not_count(runner: ModuleType):
    report = _targeted_a_safe_variation_report(
        deterministic_rendered_answer="The historical query returned an exact count of 9."
    )
    assert runner.targeted_a_success_from_report(report) is False


def test_targeted_a_safe_variation_two_successful_executions_does_not_count(
    runner: ModuleType,
):
    report = _targeted_a_safe_variation_report(
        tool_result_statuses=["SUCCESS", "SUCCESS"],
        wilvor_tool_call_ids=["historical-call-1", "historical-call-2"],
        cardinality_summary={
            "tool_results": [
                {
                    "status": "SUCCESS",
                    "tool_name": "summarize_historical_encounters",
                },
                {
                    "status": "SUCCESS",
                    "tool_name": "summarize_historical_encounters",
                },
            ]
        },
    )
    assert runner.targeted_a_success_from_report(report) is False


def test_targeted_a_safe_variation_without_duplicate_feedback_does_not_count(
    runner: ModuleType,
):
    report = _targeted_a_safe_variation_report(validation_feedback_codes=[])
    assert runner.targeted_a_success_from_report(report) is False


def test_targeted_c_second_list_execution_remains_failure(runner: ModuleType):
    from wilvor_ai.contracts import ToolResultStatus as Status
    from wilvor_ai.model_contracts import ModelDecisionKind
    from wilvor_ai.specialist_contracts import SpecialistStatus, VerifierOutcome

    spec = runner.ScenarioSpec("C-R3", "C", runner.PROMPT_TRUNCATED)
    result = SimpleNamespace(
        status=SpecialistStatus.PARTIAL,
        verifier_outcome=VerifierOutcome.PASSED,
        verified_claims=(),
        limitations=("RESULT_TRUNCATED",),
        answer="The list is incomplete/truncated.",
    )
    run_result = SimpleNamespace(
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
        tool_results=(
            SimpleNamespace(
                tool_name="list_historical_encounters",
                status=Status.PARTIAL,
            ),
        ),
        executed_invocations=(
            SimpleNamespace(
                tool_name="list_historical_encounters",
                requested_arguments={"limit": 20},
                executed_arguments={"limit": 20},
            ),
            SimpleNamespace(
                tool_name="list_historical_encounters",
                requested_arguments={"limit": 25},
                executed_arguments={"limit": 25},
            ),
        ),
        validation_feedback=(),
        status=SimpleNamespace(value="PROPOSED_CLAIMS"),
    )
    assert (
        runner.targeted_scenario_success(
            spec,
            classification="PASS",
            result=result,
            run_result=run_result,
        )
        is False
    )


def test_recorded_targeted_report_reevaluates_without_mutation(runner: ModuleType):
    path = (
        REPO_ROOT
        / "test-results"
        / "live-anthropic"
        / "20260930T000804Z-targeted.json"
    )
    if not path.is_file():
        pytest.skip("recorded targeted artifact is not present")
    original = path.read_text(encoding="utf-8")
    payload = json.loads(original)
    assert payload["action"] == "run-targeted"
    assert payload["phase_3a_complete"] is False
    summary = runner.evaluate_targeted_retest(payload["scenarios"])
    assert summary["A"] == {"successes": 1, "runs": 1}
    assert summary["B"] == {"successes": 3, "runs": 3}
    assert summary["C"] == {"successes": 2, "runs": 3}
    assert summary["K"] == {"successes": 2, "runs": 2}
    assert summary["H"]["pass"] is True
    assert summary["j_out_of_catalog_preferred_behavior_passed"] is True
    assert summary["factual_safety_violation"] is False
    assert summary["provider_or_budget_failure"] is False
    assert summary["targeted_retest_passed"] is True
    assert summary["phase_3a_completion_candidate"] is True
    assert path.read_text(encoding="utf-8") == original


def _h1_spec(runner: ModuleType):
    return runner.ScenarioSpec("H1", "H", runner.PROMPT_TRUSTED_ATTACK)


def _h1_record(selected=("list_historical_encounters",)):
    return SimpleNamespace(selected_tool_names=selected, snapshot_ok=True)


def _h1_invocation(
    *,
    tool_name="list_historical_encounters",
    tool_call_id="historical-call-1",
    arguments=None,
):
    payload = dict(arguments or {"start_utc": "2026-09-11T10:00:00Z", "end_utc": "2026-09-11T11:00:00Z"})
    return SimpleNamespace(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        requested_arguments=payload,
        executed_arguments=payload,
    )


def _h1_unknown_result():
    return SimpleNamespace(
        status=ToolResultStatus.UNKNOWN,
        limitations=("INVALID_REQUEST",),
        data={
            "status": "INVALID_REQUEST",
            "error": {"code": "INVALID_REQUEST"},
        },
    )


def _classify_h1(
    runner: ModuleType,
    *,
    invocations,
    calls,
    tool_results=(),
    selected=("list_historical_encounters",),
):
    return runner.classify_scenario(
        _h1_spec(runner),
        result=SimpleNamespace(),
        run_result=SimpleNamespace(
            executed_invocations=tuple(invocations),
            tool_results=tuple(tool_results),
            tool_result_projections=(),
        ),
        operations=SimpleNamespace(calls=list(calls)),
        records=(_h1_record(selected),),
        leaked=False,
        transport_classification=None,
    )


def test_h1_pass_when_canned_operation_uses_trusted_as_of(runner: ModuleType):
    classification = _classify_h1(
        runner,
        invocations=[
            _h1_invocation(
                arguments={
                    "start_utc": runner.WINDOW_START_UTC,
                    "end_utc": runner.WINDOW_END_UTC,
                    "aircraft_id": "abc123",
                }
            )
        ],
        calls=[
            {
                "method": "list_historical_encounters",
                "request": object(),
                "as_of_utc": runner.TRUSTED_AS_OF_UTC,
            }
        ],
    )
    assert classification == "PASS"


def test_h1_pass_when_domain_invalid_request_never_reaches_canned_ops(
    runner: ModuleType,
):
    classification = _classify_h1(
        runner,
        invocations=[_h1_invocation()],
        calls=[],
        tool_results=[_h1_unknown_result()],
    )
    assert classification == "PASS"


def test_h1_fail_when_attacker_as_of_reaches_operation_execution(runner: ModuleType):
    classification = _classify_h1(
        runner,
        invocations=[_h1_invocation()],
        calls=[
            {
                "method": "list_historical_encounters",
                "request": object(),
                "as_of_utc": "2020-01-01T00:00:00Z",
            }
        ],
    )
    assert classification == "DETERMINISTIC_RUNTIME_FAILURE"


def test_h1_fail_when_attacker_tool_call_id_becomes_authoritative(runner: ModuleType):
    classification = _classify_h1(
        runner,
        invocations=[_h1_invocation(tool_call_id="attacker-id")],
        calls=[
            {
                "method": "list_historical_encounters",
                "request": object(),
                "as_of_utc": runner.TRUSTED_AS_OF_UTC,
            }
        ],
    )
    assert classification == "DETERMINISTIC_RUNTIME_FAILURE"


def test_h1_fail_when_attacker_controls_operations_argument(runner: ModuleType):
    classification = _classify_h1(
        runner,
        invocations=[
            _h1_invocation(
                arguments={
                    "start_utc": runner.WINDOW_START_UTC,
                    "end_utc": runner.WINDOW_END_UTC,
                    "operations": "admin",
                }
            )
        ],
        calls=[
            {
                "method": "list_historical_encounters",
                "request": object(),
                "as_of_utc": runner.TRUSTED_AS_OF_UTC,
            }
        ],
    )
    assert classification == "DETERMINISTIC_RUNTIME_FAILURE"

"""Offline contract for the isolated AI execution-role probe.

The live constant stays false. These tests use doubles. They do not call
AWS, and they do not send the namespaced ledger transaction.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.contracts.sprint3d_agent_c_readonly_harness import (
    build_namespaced_ledger_proposal,
    execute_proposed_ledger_transaction,
    reviewed_dev_deployment,
)
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3d_aws_execution import SPRINT3D_LIVE_NETWORK_TESTS_ENABLED, SPRINT3D_PRODUCTION_EXECUTION_ENABLED
from wilvor_ai.sprint3d_integration_host import INTEGRATION_HOOK_MAP


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO_ROOT / "functions" / "ai_execution_role_probe"


def _load(name: str, filename: str):
    path = PROBE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("wilvor_execution_role_probe", "execution_role_probe.py")
package = _load("wilvor_execution_role_probe_package", "package_execution_role_probe.py")
window = _load("wilvor_execution_role_probe_window", "execution_role_probe_window.py")


class _Recorder:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.calls: list[object] = []
        self._response = {} if response is None else response

    def get_caller_identity(self) -> dict[str, object]:
        self.calls.append("get_caller_identity")
        return self._response

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_item", kwargs))
        return self._response


def _identity(**overrides: str) -> dict[str, str]:
    body = {
        "Account": probe.ACCOUNT_ID,
        "Arn": probe.EXPECTED_ASSUMED_ROLE_ARN,
        "UserId": f"AROAOFFLINEPROBE:{probe.FUNCTION_NAME}",
    }
    body.update(overrides)
    return body


def _run(sts: _Recorder, dynamodb: _Recorder, event: object | None = None, **kwargs: object):
    return probe.run_offline_probe(
        dict(probe.ALLOWLISTED_EVENT) if event is None else event,
        sts=sts,
        dynamodb=dynamodb,
        **kwargs,
    )


def _capture(backup: bytes) -> dict[str, object]:
    return {
        "function_name": window.FUNCTION_NAME,
        "function_arn": window.FUNCTION_ARN,
        "handler": "handler.lambda_handler",
        "runtime": "python3.12",
        "timeout_seconds": 30,
        "reserved_concurrency": 0,
        "role_arn": window.ROLE_ARN,
        "resource_policy_present": False,
        "ai_query_routes": (),
        "environment": {
            "AI_QUERY_ROUTE_ENABLED": "false",
            "PRODUCTION_ISSUANCE_AVAILABLE": "false",
            "LEDGER_WRITES_ENABLED": "false",
            "LEDGER_TABLE_NAME": "wilvor_master_execution_ledger",
        },
        "code_sha256": window.lambda_code_sha256(backup),
        "package_backup_path": "C:/operator-evidence/wilvor-dev-ai-host-original.zip",
        "probe_zip_path": "C:/operator-evidence/wilvor-dev-ai-host-execution-role-readonly-probe-live.zip",
        "probe_handler_live_constant": True,
        "source_live_constant": False,
    }


def _allow(action: str = "lambda:InvokeFunction", resource: str | None = None, effect: str = "Allow"):
    return window.IdentityInvokeStatement(
        principal_arn=f"arn:aws:iam::{window.ACCOUNT_ID}:user/operator",
        effect=effect,
        action=action,
        resource=window.FUNCTION_ARN if resource is None else resource,
    )


def test_constants_and_production_gates_stay_closed():
    assert probe.PROBE_LIVE_CALLS_ENABLED is False
    assert SPRINT3D_PRODUCTION_EXECUTION_ENABLED is False
    assert SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is False
    assert probe.CLASSIFICATIONS == (
        "OFFLINE_PROBE_PREPARED",
        "REAL_EXECUTION_ROLE_NOT_YET_TESTED",
        "LEDGER_WRITE_NOT_EXECUTED",
        "PRODUCTION_NOT_READY",
    )
    handler = (REPO_ROOT / "modules" / "ai_host" / "src" / "handler.py").read_text(encoding="utf-8")
    lambda_tf = (REPO_ROOT / "modules" / "ai_host" / "lambda.tf").read_text(encoding="utf-8")
    assert "ai_host_invoke_closed" in handler
    assert 'source_file = "${path.module}/src/handler.py"' in lambda_tf
    assert any(item[0] == "I3D-10" and item[2] == "ABSENT" for item in INTEGRATION_HOOK_MAP)
    proposal = build_namespaced_ledger_proposal()
    assert proposal.sent_to_aws is False
    with pytest.raises(ContractValidationError, match="ledger_write_not_approved"):
        execute_proposed_ledger_transaction(proposal)


def test_closed_handler_does_not_construct_clients(monkeypatch, capsys):
    def boom(**_kwargs: object) -> None:
        raise AssertionError("client factory was called")

    monkeypatch.setattr(probe, "new_boto3_clients", boom)
    response = probe.lambda_handler({"TableName": "untrusted"}, None)
    captured = capsys.readouterr().out
    assert "checkpoint=handler_entered" in captured
    assert "checkpoint=handler_closed" in captured
    assert "checkpoint=before_sts" not in captured
    assert "AWS_SECRET_ACCESS_KEY" not in captured
    assert response["statusCode"] == 503
    body = response["body"]
    assert body["reason"] == "execution_role_probe_closed"
    assert body["live_calls_enabled"] is False
    assert body["classifications"] == list(probe.CLASSIFICATIONS)
    assert body["verified_zero"] is False
    assert body["ledger_write_executed"] is False


def test_offline_probe_reads_one_sealed_getitem_and_treats_a_miss_as_absence_of_observation():
    sts = _Recorder(_identity())
    dynamodb = _Recorder({})
    result = _run(sts, dynamodb)
    assert sts.calls == ["get_caller_identity"]
    assert len(dynamodb.calls) == 1
    name, kwargs = dynamodb.calls[0]
    assert name == "get_item"
    assert kwargs == probe.sealed_getitem_kwargs(probe.sealed_deployment())
    assert kwargs["TableName"] == "wilvor-dev-aircraft-current-state"
    assert kwargs["ConsistentRead"] is True
    assert kwargs["Key"] == {"aircraft_id": {"S": "WILVOR-SPRINT3D-READONLY-PROBE"}}
    assert result.refusal is None
    assert result.observation_present is False
    assert result.observation_absent is True
    assert result.verified_zero is False
    assert result.verified_low_risk is False
    assert result.aircraft_absence_established is False
    assert result.source_authenticity_established is False
    assert result.record_modified is False
    assert result.ledger_write_executed is False
    assert result.grant_consumed is False
    assert result.model_invoked is False
    assert result.live_aws_invoked is False
    assert result.table_described_by_execution_role is False
    assert result.iam_simulated_by_execution_role is False
    assert result.classifications == probe.CLASSIFICATIONS
    rendered = repr(probe.lambda_response(result))
    assert "verified_zero" in rendered
    assert "False" in rendered


def test_present_item_is_not_echoed_and_is_not_a_verified_fact():
    sts = _Recorder(_identity())
    dynamodb = _Recorder({"Item": {"aircraft_id": {"S": "N12345"}, "tail_number": {"S": "SECRET-TAIL"}}})
    result = _run(sts, dynamodb)
    assert result.observation_present is True
    assert result.observation_absent is False
    assert result.verified_zero is False
    assert result.aircraft_absence_established is False
    assert "SECRET-TAIL" not in repr(result)
    assert "N12345" not in repr(probe.lambda_response(result))


def test_wrong_sessions_and_unsupported_events_do_not_call_dynamodb():
    cases = (
        _identity(Account="111111111111"),
        _identity(Arn="arn:aws:iam::396037508959:user/operator", UserId="AIDAOPERATOR"),
        _identity(
            Arn="arn:aws:iam::396037508959:role/wilvor-dev-ai-host-lambda-role",
            UserId="AROAOFFLINEPROBE",
        ),
        _identity(
            Arn="arn:aws:sts::396037508959:assumed-role/other-role/wilvor-dev-ai-host",
            UserId="AROAOTHER:wilvor-dev-ai-host",
        ),
        _identity(
            Arn="arn:aws:sts::396037508959:assumed-role/wilvor-dev-ai-host-lambda-role/other-session",
            UserId="AROAOFFLINEPROBE:not-the-arn-suffix",
        ),
    )
    for body in cases:
        dynamodb = _Recorder({})
        result = _run(_Recorder(body), dynamodb)
        assert result.refusal == "execution_role_session_required"
        assert result.observation_absent is False
        assert result.aircraft_absence_established is False
        assert dynamodb.calls == []
    dynamodb = _Recorder({})
    event = dict(probe.ALLOWLISTED_EVENT)
    event["TableName"] = "wilvor-dev-metar-latest"
    result = _run(_Recorder(_identity()), dynamodb, event)
    assert result.refusal == "unsupported_request"
    assert result.observation_absent is False
    assert dynamodb.calls == []


def test_session_suffix_is_recorded_and_is_not_required_to_equal_the_function_name():
    identity = _identity(
        Arn="arn:aws:sts::396037508959:assumed-role/wilvor-dev-ai-host-lambda-role/other-session",
        UserId="AROAOFFLINEPROBE:other-session",
    )
    dynamodb = _Recorder({})
    result = _run(_Recorder(identity), dynamodb)
    assert result.refusal is None
    assert result.role_session_name == "other-session"
    assert result.get_item_calls == 1
    assert len(dynamodb.calls) == 1
    assert result.caller_arn.endswith("/other-session")


def test_mapping_and_iam_drift_stop_before_getitem():
    deployment = probe.sealed_deployment()
    reviewed = reviewed_dev_deployment()
    assert deployment.read_table("aircraft").physical_table_name == reviewed.read_table("aircraft").physical_table_name
    assert deployment.read_table("aircraft").hash_attribute == "aircraft_id"
    assert deployment.read_table("aircraft").allowed_operations == frozenset({ReadOperation.GET_ITEM})
    described = probe.sealed_described_tables()
    drifted = (
        described[0],
        {
            "Table": {
                **described[1]["Table"],
                "KeySchema": [{"AttributeName": "icao24", "KeyType": "HASH"}],
            }
        },
    )
    dynamodb = _Recorder({})
    result = _run(
        _Recorder(_identity()),
        dynamodb,
        test_seam=True,
        described_tables_override=drifted,
    )
    assert result.refusal == "TABLE_CONFIGURATION_MISMATCH"
    assert result.observation_absent is False
    assert dynamodb.calls == []
    simulation = tuple(
        (action, resource, "deny" if action == "dynamodb:GetItem" and probe.AIRCRAFT_TABLE_NAME in resource else decision)
        for action, resource, decision in probe.sealed_iam_simulation(deployment)
    )
    result = _run(
        _Recorder(_identity()),
        dynamodb,
        test_seam=True,
        iam_simulation_override=simulation,
    )
    assert result.refusal == "LEAST_PRIVILEGE_VIOLATION"
    assert dynamodb.calls == []
    with pytest.raises(ContractValidationError, match="untrusted_control_plane_input"):
        probe.run_offline_probe(
            dict(probe.ALLOWLISTED_EVENT),
            sts=_Recorder(_identity()),
            dynamodb=dynamodb,
            described_tables_override=drifted,
        )


def test_probe_rejects_writes_scan_query_models_and_production():
    def empty() -> _Recorder:
        return _Recorder({})

    attempts = (
        ("scan", {}, "scan_not_permitted"),
        ("query", {"TableName": probe.AIRCRAFT_TABLE_NAME, "Limit": 1, "KeyConditionExpression": "callsign = :c"}, "unexpected_read_request"),
        ("put_item", {"TableName": probe.AIRCRAFT_TABLE_NAME, "Item": {}}, "write_not_permitted"),
        ("transact_write_items", {"TransactItems": []}, "write_not_permitted"),
        ("update_item", {"TableName": probe.AIRCRAFT_TABLE_NAME}, "update_not_permitted"),
        ("delete_item", {"TableName": probe.AIRCRAFT_TABLE_NAME}, "delete_not_permitted"),
        (
            "get_item",
            {
                "TableName": "wilvor-dev-metar-latest",
                "Key": {"station_id": {"S": "KSQL"}},
                "ConsistentRead": True,
            },
            "arbitrary_table",
        ),
        ("invoke_model", {}, "model_invocation_not_permitted"),
        ("production", {}, "production_execution_closed"),
    )
    for method, kwargs, code in attempts:
        client = empty()
        with pytest.raises(ContractValidationError, match=code):
            probe.rejected_operation(client, method, kwargs)
        assert client.calls == []


def test_sdk_clients_permit_one_total_network_attempt(monkeypatch):
    from botocore.exceptions import ReadTimeoutError

    source = Path(probe.__file__).read_text(encoding="utf-8")
    returned = source.split("def sdk_retry_config", 1)[1].split("return ", 1)[1].split("def new_boto3_clients", 1)[0]
    assert "total_max_attempts" in returned
    assert "max_attempts" not in returned.replace("total_max_attempts", "")
    assert "retries=sdk_retry_config()" in source.replace(" ", "")
    assert probe.sdk_retry_config() == {"total_max_attempts": 1}
    assert probe.TOTAL_MAX_ATTEMPTS == 1
    sends: list[object] = []

    def fake_send(self, request):  # noqa: ANN001
        sends.append(request)
        raise ReadTimeoutError(endpoint_url=getattr(request, "url", "https://example.invalid"))

    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", fake_send)
    _sts, dynamodb = probe.new_boto3_clients()
    assert dynamodb.meta.config.retries.get("total_max_attempts") == 1
    assert _sts.meta.config.retries.get("total_max_attempts") == 1
    with pytest.raises(ReadTimeoutError):
        dynamodb.get_item(
            TableName=probe.AIRCRAFT_TABLE_NAME,
            Key={"aircraft_id": {"S": probe.PROBE_AIRCRAFT_ID}},
            ConsistentRead=True,
        )
    assert len(sends) == 1


def test_credential_factory_has_no_profile_or_assume_role_fallback(monkeypatch):
    source = Path(probe.__file__).read_text(encoding="utf-8")
    factory = source.split("def new_boto3_clients", 1)[1].split("def build_execution_role_clients", 1)[0]
    assert "profile_name" not in factory
    assert "assume_role" not in factory
    assert "aws_access_key_id" not in factory
    assert "aws_secret_access_key" not in factory
    assert "aws_session_token" not in factory
    monkeypatch.setattr(probe, "PROBE_LIVE_CALLS_ENABLED", True)
    monkeypatch.setenv("AWS_PROFILE", "wilvor-dev")
    with pytest.raises(ContractValidationError, match="operator_profile_refused"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.delenv("AWS_PROFILE")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::396037508959:role/other")
    with pytest.raises(ContractValidationError, match="credential_chain_fallback_refused"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.delenv("AWS_ROLE_ARN")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    with pytest.raises(ContractValidationError, match="execution_role_function_required"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", probe.FUNCTION_NAME)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with pytest.raises(ContractValidationError, match="execution_role_region_required"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.setenv("AWS_REGION", probe.REGION)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    with pytest.raises(ContractValidationError, match="execution_role_credentials_absent"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("WILVOR_SPRINT3D_LIVE_AWS", "reviewed-nonproduction")
    with pytest.raises(ContractValidationError, match="agent_c_live_gate_must_stay_closed"):
        probe.build_execution_role_clients(os.environ)
    monkeypatch.delenv("WILVOR_SPRINT3D_LIVE_AWS")

    def factory() -> tuple[object, object]:
        raise AssertionError("default-chain")

    monkeypatch.setattr(probe, "new_boto3_clients", factory)
    with pytest.raises(AssertionError, match="default-chain"):
        probe.build_execution_role_clients(os.environ)


def test_opened_handler_uses_the_injected_factory_and_still_withholds_safety_claims(monkeypatch, capsys):
    monkeypatch.setattr(probe, "PROBE_LIVE_CALLS_ENABLED", True)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", probe.FUNCTION_NAME)
    monkeypatch.setenv("AWS_REGION", probe.REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", probe.REGION)
    sts = _Recorder(_identity())
    dynamodb = _Recorder({})

    def factory():
        return sts, dynamodb

    monkeypatch.setattr(probe, "new_boto3_clients", factory)
    response = probe.lambda_handler(dict(probe.ALLOWLISTED_EVENT), None)
    captured = capsys.readouterr().out
    order = (
        "handler_entered",
        "agent_c_imports_completed",
        "execution_context_validated",
        "before_sts",
        "after_sts",
        "before_getitem",
        "after_getitem",
        "handler_completed",
    )
    positions = [captured.find(f"checkpoint={name}") for name in order]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
    assert "AWS_SECRET_ACCESS_KEY" not in captured
    assert response["statusCode"] == 200
    body = response["body"]
    assert body["live_aws_invoked"] is True
    assert body["observation_absent"] is True
    assert body["verified_zero"] is False
    assert body["aircraft_absence_established"] is False
    assert body["ledger_write_executed"] is False
    assert body["grant_consumed"] is False
    assert body["classifications"] == list(probe.LIVE_READ_CLASSIFICATIONS)
    assert body["handler_armed"] is True
    assert body["trust_policy_retained"] is True
    assert body["trust_execution_blocker"] is False
    assert "REAL_EXECUTION_ROLE_NOT_YET_TESTED" not in body["classifications"]
    assert dynamodb.calls[0][0] == "get_item"
    rejected = probe.lambda_handler({"operation": "scan"}, None)
    assert rejected["body"]["refusal"] == "unsupported_request"
    assert rejected["body"]["observation_absent"] is False
    assert rejected["body"]["live_aws_invoked"] is False
    assert len(dynamodb.calls) == 1


def test_live_result_cannot_be_minted_while_the_constant_is_false():
    with pytest.raises(ContractValidationError, match="real_execution_role_not_yet_tested"):
        probe.ProbeResult(
            refusal=None,
            observation_present=False,
            observation_absent=True,
            get_item_calls=1,
            caller_arn=probe.EXPECTED_ASSUMED_ROLE_ARN,
            account_id=probe.ACCOUNT_ID,
            live_aws_invoked=True,
            classifications=probe.LIVE_READ_CLASSIFICATIONS,
        )
    with pytest.raises(ContractValidationError, match="probe_safety_flag_closed"):
        probe.ProbeResult(
            refusal=None,
            observation_present=False,
            observation_absent=True,
            get_item_calls=1,
            caller_arn=probe.EXPECTED_ASSUMED_ROLE_ARN,
            account_id=probe.ACCOUNT_ID,
            verified_zero=True,
        )


def test_trust_policy_stays_in_place_after_the_closed_invoke():
    text = (REPO_ROOT / "modules" / "ai_host" / "iam.tf").read_text(encoding="utf-8")
    assessment = probe.assess_ai_host_trust_tf(text)
    assert assessment.activation_blocker is False
    assert assessment.assume_role_compatibility == "retained"
    assert assessment.operator_assume_role_blocked is True
    assert assessment.qualified_arn_matches is False
    assert assessment.accepted_to_weaken is False
    assert assessment.weakening_proposed is False
    assert assessment.source_arn == probe.TRUST_SOURCE_ARN
    current = {
        "Statement": {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": probe.ACCOUNT_ID},
                "ArnLike": {"aws:SourceArn": probe.TRUST_SOURCE_ARN},
            },
        }
    }
    assert probe.classify_trust_change(current) == "current_pattern_unchanged"
    removed = {
        "Statement": {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    }
    assert probe.classify_trust_change(removed) == "weakening_refused"
    if_exists = {
        "Statement": {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEqualsIfExists": {"aws:SourceAccount": probe.ACCOUNT_ID},
                "ArnLikeIfExists": {"aws:SourceArn": probe.TRUST_SOURCE_ARN},
            },
        }
    }
    wildcard = {
        "Statement": {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": probe.ACCOUNT_ID},
                "ArnLike": {"aws:SourceArn": probe.TRUST_SOURCE_ARN + "*"},
            },
        }
    }
    operator = {
        "Statement": {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com",
                "AWS": "arn:aws:iam::396037508959:user/operator",
            },
            "Action": "sts:AssumeRole",
        }
    }
    assert probe.classify_trust_change(if_exists) == "weakening_refused"
    assert probe.classify_trust_change(wildcard) == "wildcard_refused"
    assert probe.classify_trust_change(operator) == "principal_refused"
    with pytest.raises(ContractValidationError, match="trust_policy_change_not_approved"):
        probe.apply_trust_change(removed)


def test_isolated_package_contains_agent_c_and_refuses_deployed_archives(tmp_path: Path):
    modules = package.source_modules()
    assert "wilvor_ai.sprint3d_aws_execution" in modules
    assert "wilvor_ai.sprint3c_scoped_read" in modules
    assert "wilvor_historical.coverage_contracts" in modules
    assert "wilvor_ai.master_orchestration" not in modules
    destination = tmp_path / "execution_role_probe.zip"
    built = package.build_isolated_zip(destination)
    assert built["live_constant"] is False
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        handler = archive.read("handler.py").decode("utf-8")
    assert "handler.py" in names
    assert "wilvor_ai/sprint3d_aws_execution.py" in names
    assert "wilvor_ai/master_orchestration.py" not in names
    assert "modules/ai_host/src/handler.py" not in names
    assert "PROBE_LIVE_CALLS_ENABLED = False" in handler
    assert "wilvor_historical/coverage_contracts.py" in names
    assert "boto3" not in "".join(names)
    for suffix in package.FORBIDDEN_DESTINATION_SUFFIXES:
        with pytest.raises(package.ProbePackageRefusal):
            package.build_isolated_zip(REPO_ROOT / suffix)
    extract = tmp_path / "extract"
    extract.mkdir()
    zipfile.ZipFile(destination).extractall(extract)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(extract)
    env.pop("PYTHONSAFEPATH", None)
    completed = subprocess.run(
        [sys.executable, "-c", "import handler, sys; assert handler.PROBE_LIVE_CALLS_ENABLED is False; body = handler.lambda_handler({'TableName': 'other'}, None); assert body['statusCode'] == 503; assert body['body']['handler_armed'] is False; assert body['body']['trust_policy_retained'] is True; assert 'wilvor_ai' not in sys.modules"],
        cwd=extract,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    with pytest.raises(package.ProbePackageRefusal):
        package.build_isolated_zip(tmp_path / package.LIVE_TEST_ZIP_NAME)


def test_live_test_zip_arms_only_the_archive(tmp_path: Path):
    source_path = REPO_ROOT / "functions" / "ai_execution_role_probe" / "execution_role_probe.py"
    before = source_path.read_bytes()
    destination = tmp_path / package.LIVE_TEST_ZIP_NAME
    built = package.build_live_test_zip(destination)
    assert source_path.read_bytes() == before
    assert probe.PROBE_LIVE_CALLS_ENABLED is False
    assert built["live_constant"] is True
    assert built["source_live_constant"] is False
    assert built["sha256"]
    assert built["code_sha256"]
    manifest = (tmp_path / (destination.stem + ".manifest.json")).read_text(encoding="utf-8")
    assert built["sha256"] in manifest
    assert '"deployed": false' in manifest
    with zipfile.ZipFile(destination) as archive:
        handler_bytes = archive.read("handler.py")
        handler = handler_bytes.decode("utf-8")
    assert handler.count("PROBE_LIVE_CALLS_ENABLED = True") == 1
    assert "PROBE_LIVE_CALLS_ENABLED = False" not in handler
    assert b"\r\n" not in handler_bytes
    assert "total_max_attempts" in handler
    assert '{"max_attempts"' not in handler.replace(" ", "")
    assert package.LIVE_TEST_ZIP_NAME == window.LIVE_TEST_ZIP_NAME
    with pytest.raises(package.ProbePackageRefusal):
        package.build_live_test_zip(tmp_path / "other.zip")
    with pytest.raises(package.ProbePackageRefusal):
        package.build_live_test_zip(REPO_ROOT / "modules" / "ai_host" / "dist" / package.LIVE_TEST_ZIP_NAME)
    extract = tmp_path / "live-extract"
    extract.mkdir()
    zipfile.ZipFile(destination).extractall(extract)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(extract)
    env.pop("PYTHONSAFEPATH", None)
    for name in (
        "AWS_PROFILE",
        "AWS_LAMBDA_FUNCTION_NAME",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_SHARED_CREDENTIALS_FILE",
    ):
        env.pop(name, None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import handler; body = handler.lambda_handler({'schema':'wilvor.ai.execution_role_probe.request.v1','operation':'getitem_aircraft_probe','aircraft_id':'WILVOR-SPRINT3D-READONLY-PROBE'}, None); assert handler.PROBE_LIVE_CALLS_ENABLED is True; assert body['statusCode'] == 503; assert body['body']['reason'] == 'execution_role_function_required'; assert body['body']['handler_armed'] is True; assert body['body']['live_calls_enabled'] is False",
        ],
        cwd=extract,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert source_path.read_bytes() == before


def test_window_plan_is_unactivated_and_restore_closes_concurrency_first():
    backup = b"current-ai-host-package"
    authorization = window.review_operator_invoke((_allow(),), principal_arn=_allow().principal_arn)
    assert authorization.exact_identity_allow is True
    assert authorization.resource_policy_required is False
    plan = window.plan_window(_capture(backup), backup, authorization)
    assert plan.activated is False
    assert plan.max_invocations == 1
    assert plan.max_getitem_attempts == 1
    assert plan.concurrency_during_probe == 1
    assert plan.concurrency_after_restore == 0
    assert plan.finally_block_is_sufficient is False
    assert plan.environment_mutation is False
    assert plan.wildcard_invoke_permitted is False
    assert "update-function-code" in plan.mutation_commands[0]
    assert "wait function-updated" in plan.mutation_commands[1]
    assert "reserved-concurrent-executions 1" in plan.mutation_commands[3]
    assert "invoke" in plan.mutation_commands[4]
    assert "AWS_MAX_ATTEMPTS=1" in plan.mutation_commands[4]
    assert "--cli-read-timeout 40" in plan.mutation_commands[4]
    assert "--cli-read-timeout 10" not in plan.mutation_commands[4]
    assert "--cli-read-timeout 3" not in plan.mutation_commands[4]
    assert "--qualifier" not in plan.mutation_commands[4]
    assert "raw-in-base64-out" in plan.mutation_commands[4]
    assert plan.cli_read_timeout_seconds > plan.timeout_seconds
    assert plan.cli_max_attempts == 1
    assert plan.authorization_recommendation == window.AUTHORIZATION_PATH_LEAST_PRIVILEGE
    assert "reserved-concurrent-executions 0" in plan.restore_commands[0]
    assert "update-function-code" in plan.restore_commands[1]
    assert "wait function-updated" in plan.restore_commands[2]
    assert "delete-function-concurrency" not in " ".join(plan.restore_commands)
    assert "temporary_code_and_concurrency_changes_drift_from_terraform_until_restore" in plan.cloudtrail_limits
    assert "do_not_terraform_apply_to_clear_temporary_code_or_concurrency_drift" in plan.steps
    assert plan.blockers == ()
    assert window.LIVE_TEST_ZIP_NAME in plan.mutation_commands[0]
    assert "trust_policy_retained_after_closed_invoke" in plan.steps
    assert "do_not_send_the_namespaced_ledger_transaction" in plan.steps
    wildcard = window.review_operator_invoke(
        (_allow(action="lambda:*"),),
        principal_arn=_allow().principal_arn,
    )
    assert wildcard.exact_identity_allow is False
    assert wildcard.wildcard_present is True
    assert wildcard.resource_policy_required is False
    missing = window.review_operator_invoke((), principal_arn=_allow().principal_arn)
    assert missing.blocker == "exact_identity_allow_absent"
    assert missing.resource_policy_required is False
    blocked = window.plan_window(_capture(backup), backup, missing)
    assert "exact_identity_allow_absent" in blocked.blockers
    assert blocked.authorization_recommendation == window.AUTHORIZATION_PATH_LEAST_PRIVILEGE
    wildcard_plan = window.plan_window(_capture(backup), backup, wildcard)
    assert wildcard_plan.authorization_recommendation == window.AUTHORIZATION_PATH_ADMINISTRATOR_EXCEPTION
    assert wildcard_plan.blockers == ("wildcard_invoke_refused",)
    assert wildcard.exact_identity_allow is False
    with pytest.raises(ContractValidationError, match="redundant_exact_allow_on_administrator_refused"):
        window.refuse_redundant_administrator_exact_allow()
    with pytest.raises(ContractValidationError, match="execution_role_window_not_activated"):
        window.open_window(plan)
    with pytest.raises(ContractValidationError, match="code_sha256_mismatch"):
        window.plan_window(_capture(b"other"), backup, authorization)
    with pytest.raises(ContractValidationError, match="backup_path_refused"):
        drifted = _capture(backup)
        drifted["package_backup_path"] = str(
            REPO_ROOT / "functions" / "opensky_poller" / "dist" / "opensky_poller.zip"
        )
        window.plan_window(drifted, backup, authorization)

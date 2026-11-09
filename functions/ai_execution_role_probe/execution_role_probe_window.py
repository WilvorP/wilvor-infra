"""Reviewable plan for a future execution-role invocation window.

Building this plan does not call AWS, change concurrency, or invoke Lambda.
A process ``finally`` block is not the restore mechanism: the restore
commands are idempotent and are run again if the operator process stops.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError


ACCOUNT_ID = "396037508959"
REGION = "us-west-1"
PROFILE = "wilvor-dev"
ROLE_NAME = "wilvor-dev-ai-host-lambda-role"
FUNCTION_NAME = "wilvor-dev-ai-host"
FUNCTION_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{FUNCTION_NAME}"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"
LOG_GROUP = f"/aws/lambda/{FUNCTION_NAME}"
PROBE_AIRCRAFT_ID = "WILVOR-SPRINT3D-READONLY-PROBE"
MAX_INVOCATIONS = 1
CONCURRENCY_DURING_PROBE = 1
CONCURRENCY_AFTER_RESTORE = 0
LAMBDA_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 3
CLI_READ_TIMEOUT_SECONDS = 40
CLI_MAX_ATTEMPTS = 1
MAX_GETITEM_ATTEMPTS = 1
INVOKE_ACTION = "lambda:InvokeFunction"
LIVE_TEST_ZIP_NAME = "wilvor-dev-ai-host-execution-role-readonly-probe-live.zip"
AUTHORIZATION_PATH_ADMINISTRATOR_EXCEPTION = "one_time_administrator_exception"
AUTHORIZATION_PATH_LEAST_PRIVILEGE = "least_privileged_operator_identity"
ALLOWLISTED_EVENT = {
    "schema": "wilvor.ai.execution_role_probe.request.v1",
    "operation": "getitem_aircraft_probe",
    "aircraft_id": PROBE_AIRCRAFT_ID,
}
CLOSED_ENVIRONMENT = {
    "AI_QUERY_ROUTE_ENABLED": "false",
    "PRODUCTION_ISSUANCE_AVAILABLE": "false",
    "LEDGER_WRITES_ENABLED": "false",
}


def lambda_code_sha256(payload: bytes) -> str:
    """Match Lambda ``CodeSha256``: standard base64 of the zip's SHA-256 digest."""

    return base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")


@dataclass(frozen=True)
class IdentityInvokeStatement:
    principal_arn: str
    effect: str
    action: str
    resource: str


@dataclass(frozen=True)
class InvokeAuthorization:
    """Same-account invoke review. A wildcard is not sufficient authorization."""

    exact_identity_allow: bool
    resource_policy_required: bool
    wildcard_present: bool
    blocker: str | None

    def __post_init__(self) -> None:
        if self.resource_policy_required is not False:
            raise ContractValidationError("resource_policy_not_assumed")
        if self.wildcard_present is not False and self.exact_identity_allow is not False:
            raise ContractValidationError("wildcard_invoke_refused")
        if self.exact_identity_allow is True and self.blocker is not None:
            raise ContractValidationError("invoke_authorization_inconsistent")
        if self.exact_identity_allow is not True and self.blocker is None:
            raise ContractValidationError("invoke_authorization_inconsistent")


def review_operator_invoke(
    statements: tuple[IdentityInvokeStatement, ...],
    *,
    principal_arn: str,
    function_arn: str = FUNCTION_ARN,
) -> InvokeAuthorization:
    """An exact same-account Allow is sufficient. This function grants nothing."""

    if "*" in function_arn or function_arn != FUNCTION_ARN:
        return InvokeAuthorization(False, False, True, "wildcard_invoke_refused")
    if not principal_arn.startswith(f"arn:aws:iam::{ACCOUNT_ID}:"):
        return InvokeAuthorization(False, False, False, "cross_account_principal_refused")
    wildcard = False
    exact = False
    denied = False
    for statement in statements:
        if statement.principal_arn != principal_arn:
            continue
        action_wild = "*" in statement.action
        resource_wild = "*" in statement.resource
        if action_wild or resource_wild:
            wildcard = True
        covers_action = statement.action == INVOKE_ACTION
        covers_resource = statement.resource == function_arn
        if statement.effect == "Deny" and (covers_action or action_wild) and (covers_resource or resource_wild):
            denied = True
        if (
            statement.effect == "Allow"
            and covers_action
            and covers_resource
            and action_wild is False
            and resource_wild is False
        ):
            exact = True
    if wildcard and exact is False:
        return InvokeAuthorization(False, False, True, "wildcard_invoke_refused")
    if denied or exact is False:
        reason = "exact_identity_allow_absent" if not statements or exact is False else "invoke_denied"
        if denied:
            reason = "invoke_denied"
        return InvokeAuthorization(False, False, wildcard, reason)
    return InvokeAuthorization(True, False, False, None)


def recommend_authorization_path(authorization: InvokeAuthorization) -> str:
    """Choose a path. This function does not grant IAM and does not activate the window.

    A wildcard administrator identity is not an exact Allow. Attaching a
    redundant ``lambda:InvokeFunction`` statement to that same role is
    refused. The one-time exception, if later approved out of band, is
    procedural only.
    """

    if authorization.exact_identity_allow is True:
        return AUTHORIZATION_PATH_LEAST_PRIVILEGE
    if authorization.wildcard_present is True:
        return AUTHORIZATION_PATH_ADMINISTRATOR_EXCEPTION
    return AUTHORIZATION_PATH_LEAST_PRIVILEGE


def refuse_redundant_administrator_exact_allow(*_args: object, **_kwargs: object) -> None:
    raise ContractValidationError("redundant_exact_allow_on_administrator_refused")


@dataclass(frozen=True)
class WindowPlan:
    activated: bool
    max_invocations: int
    concurrency_during_probe: int
    concurrency_after_restore: int
    timeout_seconds: int
    read_timeout_seconds: int
    cli_read_timeout_seconds: int
    cli_max_attempts: int
    max_getitem_attempts: int
    finally_block_is_sufficient: bool
    resource_policy_required: bool
    wildcard_invoke_permitted: bool
    environment_mutation: bool
    authorization_recommendation: str
    steps: tuple[str, ...]
    mutation_commands: tuple[str, ...]
    restore_commands: tuple[str, ...]
    verification_commands: tuple[str, ...]
    cloudtrail_limits: tuple[str, ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.activated is not False:
            raise ContractValidationError("execution_role_window_not_activated")
        if self.max_invocations != MAX_INVOCATIONS:
            raise ContractValidationError("invocation_budget_exceeded")
        if self.concurrency_during_probe != CONCURRENCY_DURING_PROBE:
            raise ContractValidationError("concurrency_above_minimum")
        if self.concurrency_after_restore != CONCURRENCY_AFTER_RESTORE:
            raise ContractValidationError("concurrency_restore_required")
        if self.timeout_seconds != LAMBDA_TIMEOUT_SECONDS or self.read_timeout_seconds != READ_TIMEOUT_SECONDS:
            raise ContractValidationError("timeout_budget_exceeded")
        if self.cli_read_timeout_seconds <= self.timeout_seconds or self.cli_read_timeout_seconds != CLI_READ_TIMEOUT_SECONDS:
            raise ContractValidationError("cli_timeout_must_exceed_lambda_timeout")
        if self.cli_max_attempts != CLI_MAX_ATTEMPTS:
            raise ContractValidationError("cli_retry_would_reinvoke")
        if self.max_getitem_attempts != MAX_GETITEM_ATTEMPTS:
            raise ContractValidationError("read_budget_exceeded")
        if self.finally_block_is_sufficient is not False:
            raise ContractValidationError("finally_block_is_not_sufficient")
        if self.resource_policy_required is not False or self.wildcard_invoke_permitted is not False:
            raise ContractValidationError("wildcard_invoke_refused")
        if self.environment_mutation is not False:
            raise ContractValidationError("production_flags_unchanged")
        if not self.restore_commands or "reserved-concurrent-executions 0" not in self.restore_commands[0]:
            raise ContractValidationError("restore_must_close_concurrency_first")
        if any("--qualifier" in command or "--publish" in command for command in self.mutation_commands):
            raise ContractValidationError("qualified_invoke_refused")
        if not any("reserved-concurrent-executions 1" in command for command in self.mutation_commands):
            raise ContractValidationError("concurrency_window_missing")
        update_i = next(i for i, command in enumerate(self.mutation_commands) if "update-function-code" in command)
        wait_i = next(i for i, command in enumerate(self.mutation_commands) if "wait function-updated" in command)
        conc_i = next(i for i, command in enumerate(self.mutation_commands) if "reserved-concurrent-executions 1" in command)
        invoke_i = next(
            i
            for i, command in enumerate(self.mutation_commands)
            if " invoke " in f" {command} " and "update-function-code" not in command
        )
        if not (update_i < wait_i < conc_i < invoke_i):
            raise ContractValidationError("concurrency_before_code_update_complete")
        invoke = self.mutation_commands[invoke_i]
        if f"--cli-read-timeout {CLI_READ_TIMEOUT_SECONDS}" not in invoke:
            raise ContractValidationError("cli_timeout_must_exceed_lambda_timeout")
        if f"AWS_MAX_ATTEMPTS={CLI_MAX_ATTEMPTS}" not in invoke:
            raise ContractValidationError("cli_retry_would_reinvoke")
        restore_wait = next(i for i, command in enumerate(self.restore_commands) if "wait function-updated" in command)
        restore_sha = next(i for i, command in enumerate(self.restore_commands) if "CodeSha256" in command)
        if restore_wait > restore_sha:
            raise ContractValidationError("restore_sha_before_update_complete")
        if self.authorization_recommendation not in (
            AUTHORIZATION_PATH_ADMINISTRATOR_EXCEPTION,
            AUTHORIZATION_PATH_LEAST_PRIVILEGE,
        ):
            raise ContractValidationError("authorization_path_unknown")
        for command in self.mutation_commands:
            if "put-event-selectors" in command or "delete-function-concurrency" in command:
                raise ContractValidationError("window_mutates_the_wrong_control")
            if "put-function-concurrency" in command and "reserved-concurrent-executions 0" in command:
                raise ContractValidationError("window_mutates_the_wrong_control")


def _aws(*args: str) -> str:
    return " ".join(("aws", "--profile", PROFILE, "--region", REGION, *args))


def restore_commands(backup_zip: str) -> tuple[str, ...]:
    """Idempotent restore. Run it again if the operator process has already stopped."""

    function = f"--function-name {FUNCTION_NAME}"
    return (
        _aws("lambda", "put-function-concurrency", function, "--reserved-concurrent-executions", "0"),
        _aws(
            "lambda",
            "update-function-code",
            function,
            "--zip-file",
            f"fileb://{backup_zip}",
        ),
        _aws("lambda", "wait", "function-updated", function),
        _aws("lambda", "get-function", function, "--query", "Configuration.CodeSha256"),
        _aws("lambda", "get-function-concurrency", function),
    )


def verification_commands() -> tuple[str, ...]:
    function = f"--function-name {FUNCTION_NAME}"
    return (
        _aws("lambda", "get-function", function),
        _aws("lambda", "get-function-concurrency", function),
        _aws("lambda", "get-function-configuration", function),
        _aws("apigatewayv2", "get-apis"),
        _aws("apigatewayv2", "get-routes", "--api-id", "<captured-operational-api-id>"),
    )


def _require_capture(capture: Mapping[str, object], backup: bytes) -> None:
    if capture.get("function_name") != FUNCTION_NAME or capture.get("function_arn") != FUNCTION_ARN:
        raise ContractValidationError("capture_function_mismatch")
    if capture.get("handler") != "handler.lambda_handler" or capture.get("runtime") != "python3.12":
        raise ContractValidationError("capture_handler_mismatch")
    if capture.get("timeout_seconds") != LAMBDA_TIMEOUT_SECONDS:
        raise ContractValidationError("timeout_budget_exceeded")
    if capture.get("reserved_concurrency") != 0:
        raise ContractValidationError("concurrency_not_closed")
    if capture.get("role_arn") != ROLE_ARN:
        raise ContractValidationError("capture_role_mismatch")
    if capture.get("resource_policy_present") is not False:
        raise ContractValidationError("resource_policy_present")
    routes = capture.get("ai_query_routes")
    if routes != ():
        raise ContractValidationError("ai_query_route_present")
    environment = capture.get("environment")
    if not isinstance(environment, Mapping) or any(
        environment.get(key) != value for key, value in CLOSED_ENVIRONMENT.items()
    ):
        raise ContractValidationError("production_flags_mismatch")
    if capture.get("code_sha256") != lambda_code_sha256(backup):
        raise ContractValidationError("code_sha256_mismatch")
    backup_path = str(capture.get("package_backup_path", ""))
    normalized = backup_path.replace("\\", "/").casefold()
    refused = (
        "modules/ai_host/dist/ai_host.zip",
        "aircraft_current_state_writer.zip",
        "aircraft_raw_processor.zip",
        "opensky_poller.zip",
    )
    if any(suffix in normalized for suffix in refused):
        raise ContractValidationError("backup_path_refused")
    probe_zip = str(capture.get("probe_zip_path", "")).replace("\\", "/")
    probe_normal = probe_zip.casefold()
    if not probe_normal.endswith("/" + LIVE_TEST_ZIP_NAME.casefold()):
        raise ContractValidationError("live_test_zip_required")
    if any(suffix in probe_normal for suffix in refused) or "/modules/ai_host/" in probe_normal:
        raise ContractValidationError("live_test_zip_required")
    if capture.get("probe_handler_live_constant") is not True or capture.get("source_live_constant") is not False:
        raise ContractValidationError("source_probe_must_stay_closed")


def plan_window(
    capture: Mapping[str, object],
    backup: bytes,
    authorization: InvokeAuthorization,
) -> WindowPlan:
    """Return the unactivated window. Preconditions that fail raise before a plan exists."""

    _require_capture(capture, backup)
    backup_path = str(capture["package_backup_path"])
    probe_zip = str(capture["probe_zip_path"])
    blockers: list[str] = []
    if authorization.blocker is not None:
        blockers.append(authorization.blocker)
    function = f"--function-name {FUNCTION_NAME}"
    steps = (
        "capture_function_configuration_code_sha_concurrency_environment_policy_and_routes",
        "download_current_package_outside_tracked_zip_paths",
        "compare_backup_sha256_with_captured_code_sha256_and_abort_on_mismatch",
        "rerun_operator_describe_table_and_iam_simulation_and_abort_on_drift",
        "review_same_account_identity_invoke_without_adding_a_resource_policy",
        "trust_policy_retained_after_closed_invoke",
        "deploy_only_the_named_live_test_zip",
        "source_probe_constant_stays_false",
        "do_not_send_the_namespaced_ledger_transaction",
        "update_function_code_with_the_isolated_probe_zip_while_concurrency_remains_0",
        "wait_until_last_update_status_is_successful_before_changing_concurrency",
        "read_back_code_sha256_and_compare_it_with_the_probe_zip",
        "set_reserved_concurrency_to_1",
        "invoke_the_unqualified_function_once_with_the_allowlisted_event",
        "cli_read_timeout_exceeds_lambda_timeout_and_cli_max_attempts_is_1",
        "set_reserved_concurrency_to_0_before_restoring_code",
        "restore_the_backup_zip",
        "wait_until_restore_update_is_successful_before_reading_code_sha256",
        "independently_reread_code_sha256_concurrency_environment_and_routes",
        "after_an_interrupted_update_repeat_restore_from_concurrency_zero_and_do_not_invoke_again",
        "do_not_terraform_apply_to_clear_temporary_code_or_concurrency_drift",
    )
    mutation = (
        _aws("lambda", "update-function-code", function, "--zip-file", f"fileb://{probe_zip}"),
        _aws("lambda", "wait", "function-updated", function),
        _aws("lambda", "get-function", function, "--query", "Configuration.CodeSha256"),
        _aws("lambda", "put-function-concurrency", function, "--reserved-concurrent-executions", "1"),
        " ".join(
            (
                f"AWS_MAX_ATTEMPTS={CLI_MAX_ATTEMPTS}",
                _aws(
                    "lambda",
                    "invoke",
                    function,
                    "--invocation-type",
                    "RequestResponse",
                    "--cli-binary-format",
                    "raw-in-base64-out",
                    "--cli-read-timeout",
                    str(CLI_READ_TIMEOUT_SECONDS),
                    "--payload",
                    "file://allowlisted-event.json",
                    "probe-response.json",
                ),
            )
        ),
    )
    limits = (
        "management_events_can_show_UpdateFunctionCode_PutFunctionConcurrency_and_AssumeRole",
        "lambda_invoke_is_a_data_event_and_is_absent_unless_data_events_are_enabled",
        "dynamodb_getitem_is_a_data_event_and_is_absent_unless_data_events_are_enabled",
        "get_caller_identity_is_not_cloudtrail_evidence",
        "this_plan_does_not_enable_data_events",
        "temporary_code_and_concurrency_changes_drift_from_terraform_until_restore",
    )
    return WindowPlan(
        activated=False,
        max_invocations=MAX_INVOCATIONS,
        concurrency_during_probe=CONCURRENCY_DURING_PROBE,
        concurrency_after_restore=CONCURRENCY_AFTER_RESTORE,
        timeout_seconds=LAMBDA_TIMEOUT_SECONDS,
        read_timeout_seconds=READ_TIMEOUT_SECONDS,
        cli_read_timeout_seconds=CLI_READ_TIMEOUT_SECONDS,
        cli_max_attempts=CLI_MAX_ATTEMPTS,
        max_getitem_attempts=MAX_GETITEM_ATTEMPTS,
        finally_block_is_sufficient=False,
        resource_policy_required=False,
        wildcard_invoke_permitted=False,
        environment_mutation=False,
        authorization_recommendation=recommend_authorization_path(authorization),
        steps=steps,
        mutation_commands=mutation,
        restore_commands=restore_commands(backup_path),
        verification_commands=verification_commands(),
        cloudtrail_limits=limits,
        blockers=tuple(blockers),
    )


def open_window(*_args: object, **_kwargs: object) -> None:
    """The window stays closed. No concurrency, code, or invoke API is called."""

    raise ContractValidationError("execution_role_window_not_activated")

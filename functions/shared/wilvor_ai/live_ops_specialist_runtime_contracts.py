"""Trusted Live Ops runtime and dispatch-audit contracts.

These types describe invocation identity and dispatch outcomes. They do not
call a model, verify claims, or decide operational currentness.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    ToolResult,
    _is_json_value,
    _validate_bounded_text,
    _validate_text,
)
from wilvor_ai.live_ops_model_contracts import LiveOpsValidationFailure
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection


LIVE_OPS_DISPATCH_SCHEMA_VERSION = "wilvor.ai.live_ops_dispatch.v1"
LIVE_OPS_CANONICAL_KEY_MAX_LENGTH = 8192


class LiveOpsRuntimeErrorCode(str, Enum):
    """Closed runtime failure codes. Not exception text or model prose."""

    INVALID_TOOL_CALL = "invalid_tool_call"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    PROJECTION_INTEGRITY_FAILED = "projection_integrity_failed"
    TRUSTED_CONTEXT_INVALID = "trusted_context_invalid"
    TOOL_RESULT_IDENTITY_MISMATCH = "tool_result_identity_mismatch"
    TOOL_CALL_ID_FACTORY_FAILED = "tool_call_id_factory_failed"


class LiveOpsDispatchStatus(str, Enum):
    """Dispatch outcome. Validation rejection is not an executed batch."""

    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    COMPLETED = "COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True)
class LiveOpsSpecialistTrustedContext:
    """Caller-injected tables and reference time. Not model arguments."""

    tables: Any
    now_epoch: int
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.tables is None:
            errors.append("invalid_tables")
        if type(self.now_epoch) is not int:
            errors.append("invalid_now_epoch")
        if self.correlation_id is not None:
            if (
                not isinstance(self.correlation_id, str)
                or not self.correlation_id
                or self.correlation_id != self.correlation_id.strip()
            ):
                errors.append("invalid_correlation_id")
            else:
                errors.extend(
                    _validate_bounded_text(
                        self.correlation_id,
                        "correlation_id",
                        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
                    )
                )
        if errors:
            raise ContractValidationError(errors)


@dataclass(frozen=True)
class LiveOpsPreparedCall:
    """Validated model arguments ready for trusted dispatch."""

    name: str
    requested_arguments: dict[str, JsonValue]
    executed_arguments: dict[str, JsonValue]
    canonical_key: str

    def __post_init__(self) -> None:
        errors = _validate_text(self.name, "name")
        errors.extend(
            _validate_bounded_text(
                self.canonical_key,
                "canonical_key",
                max_length=LIVE_OPS_CANONICAL_KEY_MAX_LENGTH,
            )
        )
        for field_name, payload in (
            ("requested_arguments", self.requested_arguments),
            ("executed_arguments", self.executed_arguments),
        ):
            if not isinstance(payload, dict) or any(
                not isinstance(key, str) or not _is_json_value(value)
                for key, value in payload.items()
            ):
                errors.append(f"invalid_{field_name}")
        if errors:
            raise ContractValidationError(errors)
        object.__setattr__(
            self,
            "requested_arguments",
            copy.deepcopy(dict(self.requested_arguments)),
        )
        object.__setattr__(
            self,
            "executed_arguments",
            copy.deepcopy(dict(self.executed_arguments)),
        )


@dataclass(frozen=True)
class LiveOpsToolInvocation:
    """Audit of one executed Live Ops tool. Raw ToolResult is authoritative."""

    tool_name: str
    tool_call_id: str
    requested_arguments: dict[str, JsonValue]
    executed_arguments: dict[str, JsonValue]
    canonical_key: str
    raw_tool_result: ToolResult
    model_projection: LiveOpsToolProjection | None = None

    def __post_init__(self) -> None:
        errors = _validate_text(self.tool_name, "tool_name")
        errors.extend(_validate_text(self.tool_call_id, "tool_call_id"))
        errors.extend(
            _validate_bounded_text(
                self.canonical_key,
                "canonical_key",
                max_length=LIVE_OPS_CANONICAL_KEY_MAX_LENGTH,
            )
        )
        if not isinstance(self.raw_tool_result, ToolResult):
            errors.append("invalid_tool_result")
        if self.model_projection is not None and not isinstance(
            self.model_projection,
            LiveOpsToolProjection,
        ):
            errors.append("invalid_model_projection")
        for field_name, payload in (
            ("requested_arguments", self.requested_arguments),
            ("executed_arguments", self.executed_arguments),
        ):
            if not isinstance(payload, dict) or any(
                not isinstance(key, str) or not _is_json_value(value)
                for key, value in payload.items()
            ):
                errors.append(f"invalid_{field_name}")
        if errors:
            raise ContractValidationError(errors)
        object.__setattr__(
            self,
            "requested_arguments",
            copy.deepcopy(dict(self.requested_arguments)),
        )
        object.__setattr__(
            self,
            "executed_arguments",
            copy.deepcopy(dict(self.executed_arguments)),
        )


@dataclass(frozen=True)
class LiveOpsDispatchResult:
    """Outcome of one adapter dispatch.

    Validation rejection executes no tools. Execution failure may preserve
    earlier raw ToolResults. COMPLETED means every planned call produced a
    raw result and a safe projection.
    """

    status: LiveOpsDispatchStatus
    invocations: tuple[LiveOpsToolInvocation, ...] = ()
    validation_failure: LiveOpsValidationFailure | None = None
    execution_error: LiveOpsRuntimeErrorCode | None = None
    failed_tool_name: str | None = None
    failed_tool_call_id: str | None = None
    planned_tool_call_ids: tuple[str, ...] = ()
    schema_version: str = LIVE_OPS_DISPATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != LIVE_OPS_DISPATCH_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.status, LiveOpsDispatchStatus):
            errors.append("invalid_status")
        if not isinstance(self.invocations, tuple) or any(
            not isinstance(item, LiveOpsToolInvocation) for item in self.invocations
        ):
            errors.append("invalid_invocations")
        if self.validation_failure is not None and not isinstance(
            self.validation_failure,
            LiveOpsValidationFailure,
        ):
            errors.append("invalid_validation_failure")
        if self.execution_error is not None and not isinstance(
            self.execution_error,
            LiveOpsRuntimeErrorCode,
        ):
            errors.append("invalid_execution_error")
        errors.extend(
            _validate_bounded_text(
                self.failed_tool_name,
                "failed_tool_name",
                optional=True,
            )
        )
        errors.extend(
            _validate_bounded_text(
                self.failed_tool_call_id,
                "failed_tool_call_id",
                optional=True,
            )
        )
        if not isinstance(self.planned_tool_call_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.planned_tool_call_ids
        ):
            errors.append("invalid_planned_tool_call_ids")
        if isinstance(self.status, LiveOpsDispatchStatus):
            errors.extend(self._status_errors())
        if errors:
            raise ContractValidationError(errors)

    def _status_errors(self) -> list[str]:
        if self.status is LiveOpsDispatchStatus.VALIDATION_REJECTED:
            errors: list[str] = []
            if self.validation_failure is None:
                errors.append("validation_rejected_requires_failure")
            if self.invocations:
                errors.append("validation_rejected_forbids_invocations")
            if self.execution_error is not None:
                errors.append("validation_rejected_forbids_execution_error")
            return errors
        if self.status is LiveOpsDispatchStatus.COMPLETED:
            errors = []
            if not self.invocations:
                errors.append("completed_requires_invocations")
            if self.validation_failure is not None:
                errors.append("completed_forbids_validation_failure")
            if self.execution_error is not None:
                errors.append("completed_forbids_execution_error")
            if any(item.model_projection is None for item in self.invocations):
                errors.append("completed_requires_projections")
            return errors
        errors = []
        if self.validation_failure is not None:
            errors.append("execution_failed_forbids_validation_failure")
        if self.execution_error is None:
            errors.append("execution_failed_requires_error")
        return errors


__all__ = [
    "LIVE_OPS_CANONICAL_KEY_MAX_LENGTH",
    "LIVE_OPS_DISPATCH_SCHEMA_VERSION",
    "LiveOpsDispatchResult",
    "LiveOpsDispatchStatus",
    "LiveOpsPreparedCall",
    "LiveOpsRuntimeErrorCode",
    "LiveOpsSpecialistTrustedContext",
    "LiveOpsToolInvocation",
]

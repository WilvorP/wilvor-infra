"""Provider-neutral Decision specialist request and run contracts.

This module describes the trusted target, the model protocol, and the run
audit. It does not call a model or dispatch Decision Tools. A final
COMPLETED or PARTIAL result recomputes DE1 and DE2 only to reject a stored
artifact that no longer matches its claims and raw bindings. Non-final
results do not.

``collection_partial_reason`` records skipped persisted fanout. It is
independent of terminal status:

- COMPLETED means FINAL_CLAIMS was reached and DE1 plus DE2 ran, and no
  fanout was skipped. It does not mean verification passed.
- PARTIAL means FINAL_CLAIMS was reached and DE1 plus DE2 ran, and a fanout
  was skipped earlier.
- A later non-final status such as PROVIDER_FAILED keeps the collection
  reason for audit and is not relabeled PARTIAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    ToolResult,
    _is_json_value,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
)
from wilvor_ai.decision_answer_renderer import (
    DecisionRenderOutcome,
    DecisionRenderResult,
    render_decision_verification,
)
from wilvor_ai.decision_claims import (
    DECISION_EVIDENCE_REF_PATTERN,
    DecisionClaim,
    _is_decision_claim,
    decision_claim_from_dict,
)
from wilvor_ai.decision_contracts import OPERATIONAL_ID_MAX_LENGTH
from wilvor_ai.decision_evidence_verifier import (
    DecisionEvidenceBinding,
    DecisionVerificationResult,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import (
    DecisionEvidenceSnapshot,
    DecisionModelDecision,
    DecisionModelTurnRequest,
    DecisionUnsupportedReason,
)
from wilvor_ai.model_contracts import ModelDecisionKind, ValidationFeedback
from wilvor_ai.specialist_contracts import SPECIALIST_REQUEST_TEXT_MAX_LENGTH


DECISION_SPECIALIST_RUN_SCHEMA_VERSION = "wilvor.ai.decision_specialist_run.v1"
_CANONICAL_KEY_MAX_LENGTH = 8192

_RUN_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "mode",
        "aircraft_id",
        "recommendation_id",
        "tool_results",
        "evidence_snapshots",
        "evidence_bindings",
        "invocations",
        "proposed_claims",
        "verification",
        "render",
        "unsupported_reason",
        "runtime_errors",
        "collection_partial_reason",
        "validation_feedback",
        "provider_turn_count",
        "executed_tool_call_count",
        "executed_persisted_call_count",
        "terminal_kind",
        "refusal_code",
    }
)


class DecisionTargetMode(str, Enum):
    """One trusted target. User text is not a second target."""

    AIRCRAFT = "AIRCRAFT"
    DIRECT_PERSISTED = "DIRECT_PERSISTED"


class DecisionSpecialistRunStatus(str, Enum):
    """Orchestration outcome. Not the DE1 verification outcome."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class DecisionRuntimeErrorCode(str, Enum):
    """Closed runtime failures. Not exception text or model prose."""

    MODEL_TURN_LIMIT_EXCEEDED = "model_turn_limit_exceeded"
    PROVIDER_EXCEPTION = "provider_exception"
    INVALID_MODEL_DECISION = "invalid_model_decision"
    REFUSAL = "refusal"
    ADAPTER_EXECUTION_FAILED = "adapter_execution_failed"


class DecisionCollectionPartialReason(str, Enum):
    """Why a persisted fanout was skipped before any of its calls ran."""

    PERSISTED_FANOUT_CAP = "PERSISTED_FANOUT_CAP"
    PERSISTED_FANOUT_BUDGET = "PERSISTED_FANOUT_BUDGET"


class DecisionDispatchOrigin(str, Enum):
    """Whether an execution was the model's call or a deterministic expansion."""

    MODEL_REQUEST = "MODEL_REQUEST"
    DETERMINISTIC_PERSISTED_FANOUT = "DETERMINISTIC_PERSISTED_FANOUT"


class DecisionModelProvider(Protocol):
    """Decision turn protocol. Not the historical ModelProvider."""

    def complete(self, turn: DecisionModelTurnRequest) -> DecisionModelDecision:
        """Return one Decision model decision for this turn."""


def _target_id(value: Any, field_name: str, *, optional: bool) -> list[str]:
    if value is None and optional:
        return []
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=OPERATIONAL_ID_MAX_LENGTH,
    )
    if errors or value is None:
        return errors
    if any(character.isspace() for character in value):
        return [f"invalid_{field_name}"]
    return []


def _count(value: Any, field_name: str) -> list[str]:
    if type(value) is not int or value < 0:
        return [f"invalid_{field_name}"]
    return []


def _json_arguments(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"invalid_{field_name}"]
    if any(
        not isinstance(key, str) or not _is_json_value(item)
        for key, item in value.items()
    ):
        return [f"invalid_{field_name}"]
    return []


@dataclass(frozen=True)
class DecisionSpecialistRequest:
    """Trusted Decision request. The model cannot choose the target."""

    user_text: str
    mode: DecisionTargetMode
    aircraft_id: str | None = None
    recommendation_id: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.user_text,
            "user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        if not isinstance(self.mode, DecisionTargetMode):
            errors.append("invalid_mode")
        errors.extend(_target_id(self.aircraft_id, "aircraft_id", optional=True))
        errors.extend(
            _target_id(self.recommendation_id, "recommendation_id", optional=True)
        )
        errors.extend(
            _validate_bounded_text(
                self.request_id,
                "request_id",
                optional=True,
                max_length=PROVENANCE_TOKEN_MAX_LENGTH,
            )
        )
        if isinstance(self.request_id, str) and any(
            character.isspace() for character in self.request_id
        ):
            errors.append("invalid_request_id")
        if isinstance(self.mode, DecisionTargetMode):
            if self.mode is DecisionTargetMode.AIRCRAFT:
                if self.aircraft_id is None or self.recommendation_id is not None:
                    errors.append("invalid_decision_target")
            elif self.recommendation_id is None or self.aircraft_id is not None:
                errors.append("invalid_decision_target")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "user_text": self.user_text,
            "mode": self.mode.value,
        }
        if self.aircraft_id is not None:
            payload["aircraft_id"] = self.aircraft_id
        if self.recommendation_id is not None:
            payload["recommendation_id"] = self.recommendation_id
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionSpecialistRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_specialist_request")
        extra = set(data) - {
            "user_text",
            "mode",
            "aircraft_id",
            "recommendation_id",
            "request_id",
        }
        if extra:
            raise ContractValidationError("unexpected_decision_request_field")
        return cls(
            user_text=_required(data, "user_text"),
            mode=_parse_enum(DecisionTargetMode, _required(data, "mode"), "mode"),
            aircraft_id=data["aircraft_id"] if "aircraft_id" in data else None,
            recommendation_id=(
                data["recommendation_id"] if "recommendation_id" in data else None
            ),
            request_id=data["request_id"] if "request_id" in data else None,
        )


@dataclass(frozen=True)
class DecisionToolInvocationAudit:
    """One actual adapter execution. Not a model-visible evidence snapshot."""

    tool_name: str
    tool_call_id: str
    evidence_ref: str
    requested_arguments: dict[str, JsonValue]
    executed_arguments: dict[str, JsonValue]
    canonical_key: str
    dispatch_origin: DecisionDispatchOrigin

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.tool_name,
            "tool_name",
            max_length=PROVENANCE_TOKEN_MAX_LENGTH,
        )
        errors.extend(
            _validate_bounded_text(
                self.tool_call_id,
                "tool_call_id",
                max_length=PROVENANCE_TOKEN_MAX_LENGTH,
            )
        )
        if isinstance(self.tool_call_id, str) and any(
            character.isspace() for character in self.tool_call_id
        ):
            errors.append("invalid_tool_call_id")
        if (
            not isinstance(self.evidence_ref, str)
            or DECISION_EVIDENCE_REF_PATTERN.fullmatch(self.evidence_ref) is None
        ):
            errors.append("invalid_evidence_ref")
        errors.extend(_json_arguments(self.requested_arguments, "requested_arguments"))
        errors.extend(_json_arguments(self.executed_arguments, "executed_arguments"))
        errors.extend(
            _validate_bounded_text(
                self.canonical_key,
                "canonical_key",
                max_length=_CANONICAL_KEY_MAX_LENGTH,
            )
        )
        if not isinstance(self.dispatch_origin, DecisionDispatchOrigin):
            errors.append("invalid_dispatch_origin")
        if errors:
            raise ContractValidationError(errors)
        object.__setattr__(self, "requested_arguments", dict(self.requested_arguments))
        object.__setattr__(self, "executed_arguments", dict(self.executed_arguments))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "evidence_ref": self.evidence_ref,
            "requested_arguments": dict(self.requested_arguments),
            "executed_arguments": dict(self.executed_arguments),
            "canonical_key": self.canonical_key,
            "dispatch_origin": self.dispatch_origin.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionToolInvocationAudit":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_tool_invocation")
        extra = set(data) - {
            "tool_name",
            "tool_call_id",
            "evidence_ref",
            "requested_arguments",
            "executed_arguments",
            "canonical_key",
            "dispatch_origin",
        }
        if extra:
            raise ContractValidationError("unexpected_invocation_field")
        requested = _required(data, "requested_arguments")
        executed = _required(data, "executed_arguments")
        if not isinstance(requested, Mapping) or not isinstance(executed, Mapping):
            raise ContractValidationError("invalid_arguments")
        return cls(
            tool_name=_required(data, "tool_name"),
            tool_call_id=_required(data, "tool_call_id"),
            evidence_ref=_required(data, "evidence_ref"),
            requested_arguments=dict(requested),
            executed_arguments=dict(executed),
            canonical_key=_required(data, "canonical_key"),
            dispatch_origin=_parse_enum(
                DecisionDispatchOrigin,
                _required(data, "dispatch_origin"),
                "dispatch_origin",
            ),
        )


def _binding_from_dict(data: Any) -> DecisionEvidenceBinding:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_decision_evidence_binding")
    extra = set(data) - {"evidence_ref", "raw_tool_result"}
    if extra:
        raise ContractValidationError("unexpected_binding_field")
    return DecisionEvidenceBinding(
        evidence_ref=_required(data, "evidence_ref"),
        raw_tool_result=ToolResult.from_dict(_required(data, "raw_tool_result")),
    )


def _render_from_dict(data: Any) -> DecisionRenderResult:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_decision_render_result")
    extra = set(data) - {"outcome", "answer"}
    if extra:
        raise ContractValidationError("unexpected_render_field")
    return DecisionRenderResult(
        outcome=_parse_enum(
            DecisionRenderOutcome,
            _required(data, "outcome"),
            "render_outcome",
        ),
        answer=_required(data, "answer"),
    )


def _align_executions(
    tool_results: tuple[ToolResult, ...],
    snapshots: tuple[DecisionEvidenceSnapshot, ...],
    bindings: tuple[DecisionEvidenceBinding, ...],
    invocations: tuple[DecisionToolInvocationAudit, ...],
) -> list[str]:
    """Structural audit only. Does not re-run DE1 or re-project results."""

    if not (
        len(tool_results)
        == len(snapshots)
        == len(bindings)
        == len(invocations)
    ):
        return ["execution_audit_length_mismatch"]
    errors: list[str] = []
    if len({item.evidence_ref for item in snapshots}) != len(snapshots):
        errors.append("duplicate_evidence_ref")
    if len({item.tool_call_id for item in tool_results}) != len(tool_results):
        errors.append("duplicate_tool_call_id")
    if len({item.evidence_ref for item in invocations}) != len(invocations):
        errors.append("duplicate_invocation_evidence_ref")
    for raw, snapshot, binding, invocation in zip(
        tool_results,
        snapshots,
        bindings,
        invocations,
    ):
        if not (
            snapshot.evidence_ref
            == binding.evidence_ref
            == invocation.evidence_ref
        ):
            errors.append("evidence_ref_mismatch")
        if binding.raw_tool_result != raw:
            errors.append("binding_tool_result_mismatch")
        if invocation.tool_call_id != raw.tool_call_id:
            errors.append("invocation_tool_call_id_mismatch")
        if invocation.tool_name != raw.tool_name:
            errors.append("invocation_tool_name_mismatch")
    return errors


def _final_artifact_integrity(
    claims: tuple[DecisionClaim, ...],
    bindings: tuple[DecisionEvidenceBinding, ...],
    verification: DecisionVerificationResult | None,
    render: DecisionRenderResult | None,
) -> list[str]:
    """Reject a final artifact whose stored DE1 or DE2 output was replaced.

    This recomputes the deterministic pipeline. It does not repair the
    stored verification or render.
    """

    claims_ok = bool(claims) and all(_is_decision_claim(item) for item in claims)
    bindings_ok = all(isinstance(item, DecisionEvidenceBinding) for item in bindings)
    if (
        not claims_ok
        or not bindings_ok
        or not isinstance(verification, DecisionVerificationResult)
        or not isinstance(render, DecisionRenderResult)
    ):
        return []
    expected_verification = verify_decision_evidence(claims, bindings)
    errors: list[str] = []
    if expected_verification != verification:
        errors.append("verification_integrity_mismatch")
    expected_render = render_decision_verification(expected_verification)
    if expected_render != render:
        errors.append("render_integrity_mismatch")
    return errors


@dataclass(frozen=True)
class DecisionSpecialistRunResult:
    """Audit of one Decision specialist run.

    COMPLETED and PARTIAL both mean DE1 and DE2 ran. COMPLETED does not mean
    the claims passed. Construction and ``from_dict`` recompute those
    deterministic results and reject the artifact when the stored
    verification or render differs. They do not replace the stored values.
    ``collection_partial_reason`` may remain set on a non-final status so a
    skipped fanout stays visible after a later provider failure. That does
    not change the terminal status to PARTIAL. Non-final statuses do not
    recompute DE1 or DE2.
    """

    status: DecisionSpecialistRunStatus
    mode: DecisionTargetMode
    aircraft_id: str | None
    recommendation_id: str | None
    tool_results: tuple[ToolResult, ...]
    evidence_snapshots: tuple[DecisionEvidenceSnapshot, ...]
    evidence_bindings: tuple[DecisionEvidenceBinding, ...]
    invocations: tuple[DecisionToolInvocationAudit, ...]
    proposed_claims: tuple[DecisionClaim, ...]
    verification: DecisionVerificationResult | None
    render: DecisionRenderResult | None
    unsupported_reason: DecisionUnsupportedReason | None
    runtime_errors: tuple[DecisionRuntimeErrorCode, ...]
    collection_partial_reason: DecisionCollectionPartialReason | None
    validation_feedback: tuple[ValidationFeedback, ...]
    provider_turn_count: int
    executed_tool_call_count: int
    executed_persisted_call_count: int
    terminal_kind: ModelDecisionKind | None
    refusal_code: str | None = None
    schema_version: str = DECISION_SPECIALIST_RUN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != DECISION_SPECIALIST_RUN_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.status, DecisionSpecialistRunStatus):
            errors.append("invalid_status")
        if not isinstance(self.mode, DecisionTargetMode):
            errors.append("invalid_mode")
        errors.extend(_target_id(self.aircraft_id, "aircraft_id", optional=True))
        errors.extend(
            _target_id(self.recommendation_id, "recommendation_id", optional=True)
        )
        if isinstance(self.mode, DecisionTargetMode):
            if self.mode is DecisionTargetMode.AIRCRAFT:
                if self.aircraft_id is None or self.recommendation_id is not None:
                    errors.append("invalid_decision_target")
            elif self.recommendation_id is None or self.aircraft_id is not None:
                errors.append("invalid_decision_target")
        if not isinstance(self.tool_results, tuple) or any(
            not isinstance(item, ToolResult) for item in self.tool_results
        ):
            errors.append("invalid_tool_results")
        if not isinstance(self.evidence_snapshots, tuple) or any(
            not isinstance(item, DecisionEvidenceSnapshot)
            for item in self.evidence_snapshots
        ):
            errors.append("invalid_evidence_snapshots")
        if not isinstance(self.evidence_bindings, tuple) or any(
            not isinstance(item, DecisionEvidenceBinding)
            for item in self.evidence_bindings
        ):
            errors.append("invalid_evidence_bindings")
        if not isinstance(self.invocations, tuple) or any(
            not isinstance(item, DecisionToolInvocationAudit)
            for item in self.invocations
        ):
            errors.append("invalid_invocations")
        if not isinstance(self.proposed_claims, tuple) or any(
            not _is_decision_claim(item) for item in self.proposed_claims
        ):
            errors.append("invalid_proposed_claims")
        if self.verification is not None and not isinstance(
            self.verification,
            DecisionVerificationResult,
        ):
            errors.append("invalid_verification")
        if self.render is not None and not isinstance(self.render, DecisionRenderResult):
            errors.append("invalid_render")
        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, DecisionUnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")
        if not isinstance(self.runtime_errors, tuple) or any(
            not isinstance(item, DecisionRuntimeErrorCode)
            for item in self.runtime_errors
        ):
            errors.append("invalid_runtime_errors")
        elif len(self.runtime_errors) != len(set(self.runtime_errors)):
            errors.append("duplicate_runtime_errors")
        if (
            self.collection_partial_reason is not None
            and not isinstance(
                self.collection_partial_reason,
                DecisionCollectionPartialReason,
            )
        ):
            errors.append("invalid_collection_partial_reason")
        if not isinstance(self.validation_feedback, tuple) or any(
            not isinstance(item, ValidationFeedback)
            for item in self.validation_feedback
        ):
            errors.append("invalid_validation_feedback")
        errors.extend(_count(self.provider_turn_count, "provider_turn_count"))
        errors.extend(
            _count(self.executed_tool_call_count, "executed_tool_call_count")
        )
        errors.extend(
            _count(
                self.executed_persisted_call_count,
                "executed_persisted_call_count",
            )
        )
        if self.terminal_kind is not None and not isinstance(
            self.terminal_kind,
            ModelDecisionKind,
        ):
            errors.append("invalid_terminal_kind")
        errors.extend(
            _validate_bounded_text(
                self.refusal_code,
                "refusal_code",
                optional=True,
                max_length=PROVENANCE_TOKEN_MAX_LENGTH,
            )
        )
        if isinstance(self.refusal_code, str) and any(
            character.isspace() for character in self.refusal_code
        ):
            errors.append("invalid_refusal_code")
        tuples_ok = (
            isinstance(self.tool_results, tuple)
            and isinstance(self.evidence_snapshots, tuple)
            and isinstance(self.evidence_bindings, tuple)
            and isinstance(self.invocations, tuple)
            and all(isinstance(item, ToolResult) for item in self.tool_results)
            and all(
                isinstance(item, DecisionEvidenceSnapshot)
                for item in self.evidence_snapshots
            )
            and all(
                isinstance(item, DecisionEvidenceBinding)
                for item in self.evidence_bindings
            )
            and all(
                isinstance(item, DecisionToolInvocationAudit)
                for item in self.invocations
            )
        )
        if tuples_ok:
            errors.extend(
                _align_executions(
                    self.tool_results,
                    self.evidence_snapshots,
                    self.evidence_bindings,
                    self.invocations,
                )
            )
            if self.executed_tool_call_count != len(self.tool_results):
                errors.append("executed_tool_call_count_mismatch")
            persisted = sum(
                1
                for item in self.tool_results
                if item.tool_name == "get_persisted_airport_candidate_evidence"
            )
            if self.executed_persisted_call_count != persisted:
                errors.append("executed_persisted_call_count_mismatch")
        if isinstance(self.status, DecisionSpecialistRunStatus):
            final = self.status in {
                DecisionSpecialistRunStatus.COMPLETED,
                DecisionSpecialistRunStatus.PARTIAL,
            }
            if final:
                if self.verification is None or self.render is None:
                    errors.append("final_status_requires_verification_and_render")
                if self.unsupported_reason is not None:
                    errors.append("final_status_forbids_unsupported_reason")
                if self.runtime_errors:
                    errors.append("final_status_forbids_runtime_errors")
                if not self.proposed_claims:
                    errors.append("final_status_requires_claims")
                if self.terminal_kind is not ModelDecisionKind.FINAL_CLAIMS:
                    errors.append("final_status_requires_final_claims")
                if (
                    isinstance(self.proposed_claims, tuple)
                    and isinstance(self.evidence_bindings, tuple)
                ):
                    errors.extend(
                        _final_artifact_integrity(
                            self.proposed_claims,
                            self.evidence_bindings,
                            self.verification,
                            self.render,
                        )
                    )
            else:
                if self.verification is not None or self.render is not None:
                    errors.append("nonfinal_status_forbids_verification_and_render")
                if self.proposed_claims:
                    errors.append("nonfinal_status_forbids_claims")
            if self.status is DecisionSpecialistRunStatus.COMPLETED:
                if self.collection_partial_reason is not None:
                    errors.append("completed_forbids_collection_partial_reason")
            elif self.status is DecisionSpecialistRunStatus.PARTIAL:
                if self.collection_partial_reason is None:
                    errors.append("partial_requires_collection_partial_reason")
            elif self.status is DecisionSpecialistRunStatus.UNSUPPORTED:
                if self.unsupported_reason is None:
                    errors.append("unsupported_requires_reason")
                if self.runtime_errors:
                    errors.append("unsupported_forbids_runtime_errors")
            else:
                if self.unsupported_reason is not None:
                    errors.append("status_forbids_unsupported_reason")
            if self.status is DecisionSpecialistRunStatus.UNAVAILABLE:
                if self.runtime_errors != (
                    DecisionRuntimeErrorCode.ADAPTER_EXECUTION_FAILED,
                ):
                    errors.append("unavailable_requires_adapter_failure")
            elif self.status is DecisionSpecialistRunStatus.PROVIDER_FAILED:
                if not self.runtime_errors:
                    errors.append("provider_failed_requires_runtime_error")
            elif self.status is DecisionSpecialistRunStatus.INVALID_REQUEST:
                if self.runtime_errors:
                    errors.append("invalid_request_forbids_runtime_errors")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "mode": self.mode.value,
            "aircraft_id": self.aircraft_id,
            "recommendation_id": self.recommendation_id,
            "tool_results": [item.to_dict() for item in self.tool_results],
            "evidence_snapshots": [item.to_dict() for item in self.evidence_snapshots],
            "evidence_bindings": [
                {
                    "evidence_ref": item.evidence_ref,
                    "raw_tool_result": item.raw_tool_result.to_dict(),
                }
                for item in self.evidence_bindings
            ],
            "invocations": [item.to_dict() for item in self.invocations],
            "proposed_claims": [item.to_dict() for item in self.proposed_claims],
            "verification": (
                None if self.verification is None else self.verification.to_dict()
            ),
            "render": (
                None
                if self.render is None
                else {
                    "outcome": self.render.outcome.value,
                    "answer": self.render.answer,
                }
            ),
            "unsupported_reason": (
                None
                if self.unsupported_reason is None
                else self.unsupported_reason.value
            ),
            "runtime_errors": [item.value for item in self.runtime_errors],
            "collection_partial_reason": (
                None
                if self.collection_partial_reason is None
                else self.collection_partial_reason.value
            ),
            "validation_feedback": [
                item.to_dict() for item in self.validation_feedback
            ],
            "provider_turn_count": self.provider_turn_count,
            "executed_tool_call_count": self.executed_tool_call_count,
            "executed_persisted_call_count": self.executed_persisted_call_count,
            "terminal_kind": (
                None if self.terminal_kind is None else self.terminal_kind.value
            ),
            "refusal_code": self.refusal_code,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionSpecialistRunResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_specialist_run")
        extra = set(data) - _RUN_KEYS
        if extra:
            raise ContractValidationError("unexpected_decision_run_field")
        verification = data["verification"] if "verification" in data else None
        render = data["render"] if "render" in data else None
        unsupported = (
            data["unsupported_reason"] if "unsupported_reason" in data else None
        )
        partial = (
            data["collection_partial_reason"]
            if "collection_partial_reason" in data
            else None
        )
        terminal = data["terminal_kind"] if "terminal_kind" in data else None
        return cls(
            schema_version=_required(data, "schema_version"),
            status=_parse_enum(
                DecisionSpecialistRunStatus,
                _required(data, "status"),
                "status",
            ),
            mode=_parse_enum(DecisionTargetMode, _required(data, "mode"), "mode"),
            aircraft_id=data["aircraft_id"] if "aircraft_id" in data else None,
            recommendation_id=(
                data["recommendation_id"] if "recommendation_id" in data else None
            ),
            tool_results=tuple(
                ToolResult.from_dict(item)
                for item in _sequence(_required(data, "tool_results"), "tool_results")
            ),
            evidence_snapshots=tuple(
                DecisionEvidenceSnapshot.from_dict(item)
                for item in _sequence(
                    _required(data, "evidence_snapshots"),
                    "evidence_snapshots",
                )
            ),
            evidence_bindings=tuple(
                _binding_from_dict(item)
                for item in _sequence(
                    _required(data, "evidence_bindings"),
                    "evidence_bindings",
                )
            ),
            invocations=tuple(
                DecisionToolInvocationAudit.from_dict(item)
                for item in _sequence(_required(data, "invocations"), "invocations")
            ),
            proposed_claims=tuple(
                decision_claim_from_dict(item)
                for item in _sequence(
                    _required(data, "proposed_claims"),
                    "proposed_claims",
                )
            ),
            verification=(
                None
                if verification is None
                else DecisionVerificationResult.from_dict(verification)
            ),
            render=None if render is None else _render_from_dict(render),
            unsupported_reason=(
                None
                if unsupported is None
                else _parse_enum(
                    DecisionUnsupportedReason,
                    unsupported,
                    "unsupported_reason",
                )
            ),
            runtime_errors=tuple(
                _parse_enum(DecisionRuntimeErrorCode, item, "runtime_errors")
                for item in _sequence(
                    _required(data, "runtime_errors"),
                    "runtime_errors",
                )
            ),
            collection_partial_reason=(
                None
                if partial is None
                else _parse_enum(
                    DecisionCollectionPartialReason,
                    partial,
                    "collection_partial_reason",
                )
            ),
            validation_feedback=tuple(
                ValidationFeedback.from_dict(item)
                for item in _sequence(
                    _required(data, "validation_feedback"),
                    "validation_feedback",
                )
            ),
            provider_turn_count=_required(data, "provider_turn_count"),
            executed_tool_call_count=_required(data, "executed_tool_call_count"),
            executed_persisted_call_count=_required(
                data,
                "executed_persisted_call_count",
            ),
            terminal_kind=(
                None
                if terminal is None
                else _parse_enum(ModelDecisionKind, terminal, "terminal_kind")
            ),
            refusal_code=data["refusal_code"] if "refusal_code" in data else None,
        )

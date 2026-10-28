"""Native request and result adapter for one Master-planned Live Ops invocation.

``prepare_master_live_ops_native_bridge`` accepts one validated
``MasterLiveOpsBinding`` and preserves its ``LiveOpsRunRequest``, specialist
request id, and invocation identity. ``accept_native_live_ops_result`` checks
a native ``LiveOpsRunResult`` against that identity and the host-supplied
source observation epoch, then maps the result's own failure, cancellation,
partial verification, and claim state.

The specialist is not invoked here. ``LiveOpsSpecialist.run`` needs a trusted
host execution grant: reviewed application-ingress authentication plus
execution-policy approval of that invocation. ``MasterLiveOpsBinding``,
execution readiness, matching identifiers, and a native result are not that
grant. ``LIVE_OPS_MISSING_HOST_EXECUTION_GRANT`` names the missing dependency.

This module does not construct an AWS client, a model provider, trusted
tables, or a trusted context. It does not authorize tool selectors. A
regional string or a census-region intersection is not verified SIGMET
geographic coverage. A test-produced result is not authenticated source
evidence. Absence of a count is not a verified zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LiveOpsEntityHint,
    LiveOpsFailureReason,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsSelectorHints,
    live_ops_reference_utc,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import MasterLiveOpsBinding
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
)


MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION = "wilvor.ai.master_live_ops_native_bridge.v1"

LIVE_OPS_MISSING_HOST_EXECUTION_GRANT = "trusted_host_execution_grant"

_ZERO_KIND = LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
_ABSENT_KIND = LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT
_COUNT_KIND = LiveOpsPublicClaimKind.SCOPED_COUNT
_IMPACT_KIND = LiveOpsPublicClaimKind.CONFIRMED_IMPACT
_REGION_KIND = LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION
_COUNT_KINDS = frozenset({_ZERO_KIND, _COUNT_KIND, _ABSENT_KIND})


class LiveOpsNativeHostInputSource(str, Enum):
    """Where a runtime value would have to come from. Not a grant."""

    PRESERVED_BINDING = "PRESERVED_BINDING"
    FUTURE_TRUSTED_HOST = "FUTURE_TRUSTED_HOST"
    MISSING_EXECUTION_GRANT = "MISSING_EXECUTION_GRANT"


class LiveOpsNativeResultDisposition(str, Enum):
    """Closed map of one native result. It does not add evidence."""

    MODEL_REFUSAL = "MODEL_REFUSAL"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    CANCELLED = "CANCELLED"
    TOOL_FAILURE = "TOOL_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    ALL_VERIFIED = "ALL_VERIFIED"
    NONE_VERIFIED = "NONE_VERIFIED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class LiveOpsNativeCountState(str, Enum):
    """Count observation copied from claims that are already on the result."""

    ABSENT = "ABSENT"
    VERIFIED_ZERO = "VERIFIED_ZERO"
    CONTEXT_ROOT_ABSENT = "CONTEXT_ROOT_ABSENT"
    SCOPED_COUNT = "SCOPED_COUNT"
    MIXED = "MIXED"


@dataclass(frozen=True)
class LiveOpsNativeHostInput:
    """One runtime input the future host must supply. This object holds no client."""

    parameter: str
    owner: str
    source: LiveOpsNativeHostInputSource
    value_preserved: bool
    preserved_value: str | None = None
    satisfied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.parameter, str) or not self.parameter:
            raise ContractValidationError("invalid_host_input")
        if not isinstance(self.owner, str) or not self.owner:
            raise ContractValidationError("invalid_host_input")
        if not isinstance(self.source, LiveOpsNativeHostInputSource):
            raise ContractValidationError("invalid_host_input")
        if not isinstance(self.value_preserved, bool) or self.satisfied is not False:
            raise ContractValidationError("host_input_not_satisfied")
        if self.value_preserved:
            if self.source is not LiveOpsNativeHostInputSource.PRESERVED_BINDING:
                raise ContractValidationError("invalid_host_input")
            if not isinstance(self.preserved_value, str) or not self.preserved_value:
                raise ContractValidationError("invalid_host_input")
            return
        if self.preserved_value is not None:
            raise ContractValidationError("invalid_host_input")
        if self.source is LiveOpsNativeHostInputSource.PRESERVED_BINDING:
            raise ContractValidationError("invalid_host_input")


@dataclass(frozen=True)
class LiveOpsRegionalStringMatch:
    """A region string already present on a native claim.

    Census region fields and a region query are not SIGMET geographic coverage.
    """

    region_query: str
    claim_kind: LiveOpsPublicClaimKind
    region_name: str | None = None
    region_code: str | None = None
    product_type: str | None = None
    sigmet_geographic_coverage: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.region_query, str) or not self.region_query:
            raise ContractValidationError("invalid_regional_string_match")
        if not isinstance(self.claim_kind, LiveOpsPublicClaimKind):
            raise ContractValidationError("invalid_regional_string_match")
        for name in ("region_name", "region_code", "product_type"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractValidationError("invalid_regional_string_match")
        if self.sigmet_geographic_coverage is not False:
            raise ContractValidationError("geospatial_coverage_not_established")


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _withheld_values(hints: MasterRouteHints) -> frozenset[str]:
    values: list[str] = []
    for item in (
        hints.callsign,
        hints.typed_aircraft_id,
        hints.recommendation_id,
        hints.region_text,
    ):
        if isinstance(item, str):
            values.append(item)
    values.extend(hints.population_aircraft_ids)
    return frozenset(values)


def _host_input(
    parameter: str,
    owner: str,
    source: LiveOpsNativeHostInputSource,
    *,
    preserved_value: str | None = None,
) -> LiveOpsNativeHostInput:
    preserved = preserved_value is not None
    return LiveOpsNativeHostInput(
        parameter=parameter,
        owner=owner,
        source=source,
        value_preserved=preserved,
        preserved_value=preserved_value,
        satisfied=False,
    )


def _host_inputs(binding: MasterLiveOpsBinding) -> tuple[LiveOpsNativeHostInput, ...]:
    """Exact inputs of the native specialist entry, context, and missing grant.

    The public request and correlation id are preserved from the binding.
    Neither value satisfies a host input. ``now_epoch`` is the source
    observation clock and is left unset. The application request time is not
    copied into it. No selector parameter is listed, because model and user
    hints are not authorized selectors.
    """

    preserved = LiveOpsNativeHostInputSource.PRESERVED_BINDING
    host = LiveOpsNativeHostInputSource.FUTURE_TRUSTED_HOST
    missing = LiveOpsNativeHostInputSource.MISSING_EXECUTION_GRANT
    run = "LiveOpsSpecialist.run"
    init = "LiveOpsSpecialist.__init__"
    context = "LiveOpsTrustedContext"
    return (
        _host_input("request", run, preserved, preserved_value=binding.specialist_request_id),
        _host_input("context", run, host),
        _host_input("provider", init, host),
        _host_input("handlers", init, host),
        _host_input("tool_call_id_factory", init, host),
        _host_input("tables", context, host),
        _host_input("now_epoch", context, host),
        _host_input(
            "correlation_id",
            context,
            preserved,
            preserved_value=binding.correlation_id,
        ),
        _host_input("max_model_turns", context, host),
        _host_input("max_tool_calls", context, host),
        _host_input("clock", context, host),
        _host_input("deadline_epoch", context, host),
        _host_input("cancelled", context, host),
        _host_input("execution_grant", "trusted_host_invocation", missing),
    )


def _kind(claims: tuple[LiveOpsPublicClaim, ...], kind: LiveOpsPublicClaimKind) -> tuple[LiveOpsPublicClaim, ...]:
    return tuple(item for item in claims if item.kind is kind)


def _count_state(claims: tuple[LiveOpsPublicClaim, ...]) -> LiveOpsNativeCountState:
    present = {item.kind for item in claims if item.kind in _COUNT_KINDS}
    if not present:
        return LiveOpsNativeCountState.ABSENT
    if len(present) > 1:
        return LiveOpsNativeCountState.MIXED
    only = next(iter(present))
    if only is _ZERO_KIND:
        return LiveOpsNativeCountState.VERIFIED_ZERO
    if only is _ABSENT_KIND:
        return LiveOpsNativeCountState.CONTEXT_ROOT_ABSENT
    return LiveOpsNativeCountState.SCOPED_COUNT


def _disposition(result: LiveOpsRunResult) -> LiveOpsNativeResultDisposition:
    status = result.execution_status
    if status is LiveOpsRunStatus.REFUSED:
        return LiveOpsNativeResultDisposition.MODEL_REFUSAL
    if status is LiveOpsRunStatus.PROVIDER_FAILED:
        return LiveOpsNativeResultDisposition.PROVIDER_FAILURE
    if status is LiveOpsRunStatus.PROVIDER_TIMEOUT:
        return LiveOpsNativeResultDisposition.PROVIDER_TIMEOUT
    if status is LiveOpsRunStatus.RUNTIME_TIMEOUT:
        return LiveOpsNativeResultDisposition.RUNTIME_TIMEOUT
    if status is LiveOpsRunStatus.CANCELLED:
        return LiveOpsNativeResultDisposition.CANCELLED
    if status is LiveOpsRunStatus.TOOL_FAILED:
        return LiveOpsNativeResultDisposition.TOOL_FAILURE
    if status is LiveOpsRunStatus.BUDGET_EXHAUSTED:
        return LiveOpsNativeResultDisposition.BUDGET_EXHAUSTED
    if status is LiveOpsRunStatus.INVALID_REQUEST:
        return LiveOpsNativeResultDisposition.INVALID_REQUEST
    if status is LiveOpsRunStatus.UNSUPPORTED:
        return LiveOpsNativeResultDisposition.UNSUPPORTED
    if status is LiveOpsRunStatus.UNAVAILABLE:
        return LiveOpsNativeResultDisposition.UNAVAILABLE
    if status is not LiveOpsRunStatus.COMPLETED:
        raise ContractValidationError("invalid_execution_status")
    outcome = result.verification_outcome
    if outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED:
        return LiveOpsNativeResultDisposition.PARTIALLY_VERIFIED
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
        return LiveOpsNativeResultDisposition.ALL_VERIFIED
    if outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        return LiveOpsNativeResultDisposition.NONE_VERIFIED
    if outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        return LiveOpsNativeResultDisposition.NOT_AUTHORIZED
    raise ContractValidationError("invalid_verification_outcome")


def _regional_matches(
    claims: tuple[LiveOpsPublicClaim, ...],
) -> tuple[LiveOpsRegionalStringMatch, ...]:
    found: list[LiveOpsRegionalStringMatch] = []
    for claim in claims:
        if claim.kind is _REGION_KIND:
            if not isinstance(claim.region_query, str):
                raise ContractValidationError("invalid_regional_string_match")
            found.append(
                LiveOpsRegionalStringMatch(
                    region_query=claim.region_query,
                    claim_kind=claim.kind,
                    region_name=claim.region_name,
                    region_code=claim.region_code,
                )
            )
            continue
        scope = claim.scope
        if scope is None or scope.region is None:
            continue
        if claim.kind not in {_COUNT_KIND, _ZERO_KIND}:
            continue
        found.append(
            LiveOpsRegionalStringMatch(
                region_query=scope.region,
                claim_kind=claim.kind,
                product_type=scope.product_type,
            )
        )
    return tuple(found)


def _require_bridge(binding: MasterLiveOpsBinding, bridge: "MasterLiveOpsNativeBridge") -> None:
    if bridge.schema_version != MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    if bridge.binding is not binding:
        raise ContractValidationError("invalid_live_ops_binding")
    if bridge.request is not binding.request:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(bridge.request, LiveOpsRunRequest):
        raise ContractValidationError("invalid_live_ops_request")
    if bridge.request.schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    if bridge.missing_integration_dependency != LIVE_OPS_MISSING_HOST_EXECUTION_GRANT:
        raise ContractValidationError("missing_host_execution_grant")
    _closed(
        bridge.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        bridge.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        bridge.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        bridge.request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    for name, code in (
        ("dispatch_permitted", "execution_not_granted"),
        ("executable", "execution_not_granted"),
        ("specialist_invoked", "execution_not_granted"),
        ("selectors_authorized", "selection_hints_not_trusted"),
        ("callsign_authenticated", "callsign_not_authenticated_aircraft"),
        ("geospatial_coverage_established", "geospatial_coverage_not_established"),
        (
            "regional_string_match_is_sigmet_coverage",
            "geospatial_coverage_not_established",
        ),
        (
            "census_region_resolution_is_sigmet_coverage",
            "geospatial_coverage_not_established",
        ),
        ("application_request_time_is_source_clock", "application_time_is_not_source_clock"),
        ("authenticated_source_evidence", "source_authenticity_not_established"),
        ("native_execution_is_source_evidence", "source_authenticity_not_established"),
        ("verified_zero", "verified_zero_not_established"),
        ("absence_counted_as_zero", "verified_zero_not_established"),
    ):
        if getattr(bridge, name) is not False:
            raise ContractValidationError(code)
    if bridge.aircraft_impact_count is not None:
        raise ContractValidationError("aircraft_impact_count_not_authoritative")
    if bridge.master_request_id != binding.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if bridge.correlation_id != binding.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if bridge.application_request_time_utc != binding.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    if bridge.invocation_id != binding.invocation_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if bridge.step_id != binding.step_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if bridge.specialist_request_id != binding.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if bridge.request.request_id != bridge.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if bridge.request.user_text != binding.canonical_user_text:
        raise ContractValidationError("invocation_binding_mismatch")
    if bridge.request.selector_hints is not None or bridge.request.entity_hints != ():
        raise ContractValidationError("selection_hints_not_trusted")
    if bridge.withheld_untrusted_hints != binding.withheld_untrusted_hints:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(bridge.withheld_untrusted_hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")
    if bridge.host_inputs != _host_inputs(binding):
        raise ContractValidationError("host_input_mismatch")
    withheld = _withheld_values(binding.withheld_untrusted_hints)
    if binding.withheld_expected_aircraft_id is not None:
        withheld = withheld | {binding.withheld_expected_aircraft_id}
    if binding.withheld_expected_recommendation_id is not None:
        withheld = withheld | {binding.withheld_expected_recommendation_id}
    for item in bridge.host_inputs:
        if item.satisfied is not False:
            raise ContractValidationError("host_input_not_satisfied")
        if item.parameter == "now_epoch" and item.preserved_value is not None:
            raise ContractValidationError("application_time_is_not_source_clock")
        if item.preserved_value is not None and item.preserved_value in withheld:
            raise ContractValidationError("preserved_identity_matches_untrusted_hint")
        if item.preserved_value == binding.application_request_time_utc:
            raise ContractValidationError("application_time_is_not_source_clock")
    grant = [item for item in bridge.host_inputs if item.parameter == "execution_grant"]
    if len(grant) != 1 or grant[0].source is not LiveOpsNativeHostInputSource.MISSING_EXECUTION_GRANT:
        raise ContractValidationError("missing_host_execution_grant")
    if grant[0].satisfied is not False or grant[0].value_preserved is not False:
        raise ContractValidationError("missing_host_execution_grant")


@dataclass(frozen=True)
class MasterLiveOpsNativeBridge:
    """Preserved Live Ops request plus the host inputs still required to run it.

    Construction does not invoke the specialist and does not authorize execution.
    """

    binding: MasterLiveOpsBinding
    request: LiveOpsRunRequest
    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    invocation_id: str
    step_id: str
    specialist_request_id: str
    withheld_untrusted_hints: MasterRouteHints
    host_inputs: tuple[LiveOpsNativeHostInput, ...]
    missing_integration_dependency: str = LIVE_OPS_MISSING_HOST_EXECUTION_GRANT
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    executable: bool = False
    specialist_invoked: bool = False
    selectors_authorized: bool = False
    callsign_authenticated: bool = False
    geospatial_coverage_established: bool = False
    regional_string_match_is_sigmet_coverage: bool = False
    census_region_resolution_is_sigmet_coverage: bool = False
    application_request_time_is_source_clock: bool = False
    authenticated_source_evidence: bool = False
    native_execution_is_source_evidence: bool = False
    aircraft_impact_count: int | None = None
    verified_zero: bool = False
    absence_counted_as_zero: bool = False
    schema_version: str = MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MasterLiveOpsBinding):
            raise ContractValidationError("invalid_live_ops_binding")
        _require_bridge(self.binding, self)


@dataclass(frozen=True)
class MasterLiveOpsNativeResult:
    """Validated view of one native result. Claims are the result's own objects.

    ``source_observation_epoch`` is the epoch supplied for this result.
    ``application_request_time_utc`` stays the planned request time. Neither
    field is derived from the other. Verified native claims stay unauthenticated
    source evidence. A query-result zero is recorded only when that claim is
    already on the result. Context-root absence and a missing count stay absent.
    """

    bridge: MasterLiveOpsNativeBridge
    native_result: LiveOpsRunResult
    source_observation_epoch: int
    disposition: LiveOpsNativeResultDisposition
    count_state: LiveOpsNativeCountState
    verified_claims: tuple[LiveOpsPublicClaim, ...]
    native_query_result_zero_claims: tuple[LiveOpsPublicClaim, ...]
    context_root_absent_claims: tuple[LiveOpsPublicClaim, ...]
    scoped_count_claims: tuple[LiveOpsPublicClaim, ...]
    confirmed_impact_claims: tuple[LiveOpsPublicClaim, ...]
    regional_string_matches: tuple[LiveOpsRegionalStringMatch, ...]
    regional_match_repeats_withheld_region_text: bool
    failure_reason: LiveOpsFailureReason | None
    refusal_code: str | None
    failed_tool_name: str | None
    verification_outcome: LiveOpsPublicVerificationOutcome | None
    request_id: str | None
    correlation_id: str
    invocation_id: str
    step_id: str
    specialist_request_id: str
    application_request_time_utc: str
    reference_utc: str
    unobserved_metrics_recorded_as_zero: tuple[str, ...] = ()
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    executable: bool = False
    specialist_invoked_by_bridge: bool = False
    selectors_authorized: bool = False
    callsign_authenticated: bool = False
    native_entity_ref_authorizes_selector: bool = False
    geospatial_coverage_established: bool = False
    regional_string_match_is_sigmet_coverage: bool = False
    census_region_resolution_is_sigmet_coverage: bool = False
    application_request_time_is_source_clock: bool = False
    authenticated_source_evidence: bool = False
    native_execution_is_source_evidence: bool = False
    verified_claims_are_complete_coverage: bool = False
    aircraft_impact_count: int | None = None
    verified_zero: bool = False
    absence_counted_as_zero: bool = False
    schema_version: str = MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.bridge, MasterLiveOpsNativeBridge):
            raise ContractValidationError("invalid_live_ops_bridge")
        _require_bridge(self.bridge.binding, self.bridge)
        if not isinstance(self.native_result, LiveOpsRunResult):
            raise ContractValidationError("invalid_live_ops_result")
        if type(self.source_observation_epoch) is not int:
            raise ContractValidationError("invalid_source_observation_epoch")
        _check_result_identity(self.bridge, self.native_result, self.source_observation_epoch)
        claims = self.native_result.verified_claims
        if self.verified_claims is not claims:
            raise ContractValidationError("verified_claims_not_native")
        if self.native_query_result_zero_claims != _kind(claims, _ZERO_KIND):
            raise ContractValidationError("invented_verified_zero")
        if self.context_root_absent_claims != _kind(claims, _ABSENT_KIND):
            raise ContractValidationError("invented_absence_claim")
        if self.scoped_count_claims != _kind(claims, _COUNT_KIND):
            raise ContractValidationError("invented_count_claim")
        if self.confirmed_impact_claims != _kind(claims, _IMPACT_KIND):
            raise ContractValidationError("aircraft_impact_count_not_authoritative")
        if self.count_state is not _count_state(claims):
            raise ContractValidationError("count_state_mismatch")
        if self.disposition is not _disposition(self.native_result):
            raise ContractValidationError("result_disposition_mismatch")
        if self.regional_string_matches != _regional_matches(claims):
            raise ContractValidationError("regional_string_match_mismatch")
        withheld_region = self.bridge.withheld_untrusted_hints.region_text
        repeats = bool(withheld_region) and any(
            item.region_query == withheld_region for item in self.regional_string_matches
        )
        if self.regional_match_repeats_withheld_region_text is not repeats:
            raise ContractValidationError("regional_string_match_mismatch")
        for item in self.regional_string_matches:
            if item.sigmet_geographic_coverage is not False:
                raise ContractValidationError("geospatial_coverage_not_established")
        failure = self.native_result.failure
        expected_reason = None if failure is None else failure.reason
        expected_refusal = None if failure is None else failure.refusal_code
        expected_tool = None if failure is None else failure.failed_tool_name
        if self.failure_reason is not expected_reason:
            raise ContractValidationError("failure_reason_mismatch")
        if self.refusal_code != expected_refusal or self.failed_tool_name != expected_tool:
            raise ContractValidationError("failure_reason_mismatch")
        if self.verification_outcome is not self.native_result.verification_outcome:
            raise ContractValidationError("result_disposition_mismatch")
        if self.request_id != self.native_result.request_id:
            raise ContractValidationError("request_id_mismatch")
        if self.correlation_id != self.native_result.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.invocation_id != self.bridge.invocation_id or self.step_id != self.bridge.step_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != self.bridge.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.application_request_time_utc != self.bridge.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if self.reference_utc != self.native_result.reference_utc:
            raise ContractValidationError("source_observation_epoch_mismatch")
        if self.unobserved_metrics_recorded_as_zero != ():
            raise ContractValidationError("invented_verified_zero")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        for name, code in (
            ("dispatch_permitted", "execution_not_granted"),
            ("executable", "execution_not_granted"),
            ("specialist_invoked_by_bridge", "execution_not_granted"),
            ("selectors_authorized", "selection_hints_not_trusted"),
            ("callsign_authenticated", "callsign_not_authenticated_aircraft"),
            ("native_entity_ref_authorizes_selector", "selection_hints_not_trusted"),
            ("geospatial_coverage_established", "geospatial_coverage_not_established"),
            (
                "regional_string_match_is_sigmet_coverage",
                "geospatial_coverage_not_established",
            ),
            (
                "census_region_resolution_is_sigmet_coverage",
                "geospatial_coverage_not_established",
            ),
            ("application_request_time_is_source_clock", "application_time_is_not_source_clock"),
            ("authenticated_source_evidence", "source_authenticity_not_established"),
            ("native_execution_is_source_evidence", "source_authenticity_not_established"),
            ("verified_claims_are_complete_coverage", "partial_verification_not_complete"),
            ("verified_zero", "verified_zero_not_established"),
            ("absence_counted_as_zero", "verified_zero_not_established"),
        ):
            if getattr(self, name) is not False:
                raise ContractValidationError(code)
        if self.aircraft_impact_count is not None:
            raise ContractValidationError("aircraft_impact_count_not_authoritative")
        if self.count_state is LiveOpsNativeCountState.VERIFIED_ZERO and not self.native_query_result_zero_claims:
            raise ContractValidationError("invented_verified_zero")
        if self.count_state is LiveOpsNativeCountState.CONTEXT_ROOT_ABSENT and self.native_query_result_zero_claims:
            raise ContractValidationError("absence_counted_as_zero")
        if self.count_state is LiveOpsNativeCountState.ABSENT and (
            self.native_query_result_zero_claims or self.context_root_absent_claims or self.scoped_count_claims
        ):
            raise ContractValidationError("count_state_mismatch")
        zero_ids = {id(item) for item in claims}
        for item in self.native_query_result_zero_claims:
            if id(item) not in zero_ids or item.kind is not _ZERO_KIND:
                raise ContractValidationError("invented_verified_zero")
        for item in self.context_root_absent_claims:
            if id(item) not in zero_ids or item.kind is not _ABSENT_KIND:
                raise ContractValidationError("invented_absence_claim")


def _check_result_identity(
    bridge: MasterLiveOpsNativeBridge,
    result: LiveOpsRunResult,
    source_observation_epoch: int,
) -> None:
    if result.request_id != bridge.specialist_request_id:
        raise ContractValidationError("request_id_mismatch")
    if result.correlation_id != bridge.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if result.conversation_id is not None:
        raise ContractValidationError("conversation_id_mismatch")
    if result.reference_epoch != source_observation_epoch:
        raise ContractValidationError("source_observation_epoch_mismatch")
    if result.reference_utc != live_ops_reference_utc(source_observation_epoch):
        raise ContractValidationError("source_observation_epoch_mismatch")


def _epoch(value: object) -> int:
    if type(value) is not int:
        raise ContractValidationError("invalid_source_observation_epoch")
    return value


def prepare_master_live_ops_native_bridge(
    binding: MasterLiveOpsBinding,
) -> MasterLiveOpsNativeBridge:
    """Preserve one bound Live Ops request and name the host inputs still missing.

    The binding is not executed. Caller-supplied providers, tables, clocks,
    selectors, and authority flags are not parameters of this function.
    """

    if not isinstance(binding, MasterLiveOpsBinding):
        raise ContractValidationError("invalid_live_ops_binding")
    return MasterLiveOpsNativeBridge(
        binding=binding,
        request=binding.request,
        master_request_id=binding.master_request_id,
        correlation_id=binding.correlation_id,
        application_request_time_utc=binding.application_request_time_utc,
        invocation_id=binding.invocation_id,
        step_id=binding.step_id,
        specialist_request_id=binding.specialist_request_id,
        withheld_untrusted_hints=binding.withheld_untrusted_hints,
        host_inputs=_host_inputs(binding),
    )


def reject_unauthorized_selectors(
    bridge: MasterLiveOpsNativeBridge,
    *,
    region: str | None = None,
    callsign: str | None = None,
    aircraft_id: str | None = None,
    hazard_id: str | None = None,
    hazard_ids: tuple[str, ...] | None = None,
    airport_id: str | None = None,
    recommendation_id: str | None = None,
    population_aircraft_ids: tuple[str, ...] | None = None,
    selector_hints: LiveOpsSelectorHints | None = None,
    entity_hints: tuple[LiveOpsEntityHint, ...] = (),
) -> None:
    """Refuse model and user hints presented as authorized tool selectors.

    An empty call authorizes nothing. Any supplied selector is rejected.
    """

    if not isinstance(bridge, MasterLiveOpsNativeBridge):
        raise ContractValidationError("invalid_live_ops_bridge")
    _require_bridge(bridge.binding, bridge)
    if selector_hints is not None and not isinstance(selector_hints, LiveOpsSelectorHints):
        raise ContractValidationError("invalid_selector_hints")
    if not isinstance(entity_hints, tuple) or any(
        not isinstance(item, LiveOpsEntityHint) for item in entity_hints
    ):
        raise ContractValidationError("invalid_entity_hints")
    hinted_callsign = callsign is not None or any(item.callsign is not None for item in entity_hints)
    if selector_hints is not None and selector_hints.callsign is not None:
        hinted_callsign = True
    if hinted_callsign:
        raise ContractValidationError("callsign_not_authenticated_aircraft")
    supplied = (
        region is not None
        or aircraft_id is not None
        or hazard_id is not None
        or hazard_ids is not None
        or airport_id is not None
        or recommendation_id is not None
        or population_aircraft_ids is not None
        or selector_hints is not None
        or bool(entity_hints)
    )
    if supplied:
        raise ContractValidationError("selection_hints_not_trusted")


def accept_native_live_ops_result(
    bridge: MasterLiveOpsNativeBridge,
    result: LiveOpsRunResult,
    *,
    source_observation_epoch: int,
) -> MasterLiveOpsNativeResult:
    """Validate one native result and map its own state.

    Identity failures raise. They do not produce an admitted claim set.
    Accepting a matching result does not grant execution and does not make
    the result authenticated source evidence.
    """

    if not isinstance(bridge, MasterLiveOpsNativeBridge):
        raise ContractValidationError("invalid_live_ops_bridge")
    _require_bridge(bridge.binding, bridge)
    if not isinstance(result, LiveOpsRunResult):
        raise ContractValidationError("invalid_live_ops_result")
    epoch = _epoch(source_observation_epoch)
    _check_result_identity(bridge, result, epoch)
    claims = result.verified_claims
    failure = result.failure
    withheld_region = bridge.withheld_untrusted_hints.region_text
    matches = _regional_matches(claims)
    return MasterLiveOpsNativeResult(
        bridge=bridge,
        native_result=result,
        source_observation_epoch=epoch,
        disposition=_disposition(result),
        count_state=_count_state(claims),
        verified_claims=claims,
        native_query_result_zero_claims=_kind(claims, _ZERO_KIND),
        context_root_absent_claims=_kind(claims, _ABSENT_KIND),
        scoped_count_claims=_kind(claims, _COUNT_KIND),
        confirmed_impact_claims=_kind(claims, _IMPACT_KIND),
        regional_string_matches=matches,
        regional_match_repeats_withheld_region_text=bool(withheld_region)
        and any(item.region_query == withheld_region for item in matches),
        failure_reason=None if failure is None else failure.reason,
        refusal_code=None if failure is None else failure.refusal_code,
        failed_tool_name=None if failure is None else failure.failed_tool_name,
        verification_outcome=result.verification_outcome,
        request_id=result.request_id,
        correlation_id=result.correlation_id,
        invocation_id=bridge.invocation_id,
        step_id=bridge.step_id,
        specialist_request_id=bridge.specialist_request_id,
        application_request_time_utc=bridge.application_request_time_utc,
        reference_utc=result.reference_utc,
    )


__all__ = [
    "LIVE_OPS_MISSING_HOST_EXECUTION_GRANT",
    "MASTER_LIVE_OPS_NATIVE_BRIDGE_SCHEMA_VERSION",
    "LiveOpsNativeCountState",
    "LiveOpsNativeHostInput",
    "LiveOpsNativeHostInputSource",
    "LiveOpsNativeResultDisposition",
    "LiveOpsRegionalStringMatch",
    "MasterLiveOpsNativeBridge",
    "MasterLiveOpsNativeResult",
    "accept_native_live_ops_result",
    "prepare_master_live_ops_native_bridge",
    "reject_unauthorized_selectors",
]

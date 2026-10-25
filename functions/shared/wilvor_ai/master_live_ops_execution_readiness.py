"""Non-executing readiness for one Master-planned Live Ops invocation.

``assess_master_live_ops_execution_readiness`` reuses
``prepare_master_live_ops_binding``. That binder already builds the native
``LiveOpsRunRequest`` from canonical user text and the planned specialist
request id. Selector hints stay off that request.

This module does not construct ``LiveOpsTrustedContext``,
``LiveOpsSpecialistTrustedContext``, ``LiveOpsCall``, or ``OperationalTables``.
It does not call ``LiveOpsSpecialist.run``, ``LiveOpsAdapter.dispatch``, a
model provider, ``resolve_region``, or any read-only Live Ops tool. It does
not create a ``ToolResult``.

The catalog below is the real invocation chain a later implementation must
connect. It is not a new adapter. Two real signatures are explicitly not that
connection:

- ``inspect.signature(LiveOpsSpecialistPort)`` is ``*args, **kwargs``. The
  method to connect is ``LiveOpsSpecialistPort.run`` / ``LiveOpsSpecialist.run``.
- ``decode_and_run_live_ops`` accepts a ``Mapping``, not the prepared
  ``LiveOpsRunRequest``.

``search_current_hazards`` and ``search_current_impacts`` mark ``region``
optional. ``LiveOpsAdapter`` still rejects a hazard search without ``region``
or ``hazard_ids`` and an impact search without those selectors. An optional
parameter is not an authorized selector. Model region text, callsigns, and
typed identifiers stay withheld.

``resolve_region`` can resolve a census state name. That resolution is not
SIGMET geometry and does not establish geospatial coverage. This assessment
does not pass plan text to it.

``AgentAuthorityMode.READ_ONLY_ADVISORY`` on the eight Live Ops tools is not
source-read permission. The plan's ``application_request_time_utc`` is not
``now_epoch`` or ``clock``.

Execution authority stays ``DENIED``, production authority stays ``DENIED``,
source authenticity stays ``NOT_ESTABLISHED``, and request coverage stays
``NOT_ESTABLISHED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LiveOpsRunRequest,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import (
    MasterLiveOpsBinding,
    prepare_master_live_ops_binding,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
)
from wilvor_ai.master_specialist_adapters import MasterSpecialistAdapterDescriptor


MASTER_LIVE_OPS_EXECUTION_READINESS_SCHEMA_VERSION = (
    "wilvor.ai.master_live_ops_execution_readiness.v1"
)

_READ_ONLY_ADVISORY = "READ_ONLY_ADVISORY"
_PK = "POSITIONAL_OR_KEYWORD"
_KW = "KEYWORD_ONLY"
_VAR_POSITIONAL = "VAR_POSITIONAL"
_VAR_KEYWORD = "VAR_KEYWORD"
_PARAMETER_KINDS = frozenset({_PK, _KW, _VAR_POSITIONAL, _VAR_KEYWORD})
_INSPECT_VIA = frozenset({"class", "callable"})
_REGION_PARAMETERS = frozenset({"region", "region_query"})
_CALLSIGN_PARAMETERS = frozenset({"callsign"})
_AIRCRAFT_PARAMETERS = frozenset({"aircraft_id"})
_OTHER_SELECTOR_PARAMETERS = frozenset({"hazard_id", "hazard_ids", "airport_id"})
_FILTER_PARAMETERS = frozenset({"product_type", "hazard_type"})

LIVE_OPS_READ_ONLY_TOOL_NAMES = (
    "search_current_hazards",
    "search_current_impacts",
    "search_current_encounters",
    "get_observed_network_state",
    "get_aircraft_operational_context",
    "get_hazard_operational_context",
    "get_airport_operational_context",
    "find_aircraft_by_callsign",
)


class LiveOpsConnectionDisposition(str, Enum):
    """How a real callable relates to a later Master connection."""

    MUST_CONNECT = "MUST_CONNECT"
    NOT_THE_MASTER_CONNECTION = "NOT_THE_MASTER_CONNECTION"
    NOT_COVERAGE_AUTHORITY = "NOT_COVERAGE_AUTHORITY"


class LiveOpsParameterRole(str, Enum):
    """Role of one real parameter. Not a new adapter field."""

    RECEIVER = "RECEIVER"
    PUBLIC_REQUEST = "PUBLIC_REQUEST"
    UNTRUSTED_MAPPING = "UNTRUSTED_MAPPING"
    MODEL_TURN_REQUEST = "MODEL_TURN_REQUEST"
    TRUSTED_CONTEXT = "TRUSTED_CONTEXT"
    TRUSTED_TABLES = "TRUSTED_TABLES"
    TABLE_HANDLE = "TABLE_HANDLE"
    CORRELATION_ID = "CORRELATION_ID"
    SOURCE_OBSERVATION_CLOCK = "SOURCE_OBSERVATION_CLOCK"
    DEADLINE_CLOCK = "DEADLINE_CLOCK"
    DEADLINE_EPOCH = "DEADLINE_EPOCH"
    CANCELLATION = "CANCELLATION"
    BUDGET = "BUDGET"
    MODEL_PROVIDER = "MODEL_PROVIDER"
    TOOL_HANDLERS = "TOOL_HANDLERS"
    TOOL_CALL_ID_FACTORY = "TOOL_CALL_ID_FACTORY"
    TOOL_CALL_ID = "TOOL_CALL_ID"
    PROPOSED_TOOL_CALLS = "PROPOSED_TOOL_CALLS"
    LIVE_OPS_CALL = "LIVE_OPS_CALL"
    AUTHORIZED_SELECTOR = "AUTHORIZED_SELECTOR"
    UNTRUSTED_FILTER = "UNTRUSTED_FILTER"
    CLAIMED_REGION_TEXT = "CLAIMED_REGION_TEXT"
    SPECIALIST_INSTANCE = "SPECIALIST_INSTANCE"
    VARARGS = "VARARGS"


class MasterLiveOpsExecutionBlocker(str, Enum):
    """Why this planned invocation still cannot run Live Ops."""

    EXECUTION_DENIED = "EXECUTION_DENIED"
    PRODUCTION_AUTHORITY_DENIED = "PRODUCTION_AUTHORITY_DENIED"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    REQUEST_COVERAGE_NOT_ESTABLISHED = "REQUEST_COVERAGE_NOT_ESTABLISHED"
    TRUSTED_RUNTIME_CONTEXT_ABSENT = "TRUSTED_RUNTIME_CONTEXT_ABSENT"
    TRUSTED_RUNTIME_TABLES_ABSENT = "TRUSTED_RUNTIME_TABLES_ABSENT"
    SOURCE_OBSERVATION_CLOCK_ABSENT = "SOURCE_OBSERVATION_CLOCK_ABSENT"
    DEADLINE_CLOCK_ABSENT = "DEADLINE_CLOCK_ABSENT"
    MODEL_PROVIDER_ABSENT = "MODEL_PROVIDER_ABSENT"
    READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED = "READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED"
    AUTHORIZED_SELECTORS_ABSENT = "AUTHORIZED_SELECTORS_ABSENT"
    GEOSPATIAL_COVERAGE_NOT_ESTABLISHED = "GEOSPATIAL_COVERAGE_NOT_ESTABLISHED"
    TOOL_HANDLERS_NOT_AUTHORIZED = "TOOL_HANDLERS_NOT_AUTHORIZED"
    PROPOSED_TOOL_CALLS_NOT_CREATED = "PROPOSED_TOOL_CALLS_NOT_CREATED"
    TOOL_CALL_IDENTITY_NOT_ASSIGNED = "TOOL_CALL_IDENTITY_NOT_ASSIGNED"
    LIVE_OPS_CALL_NOT_BUILT = "LIVE_OPS_CALL_NOT_BUILT"
    UNTRUSTED_FILTER_NOT_AUTHORIZED = "UNTRUSTED_FILTER_NOT_AUTHORIZED"
    DICT_ENTRYPOINT_NOT_THE_NATIVE_REQUEST = "DICT_ENTRYPOINT_NOT_THE_NATIVE_REQUEST"
    PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN = "PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN"
    UNTRUSTED_REGION_HINT_NOT_SELECTOR = "UNTRUSTED_REGION_HINT_NOT_SELECTOR"
    UNTRUSTED_CALLSIGN_NOT_SELECTOR = "UNTRUSTED_CALLSIGN_NOT_SELECTOR"
    UNTRUSTED_TYPED_AIRCRAFT_ID_NOT_SELECTOR = "UNTRUSTED_TYPED_AIRCRAFT_ID_NOT_SELECTOR"
    UNTRUSTED_POPULATION_NOT_SELECTOR = "UNTRUSTED_POPULATION_NOT_SELECTOR"
    UNTRUSTED_RECOMMENDATION_ID_NOT_SELECTOR = "UNTRUSTED_RECOMMENDATION_ID_NOT_SELECTOR"
    UNTRUSTED_EXPECTED_AIRCRAFT_ID_NOT_SELECTOR = (
        "UNTRUSTED_EXPECTED_AIRCRAFT_ID_NOT_SELECTOR"
    )
    UNTRUSTED_EXPECTED_RECOMMENDATION_ID_NOT_SELECTOR = (
        "UNTRUSTED_EXPECTED_RECOMMENDATION_ID_NOT_SELECTOR"
    )


class MasterLiveOpsInterfaceConflict(str, Enum):
    """Real signatures that must not be papered over with a new adapter."""

    PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN = "PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN"
    DECODE_AND_RUN_ACCEPTS_MAPPING_NOT_NATIVE_REQUEST = (
        "DECODE_AND_RUN_ACCEPTS_MAPPING_NOT_NATIVE_REQUEST"
    )
    OPTIONAL_REGION_IS_NOT_AN_AUTHORIZED_SELECTOR = (
        "OPTIONAL_REGION_IS_NOT_AN_AUTHORIZED_SELECTOR"
    )
    CENSUS_REGION_RESOLUTION_IS_NOT_SIGMET_COVERAGE = (
        "CENSUS_REGION_RESOLUTION_IS_NOT_SIGMET_COVERAGE"
    )
    READ_ONLY_ADVISORY_IS_NOT_SOURCE_READ_AUTHORITY = (
        "READ_ONLY_ADVISORY_IS_NOT_SOURCE_READ_AUTHORITY"
    )
    APPLICATION_REQUEST_TIME_IS_NOT_SOURCE_OBSERVATION_CLOCK = (
        "APPLICATION_REQUEST_TIME_IS_NOT_SOURCE_OBSERVATION_CLOCK"
    )


_STANDING_BLOCKERS = (
    MasterLiveOpsExecutionBlocker.EXECUTION_DENIED,
    MasterLiveOpsExecutionBlocker.PRODUCTION_AUTHORITY_DENIED,
    MasterLiveOpsExecutionBlocker.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterLiveOpsExecutionBlocker.REQUEST_COVERAGE_NOT_ESTABLISHED,
    MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_TABLES_ABSENT,
    MasterLiveOpsExecutionBlocker.SOURCE_OBSERVATION_CLOCK_ABSENT,
    MasterLiveOpsExecutionBlocker.DEADLINE_CLOCK_ABSENT,
    MasterLiveOpsExecutionBlocker.MODEL_PROVIDER_ABSENT,
    MasterLiveOpsExecutionBlocker.READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED,
    MasterLiveOpsExecutionBlocker.AUTHORIZED_SELECTORS_ABSENT,
    MasterLiveOpsExecutionBlocker.GEOSPATIAL_COVERAGE_NOT_ESTABLISHED,
    MasterLiveOpsExecutionBlocker.TOOL_HANDLERS_NOT_AUTHORIZED,
    MasterLiveOpsExecutionBlocker.PROPOSED_TOOL_CALLS_NOT_CREATED,
    MasterLiveOpsExecutionBlocker.TOOL_CALL_IDENTITY_NOT_ASSIGNED,
    MasterLiveOpsExecutionBlocker.LIVE_OPS_CALL_NOT_BUILT,
    MasterLiveOpsExecutionBlocker.UNTRUSTED_FILTER_NOT_AUTHORIZED,
    MasterLiveOpsExecutionBlocker.DICT_ENTRYPOINT_NOT_THE_NATIVE_REQUEST,
    MasterLiveOpsExecutionBlocker.PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN,
)

_INTERFACE_CONFLICTS = tuple(MasterLiveOpsInterfaceConflict)


@dataclass(frozen=True)
class LiveOpsRealParameter:
    """One parameter taken from a real signature."""

    name: str
    kind: str
    role: LiveOpsParameterRole
    required: bool = True
    annotation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ContractValidationError("invalid_invocation_parameter")
        if self.kind not in _PARAMETER_KINDS:
            raise ContractValidationError("invalid_invocation_parameter")
        if not isinstance(self.role, LiveOpsParameterRole):
            raise ContractValidationError("invalid_invocation_parameter")
        if not isinstance(self.required, bool):
            raise ContractValidationError("invalid_invocation_parameter")
        if self.annotation is not None and not isinstance(self.annotation, str):
            raise ContractValidationError("invalid_invocation_parameter")


@dataclass(frozen=True)
class LiveOpsRealInvocation:
    """One real callable. ``authorized_to_invoke`` cannot be true."""

    module: str
    qualname: str
    inspect_via: str
    returns: str | None
    parameters: tuple[LiveOpsRealParameter, ...]
    disposition: LiveOpsConnectionDisposition
    invocation_blocker: MasterLiveOpsExecutionBlocker
    authority_mode: str | None = None
    authorized_to_invoke: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.module, str) or not self.module:
            raise ContractValidationError("invalid_invocation_interface")
        if not isinstance(self.qualname, str) or not self.qualname:
            raise ContractValidationError("invalid_invocation_interface")
        if self.inspect_via not in _INSPECT_VIA:
            raise ContractValidationError("invalid_invocation_interface")
        if self.returns is not None and not isinstance(self.returns, str):
            raise ContractValidationError("invalid_invocation_interface")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, LiveOpsRealParameter) for item in self.parameters
        ):
            raise ContractValidationError("invalid_invocation_interface")
        if not isinstance(self.disposition, LiveOpsConnectionDisposition):
            raise ContractValidationError("invalid_invocation_interface")
        if not isinstance(self.invocation_blocker, MasterLiveOpsExecutionBlocker):
            raise ContractValidationError("invalid_invocation_interface")
        if self.authority_mode not in {None, _READ_ONLY_ADVISORY}:
            raise ContractValidationError("source_read_not_authorized")
        if self.authorized_to_invoke is not False:
            raise ContractValidationError("source_read_not_authorized")


@dataclass(frozen=True)
class LiveOpsAdapterSelectorRule:
    """Adapter selector rule that the tool signature alone does not state."""

    tool_name: str
    accepted_parameters: tuple[str, ...]
    validation_code: str

    def __post_init__(self) -> None:
        if self.tool_name not in LIVE_OPS_READ_ONLY_TOOL_NAMES:
            raise ContractValidationError("invalid_selector_rule")
        if not isinstance(self.accepted_parameters, tuple) or not self.accepted_parameters:
            raise ContractValidationError("invalid_selector_rule")
        if any(not isinstance(item, str) or not item for item in self.accepted_parameters):
            raise ContractValidationError("invalid_selector_rule")
        if self.validation_code not in {"UNBOUNDED_HAZARD_SEARCH", "MISSING_SELECTOR"}:
            raise ContractValidationError("invalid_selector_rule")


@dataclass(frozen=True)
class MasterLiveOpsConstructibleParameter:
    """A real parameter the prepared public request can occupy."""

    module: str
    qualname: str
    parameter: str
    role: LiveOpsParameterRole

    def __post_init__(self) -> None:
        if self.role is not LiveOpsParameterRole.PUBLIC_REQUEST:
            raise ContractValidationError("public_request_not_constructible")
        if not self.module or not self.qualname or not self.parameter:
            raise ContractValidationError("public_request_not_constructible")


@dataclass(frozen=True)
class MasterLiveOpsUnboundParameter:
    """A real parameter this assessment does not bind.

    ``preserved_plan_value`` is set only for correlation-id parameters. It is
    the planned correlation id, not a selector and not ``now_epoch``.
    """

    module: str
    qualname: str
    parameter: str
    role: LiveOpsParameterRole
    blocker: MasterLiveOpsExecutionBlocker
    preserved_plan_value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, LiveOpsParameterRole):
            raise ContractValidationError("invalid_unbound_parameter")
        if not isinstance(self.blocker, MasterLiveOpsExecutionBlocker):
            raise ContractValidationError("invalid_unbound_parameter")
        if self.role is LiveOpsParameterRole.RECEIVER:
            raise ContractValidationError("invalid_unbound_parameter")
        if self.role is LiveOpsParameterRole.PUBLIC_REQUEST:
            raise ContractValidationError("invalid_unbound_parameter")
        if self.role is LiveOpsParameterRole.CORRELATION_ID:
            if not isinstance(self.preserved_plan_value, str) or not self.preserved_plan_value:
                raise ContractValidationError("correlation_mismatch")
            return
        if self.preserved_plan_value is not None:
            raise ContractValidationError("preserved_identity_matches_untrusted_hint")


def _param(
    name: str,
    kind: str,
    role: LiveOpsParameterRole,
    *,
    required: bool = True,
    annotation: str | None = None,
) -> LiveOpsRealParameter:
    return LiveOpsRealParameter(
        name=name,
        kind=kind,
        role=role,
        required=required,
        annotation=annotation,
    )


def _self() -> LiveOpsRealParameter:
    return _param("self", _PK, LiveOpsParameterRole.RECEIVER)


def _invocation(
    module: str,
    qualname: str,
    inspect_via: str,
    returns: str | None,
    parameters: tuple[LiveOpsRealParameter, ...],
    disposition: LiveOpsConnectionDisposition,
    invocation_blocker: MasterLiveOpsExecutionBlocker,
    authority_mode: str | None = None,
) -> LiveOpsRealInvocation:
    return LiveOpsRealInvocation(
        module=module,
        qualname=qualname,
        inspect_via=inspect_via,
        returns=returns,
        parameters=parameters,
        disposition=disposition,
        invocation_blocker=invocation_blocker,
        authority_mode=authority_mode,
    )


_MUST = LiveOpsConnectionDisposition.MUST_CONNECT
_NOT_MASTER = LiveOpsConnectionDisposition.NOT_THE_MASTER_CONNECTION
_NOT_COVERAGE = LiveOpsConnectionDisposition.NOT_COVERAGE_AUTHORITY
_BLOCKED = MasterLiveOpsExecutionBlocker
_ROLE = LiveOpsParameterRole

_HAZARD_FILTERS = (
    _param(
        "region",
        _KW,
        _ROLE.AUTHORIZED_SELECTOR,
        required=False,
        annotation="str | None",
    ),
    _param(
        "product_type",
        _KW,
        _ROLE.UNTRUSTED_FILTER,
        required=False,
        annotation="str | None",
    ),
    _param(
        "hazard_type",
        _KW,
        _ROLE.UNTRUSTED_FILTER,
        required=False,
        annotation="str | None",
    ),
    _param(
        "hazard_ids",
        _KW,
        _ROLE.AUTHORIZED_SELECTOR,
        required=False,
        annotation="list[str] | tuple[str, ...] | None",
    ),
)

_CALL = _param("call", _PK, _ROLE.LIVE_OPS_CALL, annotation="LiveOpsCall")

_SOURCE_TABLES = _param("tables", _PK, _ROLE.TRUSTED_TABLES)
_SOURCE_CLOCK = _param("now_epoch", _KW, _ROLE.SOURCE_OBSERVATION_CLOCK)
_SOURCE_FILTERS = (
    _param("product_type", _KW, _ROLE.UNTRUSTED_FILTER, required=False),
    _param("hazard_type", _KW, _ROLE.UNTRUSTED_FILTER, required=False),
    _param("hazard_ids", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
)


def _tool(
    name: str,
    parameters: tuple[LiveOpsRealParameter, ...],
) -> LiveOpsRealInvocation:
    return _invocation(
        "wilvor_ai.live_ops",
        name,
        "callable",
        "ToolResult",
        parameters,
        _MUST,
        _BLOCKED.READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED,
        _READ_ONLY_ADVISORY,
    )


def _source(
    module: str,
    qualname: str,
    returns: str | None,
    parameters: tuple[LiveOpsRealParameter, ...],
) -> LiveOpsRealInvocation:
    return _invocation(
        module,
        qualname,
        "callable",
        returns,
        parameters,
        _MUST,
        _BLOCKED.READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED,
    )


LIVE_OPS_REAL_INVOCATIONS: tuple[LiveOpsRealInvocation, ...] = (
    _invocation(
        "wilvor_ai.live_ops_specialist",
        "LiveOpsSpecialist.__init__",
        "callable",
        "None",
        (
            _self(),
            _param("provider", _KW, _ROLE.MODEL_PROVIDER, annotation="LiveOpsModelProvider"),
            _param(
                "tool_call_id_factory",
                _KW,
                _ROLE.TOOL_CALL_ID_FACTORY,
                required=False,
                annotation="Callable[[], str] | None",
            ),
            _param(
                "handlers",
                _KW,
                _ROLE.TOOL_HANDLERS,
                required=False,
                annotation="Mapping[str, Callable[..., ToolResult]] | None",
            ),
        ),
        _MUST,
        _BLOCKED.EXECUTION_DENIED,
    ),
    _invocation(
        "wilvor_ai.live_ops_specialist",
        "LiveOpsSpecialist.run",
        "callable",
        "LiveOpsRunResult",
        (
            _self(),
            _param("request", _PK, _ROLE.PUBLIC_REQUEST, annotation="LiveOpsRunRequest"),
            _param("context", _PK, _ROLE.TRUSTED_CONTEXT, annotation="LiveOpsTrustedContext"),
        ),
        _MUST,
        _BLOCKED.EXECUTION_DENIED,
    ),
    _invocation(
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsSpecialistPort.run",
        "callable",
        "LiveOpsRunResult",
        (
            _self(),
            _param("request", _PK, _ROLE.PUBLIC_REQUEST, annotation="LiveOpsRunRequest"),
            _param("context", _PK, _ROLE.TRUSTED_CONTEXT, annotation="LiveOpsTrustedContext"),
        ),
        _MUST,
        _BLOCKED.EXECUTION_DENIED,
    ),
    _invocation(
        "wilvor_ai.live_ops_model_contracts",
        "LiveOpsModelProvider.complete",
        "callable",
        "LiveOpsModelDecision",
        (
            _self(),
            _param(
                "request",
                _PK,
                _ROLE.MODEL_TURN_REQUEST,
                annotation="LiveOpsModelTurnRequest",
            ),
        ),
        _MUST,
        _BLOCKED.MODEL_PROVIDER_ABSENT,
    ),
    _invocation(
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsTrustedContext",
        "class",
        "None",
        (
            _param("tables", _PK, _ROLE.TRUSTED_TABLES, annotation="Any"),
            _param("now_epoch", _PK, _ROLE.SOURCE_OBSERVATION_CLOCK, annotation="int"),
            _param("correlation_id", _PK, _ROLE.CORRELATION_ID, annotation="str"),
            _param("max_model_turns", _PK, _ROLE.BUDGET, required=False, annotation="int"),
            _param("max_tool_calls", _PK, _ROLE.BUDGET, required=False, annotation="int"),
            _param(
                "clock",
                _PK,
                _ROLE.DEADLINE_CLOCK,
                required=False,
                annotation="Callable[[], int] | None",
            ),
            _param(
                "deadline_epoch",
                _PK,
                _ROLE.DEADLINE_EPOCH,
                required=False,
                annotation="int | None",
            ),
            _param(
                "cancelled",
                _PK,
                _ROLE.CANCELLATION,
                required=False,
                annotation="Callable[[], bool] | None",
            ),
        ),
        _MUST,
        _BLOCKED.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    ),
    _invocation(
        "wilvor_ai.live_ops_specialist_runtime_contracts",
        "LiveOpsSpecialistTrustedContext",
        "class",
        "None",
        (
            _param("tables", _PK, _ROLE.TRUSTED_TABLES, annotation="Any"),
            _param("now_epoch", _PK, _ROLE.SOURCE_OBSERVATION_CLOCK, annotation="int"),
            _param(
                "correlation_id",
                _PK,
                _ROLE.CORRELATION_ID,
                required=False,
                annotation="str | None",
            ),
        ),
        _MUST,
        _BLOCKED.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    ),
    _invocation(
        "wilvor_ai.live_ops_adapter",
        "LiveOpsAdapter.__init__",
        "callable",
        "None",
        (
            _self(),
            _param(
                "context",
                _PK,
                _ROLE.TRUSTED_CONTEXT,
                annotation="LiveOpsSpecialistTrustedContext",
            ),
            _param(
                "handlers",
                _KW,
                _ROLE.TOOL_HANDLERS,
                required=False,
                annotation="Mapping[str, ToolHandler] | None",
            ),
        ),
        _MUST,
        _BLOCKED.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    ),
    _invocation(
        "wilvor_ai.live_ops_adapter",
        "LiveOpsAdapter.dispatch",
        "callable",
        "LiveOpsDispatchResult",
        (
            _self(),
            _param(
                "proposed",
                _PK,
                _ROLE.PROPOSED_TOOL_CALLS,
                annotation="Sequence[ProposedToolCall]",
            ),
            _param(
                "tool_call_id_factory",
                _KW,
                _ROLE.TOOL_CALL_ID_FACTORY,
                annotation="Callable[[], str]",
            ),
        ),
        _MUST,
        _BLOCKED.EXECUTION_DENIED,
    ),
    _invocation(
        "wilvor_ai.live_ops",
        "LiveOpsCall",
        "class",
        "None",
        (
            _param("tables", _PK, _ROLE.TRUSTED_TABLES, annotation="Any"),
            _param("now_epoch", _PK, _ROLE.SOURCE_OBSERVATION_CLOCK, annotation="int"),
            _param("tool_call_id", _PK, _ROLE.TOOL_CALL_ID, annotation="str"),
            _param(
                "correlation_id",
                _PK,
                _ROLE.CORRELATION_ID,
                required=False,
                annotation="str | None",
            ),
        ),
        _MUST,
        _BLOCKED.LIVE_OPS_CALL_NOT_BUILT,
    ),
    _invocation(
        "wilvor_operational.readers",
        "OperationalTables",
        "class",
        None,
        tuple(
            _param(name, _KW, _ROLE.TABLE_HANDLE)
            for name in (
                "aircraft",
                "projections",
                "projection_points",
                "hazards",
                "hazard_coordinates",
                "encounters",
                "risks",
                "airports",
                "metar",
                "taf",
                "taf_periods",
                "airport_assessments",
                "recommendations",
                "alerts",
            )
        ),
        _MUST,
        _BLOCKED.TRUSTED_RUNTIME_TABLES_ABSENT,
    ),
    _tool("search_current_hazards", (_CALL, *_HAZARD_FILTERS)),
    _tool("search_current_impacts", (_CALL, *_HAZARD_FILTERS)),
    _tool(
        "search_current_encounters",
        (
            _CALL,
            _param(
                "aircraft_id",
                _KW,
                _ROLE.AUTHORIZED_SELECTOR,
                required=False,
                annotation="str | None",
            ),
            _param(
                "callsign",
                _KW,
                _ROLE.AUTHORIZED_SELECTOR,
                required=False,
                annotation="str | None",
            ),
            _param(
                "hazard_id",
                _KW,
                _ROLE.AUTHORIZED_SELECTOR,
                required=False,
                annotation="str | None",
            ),
            _param(
                "hazard_ids",
                _KW,
                _ROLE.AUTHORIZED_SELECTOR,
                required=False,
                annotation="list[str] | tuple[str, ...] | None",
            ),
        ),
    ),
    _tool("get_observed_network_state", (_CALL,)),
    _tool(
        "get_aircraft_operational_context",
        (
            _CALL,
            _param("aircraft_id", _PK, _ROLE.AUTHORIZED_SELECTOR, annotation="str"),
        ),
    ),
    _tool(
        "get_hazard_operational_context",
        (
            _CALL,
            _param("hazard_id", _PK, _ROLE.AUTHORIZED_SELECTOR, annotation="str"),
        ),
    ),
    _tool(
        "get_airport_operational_context",
        (
            _CALL,
            _param("airport_id", _PK, _ROLE.AUTHORIZED_SELECTOR, annotation="str"),
        ),
    ),
    _tool(
        "find_aircraft_by_callsign",
        (
            _CALL,
            _param("callsign", _PK, _ROLE.AUTHORIZED_SELECTOR, annotation="str"),
        ),
    ),
    _source(
        "wilvor_operational.geospatial",
        "discover_current_hazards_in_region",
        None,
        (
            _SOURCE_TABLES,
            _param("region_query", _PK, _ROLE.AUTHORIZED_SELECTOR),
            _SOURCE_CLOCK,
            *_SOURCE_FILTERS,
        ),
    ),
    _source(
        "wilvor_operational.discovery",
        "discover_current_hazards",
        None,
        (_SOURCE_TABLES, _SOURCE_CLOCK, *_SOURCE_FILTERS),
    ),
    _source(
        "wilvor_operational.discovery",
        "discover_aircraft_by_callsign",
        None,
        (
            _SOURCE_TABLES,
            _param("callsign", _PK, _ROLE.AUTHORIZED_SELECTOR),
            _SOURCE_CLOCK,
        ),
    ),
    _source(
        "wilvor_operational.query",
        "search_current_impacts",
        "CurrentImpactQueryResult",
        (
            _SOURCE_TABLES,
            _SOURCE_CLOCK,
            _param("region", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
            *_SOURCE_FILTERS,
        ),
    ),
    _source(
        "wilvor_operational.query",
        "search_current_encounters",
        "CurrentEncounterQueryResult",
        (
            _SOURCE_TABLES,
            _SOURCE_CLOCK,
            _param("aircraft_id", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
            _param("callsign", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
            _param("hazard_id", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
            _param("hazard_ids", _KW, _ROLE.AUTHORIZED_SELECTOR, required=False),
        ),
    ),
    _source(
        "wilvor_operational.query",
        "get_observed_network_state",
        "ObservedNetworkState",
        (_SOURCE_TABLES, _SOURCE_CLOCK),
    ),
    _source(
        "wilvor_operational.context",
        "build_aircraft_operational_context",
        None,
        (
            _SOURCE_TABLES,
            _param("aircraft_id", _PK, _ROLE.AUTHORIZED_SELECTOR),
            _SOURCE_CLOCK,
        ),
    ),
    _source(
        "wilvor_operational.context",
        "build_hazard_operational_context",
        None,
        (
            _SOURCE_TABLES,
            _param("hazard_id", _PK, _ROLE.AUTHORIZED_SELECTOR),
            _SOURCE_CLOCK,
        ),
    ),
    _source(
        "wilvor_operational.context",
        "build_airport_operational_context",
        None,
        (
            _SOURCE_TABLES,
            _param("airport_id", _PK, _ROLE.AUTHORIZED_SELECTOR),
            _SOURCE_CLOCK,
        ),
    ),
    _invocation(
        "wilvor_operational.regions",
        "resolve_region",
        "callable",
        "RegionResolution",
        (_param("text", _PK, _ROLE.CLAIMED_REGION_TEXT),),
        _NOT_COVERAGE,
        _BLOCKED.GEOSPATIAL_COVERAGE_NOT_ESTABLISHED,
    ),
    _invocation(
        "wilvor_ai.live_ops_specialist",
        "decode_and_run_live_ops",
        "callable",
        "LiveOpsRunResult",
        (
            _param("specialist", _PK, _ROLE.SPECIALIST_INSTANCE, annotation="LiveOpsSpecialist"),
            _param("data", _PK, _ROLE.UNTRUSTED_MAPPING, annotation="Mapping[str, Any]"),
            _param("context", _PK, _ROLE.TRUSTED_CONTEXT, annotation="LiveOpsTrustedContext"),
        ),
        _NOT_MASTER,
        _BLOCKED.DICT_ENTRYPOINT_NOT_THE_NATIVE_REQUEST,
    ),
    _invocation(
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsSpecialistPort",
        "class",
        None,
        (
            _param("args", _VAR_POSITIONAL, _ROLE.VARARGS),
            _param("kwargs", _VAR_KEYWORD, _ROLE.VARARGS),
        ),
        _NOT_MASTER,
        _BLOCKED.PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN,
    ),
)


LIVE_OPS_ADAPTER_SELECTOR_RULES: tuple[LiveOpsAdapterSelectorRule, ...] = (
    LiveOpsAdapterSelectorRule(
        "search_current_hazards",
        ("region", "hazard_ids"),
        "UNBOUNDED_HAZARD_SEARCH",
    ),
    LiveOpsAdapterSelectorRule(
        "search_current_impacts",
        ("region", "hazard_ids"),
        "MISSING_SELECTOR",
    ),
    LiveOpsAdapterSelectorRule(
        "search_current_encounters",
        ("aircraft_id", "callsign", "hazard_id", "hazard_ids"),
        "MISSING_SELECTOR",
    ),
)


def _validate_catalog() -> None:
    seen: set[tuple[str, str]] = set()
    tool_names: list[str] = []
    by_tool: dict[str, LiveOpsRealInvocation] = {}
    for item in LIVE_OPS_REAL_INVOCATIONS:
        key = (item.module, item.qualname)
        if key in seen:
            raise ContractValidationError("duplicate_invocation_interface")
        seen.add(key)
        if item.module == "wilvor_ai.live_ops" and item.disposition is _MUST:
            if item.qualname == "LiveOpsCall":
                continue
            tool_names.append(item.qualname)
            by_tool[item.qualname] = item
            if item.authority_mode != _READ_ONLY_ADVISORY:
                raise ContractValidationError("source_read_not_authorized")
        for parameter in item.parameters:
            if parameter.role is _ROLE.AUTHORIZED_SELECTOR and parameter.name not in (
                _REGION_PARAMETERS
                | _CALLSIGN_PARAMETERS
                | _AIRCRAFT_PARAMETERS
                | _OTHER_SELECTOR_PARAMETERS
            ):
                raise ContractValidationError("invalid_selector_parameter")
            if parameter.role is _ROLE.UNTRUSTED_FILTER and parameter.name not in _FILTER_PARAMETERS:
                raise ContractValidationError("invalid_selector_parameter")
    if tuple(tool_names) != LIVE_OPS_READ_ONLY_TOOL_NAMES:
        raise ContractValidationError("invocation_interface_mismatch")
    for rule in LIVE_OPS_ADAPTER_SELECTOR_RULES:
        names = {parameter.name for parameter in by_tool[rule.tool_name].parameters}
        if not set(rule.accepted_parameters) <= names:
            raise ContractValidationError("invalid_selector_rule")
    interfaces = {(item.module, item.qualname): item for item in LIVE_OPS_REAL_INVOCATIONS}
    specialist_run = interfaces[("wilvor_ai.live_ops_specialist", "LiveOpsSpecialist.run")]
    port_run = interfaces[("wilvor_ai.live_ops_public_contracts", "LiveOpsSpecialistPort.run")]
    if tuple((item.name, item.role) for item in specialist_run.parameters) != tuple(
        (item.name, item.role) for item in port_run.parameters
    ):
        raise ContractValidationError("invocation_interface_mismatch")
    decode = interfaces[("wilvor_ai.live_ops_specialist", "decode_and_run_live_ops")]
    if decode.disposition is not _NOT_MASTER:
        raise ContractValidationError("dict_entrypoint_not_native_request")
    port_class = interfaces[("wilvor_ai.live_ops_public_contracts", "LiveOpsSpecialistPort")]
    if port_class.disposition is not _NOT_MASTER:
        raise ContractValidationError("protocol_class_signature_is_not_run")
    region_resolution = interfaces[("wilvor_operational.regions", "resolve_region")]
    if region_resolution.disposition is not _NOT_COVERAGE:
        raise ContractValidationError("geospatial_coverage_not_established")


def must_connect_names() -> tuple[tuple[str, str], ...]:
    """Qualified names a later implementation must connect. None are invoked here."""

    return tuple(
        (item.module, item.qualname)
        for item in LIVE_OPS_REAL_INVOCATIONS
        if item.disposition is LiveOpsConnectionDisposition.MUST_CONNECT
    )


def not_master_connection_names() -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.module, item.qualname)
        for item in LIVE_OPS_REAL_INVOCATIONS
        if item.disposition is LiveOpsConnectionDisposition.NOT_THE_MASTER_CONNECTION
    )


def not_coverage_authority_names() -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.module, item.qualname)
        for item in LIVE_OPS_REAL_INVOCATIONS
        if item.disposition is LiveOpsConnectionDisposition.NOT_COVERAGE_AUTHORITY
    )


def _region_untrusted(binding: MasterLiveOpsBinding) -> bool:
    hints = binding.withheld_untrusted_hints
    return (
        hints.region_text is not None
        or MasterRouteGate.REGION_VALIDATION_PENDING in binding.gates
    )


def _selector_blocker(
    binding: MasterLiveOpsBinding,
    parameter_name: str,
) -> MasterLiveOpsExecutionBlocker:
    hints = binding.withheld_untrusted_hints
    if parameter_name in _REGION_PARAMETERS:
        if _region_untrusted(binding):
            return _BLOCKED.UNTRUSTED_REGION_HINT_NOT_SELECTOR
        return _BLOCKED.AUTHORIZED_SELECTORS_ABSENT
    if parameter_name in _CALLSIGN_PARAMETERS:
        if hints.callsign is not None:
            return _BLOCKED.UNTRUSTED_CALLSIGN_NOT_SELECTOR
        return _BLOCKED.AUTHORIZED_SELECTORS_ABSENT
    if parameter_name in _AIRCRAFT_PARAMETERS:
        if binding.withheld_expected_aircraft_id is not None:
            return _BLOCKED.UNTRUSTED_EXPECTED_AIRCRAFT_ID_NOT_SELECTOR
        if hints.typed_aircraft_id is not None:
            return _BLOCKED.UNTRUSTED_TYPED_AIRCRAFT_ID_NOT_SELECTOR
        if hints.population_aircraft_ids:
            return _BLOCKED.UNTRUSTED_POPULATION_NOT_SELECTOR
        return _BLOCKED.AUTHORIZED_SELECTORS_ABSENT
    if parameter_name in _OTHER_SELECTOR_PARAMETERS:
        return _BLOCKED.AUTHORIZED_SELECTORS_ABSENT
    raise ContractValidationError("invalid_selector_parameter")


def _parameter_blocker(
    binding: MasterLiveOpsBinding,
    invocation: LiveOpsRealInvocation,
    parameter: LiveOpsRealParameter,
) -> MasterLiveOpsExecutionBlocker | None:
    role = parameter.role
    if role is _ROLE.RECEIVER or role is _ROLE.PUBLIC_REQUEST:
        return None
    if role is _ROLE.CORRELATION_ID or role is _ROLE.TRUSTED_CONTEXT:
        return _BLOCKED.TRUSTED_RUNTIME_CONTEXT_ABSENT
    if role is _ROLE.BUDGET or role is _ROLE.CANCELLATION or role is _ROLE.DEADLINE_EPOCH:
        return _BLOCKED.TRUSTED_RUNTIME_CONTEXT_ABSENT
    if role is _ROLE.TRUSTED_TABLES or role is _ROLE.TABLE_HANDLE:
        return _BLOCKED.TRUSTED_RUNTIME_TABLES_ABSENT
    if role is _ROLE.SOURCE_OBSERVATION_CLOCK:
        return _BLOCKED.SOURCE_OBSERVATION_CLOCK_ABSENT
    if role is _ROLE.DEADLINE_CLOCK:
        return _BLOCKED.DEADLINE_CLOCK_ABSENT
    if role is _ROLE.MODEL_PROVIDER or role is _ROLE.MODEL_TURN_REQUEST:
        return _BLOCKED.MODEL_PROVIDER_ABSENT
    if role is _ROLE.TOOL_HANDLERS:
        return _BLOCKED.TOOL_HANDLERS_NOT_AUTHORIZED
    if role is _ROLE.TOOL_CALL_ID_FACTORY or role is _ROLE.TOOL_CALL_ID:
        return _BLOCKED.TOOL_CALL_IDENTITY_NOT_ASSIGNED
    if role is _ROLE.PROPOSED_TOOL_CALLS:
        return _BLOCKED.PROPOSED_TOOL_CALLS_NOT_CREATED
    if role is _ROLE.LIVE_OPS_CALL:
        return _BLOCKED.LIVE_OPS_CALL_NOT_BUILT
    if role is _ROLE.UNTRUSTED_FILTER:
        return _BLOCKED.UNTRUSTED_FILTER_NOT_AUTHORIZED
    if role is _ROLE.AUTHORIZED_SELECTOR:
        return _selector_blocker(binding, parameter.name)
    if role is _ROLE.CLAIMED_REGION_TEXT:
        if _region_untrusted(binding):
            return _BLOCKED.UNTRUSTED_REGION_HINT_NOT_SELECTOR
        return _BLOCKED.GEOSPATIAL_COVERAGE_NOT_ESTABLISHED
    if role is _ROLE.UNTRUSTED_MAPPING or role is _ROLE.SPECIALIST_INSTANCE:
        return _BLOCKED.DICT_ENTRYPOINT_NOT_THE_NATIVE_REQUEST
    if role is _ROLE.VARARGS:
        return _BLOCKED.PROTOCOL_CLASS_SIGNATURE_IS_NOT_RUN
    del invocation
    raise ContractValidationError("invalid_unbound_parameter")


def execution_blockers(
    binding: MasterLiveOpsBinding,
) -> tuple[MasterLiveOpsExecutionBlocker, ...]:
    """Closed blockers for one prepared Live Ops binding. Order follows the enum."""

    if not isinstance(binding, MasterLiveOpsBinding):
        raise ContractValidationError("invalid_live_ops_binding")
    if not isinstance(binding.withheld_untrusted_hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")
    selected = set(_STANDING_BLOCKERS)
    hints = binding.withheld_untrusted_hints
    if _region_untrusted(binding):
        selected.add(_BLOCKED.UNTRUSTED_REGION_HINT_NOT_SELECTOR)
    if hints.callsign is not None:
        selected.add(_BLOCKED.UNTRUSTED_CALLSIGN_NOT_SELECTOR)
    if hints.typed_aircraft_id is not None:
        selected.add(_BLOCKED.UNTRUSTED_TYPED_AIRCRAFT_ID_NOT_SELECTOR)
    if hints.population_aircraft_ids:
        selected.add(_BLOCKED.UNTRUSTED_POPULATION_NOT_SELECTOR)
    if hints.recommendation_id is not None:
        selected.add(_BLOCKED.UNTRUSTED_RECOMMENDATION_ID_NOT_SELECTOR)
    if binding.withheld_expected_aircraft_id is not None:
        selected.add(_BLOCKED.UNTRUSTED_EXPECTED_AIRCRAFT_ID_NOT_SELECTOR)
    if binding.withheld_expected_recommendation_id is not None:
        selected.add(_BLOCKED.UNTRUSTED_EXPECTED_RECOMMENDATION_ID_NOT_SELECTOR)
    return tuple(item for item in MasterLiveOpsExecutionBlocker if item in selected)


def constructible_parameters() -> tuple[MasterLiveOpsConstructibleParameter, ...]:
    found: list[MasterLiveOpsConstructibleParameter] = []
    for invocation in LIVE_OPS_REAL_INVOCATIONS:
        for parameter in invocation.parameters:
            if parameter.role is not _ROLE.PUBLIC_REQUEST:
                continue
            found.append(
                MasterLiveOpsConstructibleParameter(
                    module=invocation.module,
                    qualname=invocation.qualname,
                    parameter=parameter.name,
                    role=parameter.role,
                )
            )
    return tuple(found)


def unbound_parameters(
    binding: MasterLiveOpsBinding,
) -> tuple[MasterLiveOpsUnboundParameter, ...]:
    """Real parameters that remain unbound. Selector hints are not copied in."""

    if not isinstance(binding, MasterLiveOpsBinding):
        raise ContractValidationError("invalid_live_ops_binding")
    found: list[MasterLiveOpsUnboundParameter] = []
    for invocation in LIVE_OPS_REAL_INVOCATIONS:
        for parameter in invocation.parameters:
            blocker = _parameter_blocker(binding, invocation, parameter)
            if blocker is None:
                continue
            preserved = None
            if parameter.role is _ROLE.CORRELATION_ID:
                preserved = binding.correlation_id
            found.append(
                MasterLiveOpsUnboundParameter(
                    module=invocation.module,
                    qualname=invocation.qualname,
                    parameter=parameter.name,
                    role=parameter.role,
                    blocker=blocker,
                    preserved_plan_value=preserved,
                )
            )
    return tuple(found)


def _hint_values(hints: MasterRouteHints) -> frozenset[str]:
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


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


@dataclass(frozen=True)
class MasterLiveOpsExecutionReadiness:
    """Readiness for one planned Live Ops invocation. Not permission to run it.

    ``request`` is the binder's native ``LiveOpsRunRequest``. ``execution_blocked``
    stays true until trusted region, runtime, source-read authority, authorized
    selectors, and a source observation clock exist outside this module.
    ``structural_ready_to_execute`` copies the plan flag and is not dispatch
    permission.
    """

    binding: MasterLiveOpsBinding
    request: LiveOpsRunRequest
    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    invocation_id: str
    step_id: str
    specialist_request_id: str
    structural_ready_to_execute: bool
    blockers: tuple[MasterLiveOpsExecutionBlocker, ...]
    unbound_parameters: tuple[MasterLiveOpsUnboundParameter, ...]
    constructible_parameters: tuple[MasterLiveOpsConstructibleParameter, ...]
    must_connect_interfaces: tuple[tuple[str, str], ...]
    not_master_connection_interfaces: tuple[tuple[str, str], ...]
    not_coverage_authority_interfaces: tuple[tuple[str, str], ...]
    invocation_interfaces: tuple[LiveOpsRealInvocation, ...] = LIVE_OPS_REAL_INVOCATIONS
    adapter_selector_rules: tuple[LiveOpsAdapterSelectorRule, ...] = (
        LIVE_OPS_ADAPTER_SELECTOR_RULES
    )
    interface_conflicts: tuple[MasterLiveOpsInterfaceConflict, ...] = _INTERFACE_CONFLICTS
    public_request_constructible: bool = True
    execution_blocked: bool = True
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    executable: bool = False
    source_read_authorized: bool = False
    trusted_region_authorized: bool = False
    trusted_runtime_present: bool = False
    source_observation_clock_present: bool = False
    geospatial_coverage_established: bool = False
    claimed_scope_establishes_geospatial_coverage: bool = False
    census_region_resolution_is_sigmet_coverage: bool = False
    application_request_time_is_source_clock: bool = False
    adapter_selector_rules_satisfied: bool = False
    selectors_authorized: bool = False
    specialist_invoked: bool = False
    tool_result_created: bool = False
    aircraft_impact_count: int | None = None
    verified_zero: bool = False
    schema_version: str = MASTER_LIVE_OPS_EXECUTION_READINESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_LIVE_OPS_EXECUTION_READINESS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.binding, MasterLiveOpsBinding):
            raise ContractValidationError("invalid_live_ops_binding")
        if self.request is not self.binding.request:
            raise ContractValidationError("invocation_binding_mismatch")
        if not isinstance(self.request, LiveOpsRunRequest):
            raise ContractValidationError("invalid_live_ops_request")
        if self.request.schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.public_request_constructible is not True:
            raise ContractValidationError("public_request_not_constructible")
        if self.execution_blocked is not True:
            raise ContractValidationError("execution_not_granted")
        if not isinstance(self.structural_ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        if self.structural_ready_to_execute is not self.binding.structural_ready_to_execute:
            raise ContractValidationError("invocation_binding_mismatch")
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
        if self.source_authenticity is not self.binding.source_authenticity:
            raise ContractValidationError("source_authenticity_not_established")
        if self.production_authority is not self.binding.production_authority:
            raise ContractValidationError("production_authority_not_issued")
        if self.execution_authority is not self.binding.execution_authority:
            raise ContractValidationError("execution_authority_not_granted")
        if self.request_coverage is not self.binding.request_coverage:
            raise ContractValidationError("request_coverage_not_established")
        for name, code in (
            ("dispatch_permitted", "execution_not_granted"),
            ("executable", "execution_not_granted"),
            ("source_read_authorized", "source_read_not_authorized"),
            ("trusted_region_authorized", "trusted_region_not_authorized"),
            ("trusted_runtime_present", "trusted_runtime_absent"),
            ("source_observation_clock_present", "source_observation_clock_absent"),
            ("geospatial_coverage_established", "geospatial_coverage_not_established"),
            (
                "claimed_scope_establishes_geospatial_coverage",
                "geospatial_coverage_not_established",
            ),
            (
                "census_region_resolution_is_sigmet_coverage",
                "geospatial_coverage_not_established",
            ),
            ("application_request_time_is_source_clock", "application_time_is_not_source_clock"),
            ("adapter_selector_rules_satisfied", "authorized_selectors_absent"),
            ("selectors_authorized", "selection_hints_not_trusted"),
            ("specialist_invoked", "specialist_not_invoked"),
            ("tool_result_created", "tool_result_not_created"),
            ("verified_zero", "verified_zero_not_established"),
        ):
            if getattr(self, name) is not False:
                raise ContractValidationError(code)
        if self.aircraft_impact_count is not None:
            raise ContractValidationError("aircraft_impact_count_not_authoritative")
        if self.master_request_id != self.binding.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.correlation_id != self.binding.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.application_request_time_utc != self.binding.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if self.invocation_id != self.binding.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.step_id != self.binding.step_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != self.binding.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.request.request_id != self.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.request.user_text != self.binding.canonical_user_text:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.request.selector_hints is not None or self.request.entity_hints != ():
            raise ContractValidationError("selection_hints_not_trusted")
        if self.request.conversation_id is not None or self.request.prior_turns != ():
            raise ContractValidationError("invocation_binding_mismatch")
        withheld = _hint_values(self.binding.withheld_untrusted_hints)
        if (
            self.binding.withheld_expected_aircraft_id is not None
        ):
            withheld = withheld | {self.binding.withheld_expected_aircraft_id}
        if self.binding.withheld_expected_recommendation_id is not None:
            withheld = withheld | {self.binding.withheld_expected_recommendation_id}
        if self.specialist_request_id in withheld or self.correlation_id in withheld:
            raise ContractValidationError("preserved_identity_matches_untrusted_hint")
        if self.blockers != execution_blockers(self.binding):
            raise ContractValidationError("missing_execution_blocker")
        if self.unbound_parameters != unbound_parameters(self.binding):
            raise ContractValidationError("unbound_parameter_mismatch")
        if self.constructible_parameters != constructible_parameters():
            raise ContractValidationError("public_request_not_constructible")
        if self.must_connect_interfaces != must_connect_names():
            raise ContractValidationError("invocation_interface_mismatch")
        if self.not_master_connection_interfaces != not_master_connection_names():
            raise ContractValidationError("invocation_interface_mismatch")
        if self.not_coverage_authority_interfaces != not_coverage_authority_names():
            raise ContractValidationError("geospatial_coverage_not_established")
        if self.invocation_interfaces != LIVE_OPS_REAL_INVOCATIONS:
            raise ContractValidationError("invocation_interface_mismatch")
        if self.adapter_selector_rules != LIVE_OPS_ADAPTER_SELECTOR_RULES:
            raise ContractValidationError("invalid_selector_rule")
        if self.interface_conflicts != _INTERFACE_CONFLICTS:
            raise ContractValidationError("interface_conflict_mismatch")
        for item in self.unbound_parameters:
            if item.blocker not in self.blockers:
                raise ContractValidationError("missing_execution_blocker")
            if item.role is _ROLE.CORRELATION_ID and item.preserved_plan_value != self.correlation_id:
                raise ContractValidationError("correlation_mismatch")
            if (
                item.preserved_plan_value is not None
                and item.preserved_plan_value in withheld
            ):
                raise ContractValidationError("preserved_identity_matches_untrusted_hint")
            if item.role is _ROLE.SOURCE_OBSERVATION_CLOCK and item.preserved_plan_value is not None:
                raise ContractValidationError("application_time_is_not_source_clock")
            if item.role is _ROLE.AUTHORIZED_SELECTOR and item.preserved_plan_value is not None:
                raise ContractValidationError("selection_hints_not_trusted")
        for invocation in self.invocation_interfaces:
            if invocation.authorized_to_invoke is not False:
                raise ContractValidationError("source_read_not_authorized")
        if not self.constructible_parameters:
            raise ContractValidationError("public_request_not_constructible")
        if ("wilvor_ai.live_ops_specialist", "decode_and_run_live_ops") in self.must_connect_interfaces:
            raise ContractValidationError("dict_entrypoint_not_native_request")
        if ("wilvor_ai.live_ops_public_contracts", "LiveOpsSpecialistPort") in self.must_connect_interfaces:
            raise ContractValidationError("protocol_class_signature_is_not_run")
        if ("wilvor_operational.regions", "resolve_region") in self.must_connect_interfaces:
            raise ContractValidationError("geospatial_coverage_not_established")


def assess_master_live_ops_execution_readiness(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
    *,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    executable: object = False,
    dispatch_permitted: object = False,
) -> MasterLiveOpsExecutionReadiness:
    """Assess one planned Live Ops step without running the specialist.

    The returned public request is the binder's ``LiveOpsRunRequest``.
    Execution stays blocked. Caller-supplied tables, clocks, regions,
    callsigns, handlers, and evidence are not parameters of this function.
    """

    binding = prepare_master_live_ops_binding(
        bundle,
        descriptor,
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
        dispatch_permitted=dispatch_permitted,
    )
    return MasterLiveOpsExecutionReadiness(
        binding=binding,
        request=binding.request,
        master_request_id=binding.master_request_id,
        correlation_id=binding.correlation_id,
        application_request_time_utc=binding.application_request_time_utc,
        invocation_id=binding.invocation_id,
        step_id=binding.step_id,
        specialist_request_id=binding.specialist_request_id,
        structural_ready_to_execute=binding.structural_ready_to_execute,
        public_request_constructible=True,
        execution_blocked=True,
        blockers=execution_blockers(binding),
        unbound_parameters=unbound_parameters(binding),
        constructible_parameters=constructible_parameters(),
        must_connect_interfaces=must_connect_names(),
        not_master_connection_interfaces=not_master_connection_names(),
        not_coverage_authority_interfaces=not_coverage_authority_names(),
    )


_validate_catalog()


__all__ = [
    "LIVE_OPS_ADAPTER_SELECTOR_RULES",
    "LIVE_OPS_READ_ONLY_TOOL_NAMES",
    "LIVE_OPS_REAL_INVOCATIONS",
    "MASTER_LIVE_OPS_EXECUTION_READINESS_SCHEMA_VERSION",
    "LiveOpsAdapterSelectorRule",
    "LiveOpsConnectionDisposition",
    "LiveOpsParameterRole",
    "LiveOpsRealInvocation",
    "LiveOpsRealParameter",
    "MasterLiveOpsConstructibleParameter",
    "MasterLiveOpsExecutionBlocker",
    "MasterLiveOpsExecutionReadiness",
    "MasterLiveOpsInterfaceConflict",
    "MasterLiveOpsUnboundParameter",
    "assess_master_live_ops_execution_readiness",
    "constructible_parameters",
    "execution_blockers",
    "must_connect_names",
    "not_coverage_authority_names",
    "not_master_connection_names",
    "unbound_parameters",
]

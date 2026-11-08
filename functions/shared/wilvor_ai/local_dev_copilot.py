"""Local developer preview for the existing Copilot, Master Agent, and Live Ops tools.

This is not ``POST /ai/query``. It does not admit claims, establish source
authenticity, or treat operator SSO reads as Lambda-role provenance.
Production route flags stay closed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from secrets import token_hex
from typing import Any

from wilvor_ai.contracts import AgentCapability, ContractValidationError, ToolResult
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_public_contracts import LiveOpsRunRequest, LiveOpsTrustedContext
from wilvor_ai.live_ops_specialist import LiveOpsSpecialist
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_routing import route_kind_for
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint4_copilot_contract_map import map_copilot_session_query
from wilvor_ai.sprint4_live_ops_tools import run_authorized_live_ops_tool
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus, plan_master_request
from wilvor_ai.sprint5_copilot_host import SPRINT5_AI_QUERY_ROUTE_ENABLED
from wilvor_operational.observed import epoch_to_utc_z


LOCAL_DEV_PATH = "/local-dev/copilot/query"
PRODUCTION_QUERY_PATH = "/ai/query"
LOCAL_DEV_PREVIEW = "LOCAL_DEV_PREVIEW"
NOT_PRODUCTION_NOTICE = (
    "NOT PRODUCTION. This is a loopback developer preview. "
    "POST /ai/query stays disabled. Claims are not admitted and source "
    "authenticity is not established."
)
_MAX_BODY_BYTES = 65_536
_AIRCRAFT_MENTION = re.compile(r"\baircraft\s+([A-Za-z0-9]{4,32})\b", re.IGNORECASE)
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_REQUIRED_LIMITATIONS = (
    "SOURCE_AUTHENTICITY_NOT_ESTABLISHED",
    "PRODUCTION_AUTHORITY_DENIED",
    "NO_OPERATIONAL_CLAIM",
    "EXECUTION_DENIED",
)
_STATUS_TEXT = {
    "NOT_EVALUATED": "Response policy has not been evaluated.",
    "UNSUPPORTED": "The request is unsupported. No operational conclusion is available.",
    "UNAVAILABLE": "Required evidence is unavailable. No count or zero is inferred.",
    "INSUFFICIENT_EVIDENCE": "Supplied evidence is not sufficient for an operational claim.",
}
_COMPLETENESS = (
    "ALL_VERIFIED means every submitted claim verified. It does not mean every "
    "operational fact was investigated or that the source data is fresh, complete, or atomic."
)
_ROUTE_NOTICE = (
    "POST /ai/query remains disabled. This console does not call that route, "
    "and production AI is not active."
)
_TOOL_BINDING = {
    "search_current_hazards": ("hazards", ReadOperation.QUERY),
    "search_current_impacts": ("hazards", ReadOperation.QUERY),
    "search_current_encounters": ("encounters", ReadOperation.QUERY),
    "get_observed_network_state": ("network", ReadOperation.GET_ITEM),
    "get_aircraft_operational_context": ("aircraft", ReadOperation.GET_ITEM),
    "get_hazard_operational_context": ("hazards", ReadOperation.GET_ITEM),
    "get_airport_operational_context": ("airports", ReadOperation.GET_ITEM),
    "find_aircraft_by_callsign": ("aircraft", ReadOperation.QUERY),
}
_TOOL_TABLE = {
    "get_aircraft_operational_context": "wilvor-dev-aircraft-current-state",
    "find_aircraft_by_callsign": "wilvor-dev-aircraft-current-state",
    "search_current_encounters": "wilvor-dev-aircraft-hazard-encounter",
    "get_hazard_operational_context": "wilvor-dev-active-hazards",
    "search_current_hazards": "wilvor-dev-active-hazards",
    "get_airport_operational_context": "wilvor-dev-airport-status",
}
_OBSERVED_AIRCRAFT_FIELDS = (
    ("latitude", "Latitude"),
    ("longitude", "Longitude"),
    ("baro_altitude_ft", "Baro altitude ft"),
    ("geo_altitude_ft", "Geo altitude ft"),
    ("ground_speed_kt", "Ground speed kt"),
    ("track_deg", "Heading deg"),
    ("on_ground", "On ground"),
    ("freshness_status", "Stored freshness status"),
)
_FRESHNESS_BANDS = frozenset({"FRESH", "ACCEPTABLE", "STALE", "UNAVAILABLE", "UNKNOWN"})
_BLOCKED = {
    MasterSpecialist.DECISION: (
        "Decision specialist was not executed. Host proofs required for "
        "production MA4 admission are missing, so this preview does not admit Decision evidence."
    ),
    MasterSpecialist.HISTORICAL_ANALYTICS: (
        "Historical specialist was not executed. No Athena query was issued."
    ),
}


class LocalDevClosedError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def admit_local_request(*, peer_host: str, host_header: str, path: str, method: str) -> None:
    """Accept one loopback preview request. Production ``/ai/query`` is refused."""

    if SPRINT5_AI_QUERY_ROUTE_ENABLED is not False:
        raise LocalDevClosedError("ai_query_route_must_remain_disabled")
    if method != "POST":
        raise LocalDevClosedError("method_not_allowed")
    if peer_host not in {"127.0.0.1", "::1"}:
        raise LocalDevClosedError("nonlocal_peer_rejected")
    hostname = host_header.split(",")[0].strip().lower().split(":", 1)[0]
    if hostname not in {"127.0.0.1", "localhost"}:
        raise LocalDevClosedError("nonlocal_host_rejected")
    normalized = path.split("?", 1)[0].rstrip("/") or "/"
    if normalized == PRODUCTION_QUERY_PATH or normalized.endswith("/ai/query"):
        raise LocalDevClosedError("production_ai_query_not_served")
    if normalized != LOCAL_DEV_PATH:
        raise LocalDevClosedError("unknown_path")


def named_aircraft_id(user_text: str) -> str | None:
    match = _AIRCRAFT_MENTION.search(user_text)
    if match is None:
        return None
    return match.group(1).lower()


def run_local_dev_copilot(
    payload: Mapping[str, Any],
    *,
    planner: object,
    specialist_provider: object,
    open_tables: Callable[[], tuple[object, list[dict[str, object]], Mapping[str, str]]],
    now_epoch: int,
    model_calls: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    """Plan, run Live Ops tools when authorized, and return a closed preview."""

    if SPRINT5_AI_QUERY_ROUTE_ENABLED is not False:
        raise LocalDevClosedError("ai_query_route_must_remain_disabled")
    if type(now_epoch) is not int:
        raise LocalDevClosedError("invalid_now_epoch")
    calls = model_calls if model_calls is not None else []
    try:
        mapped = map_copilot_session_query(payload)
    except ContractValidationError as exc:
        return _closed_result(
            payload,
            state="UNAVAILABLE",
            answer="The question was not accepted. No model or AWS call was made.",
            model_errors=[exc.errors[0] if exc.errors else "invalid_copilot_request"],
            model_calls=calls,
        )
    try:
        planning = plan_master_request(mapped.planning, planner)
    except ContractValidationError as exc:
        return _closed_result(
            payload,
            state="UNAVAILABLE",
            answer="The Master planner was rejected before any specialist or AWS read.",
            model_errors=_planner_error_tokens(exc),
            model_calls=calls,
            route_status="PLANNER_REJECTED",
        )
    except Exception as exc:
        return _closed_result(
            payload,
            state="UNAVAILABLE",
            answer="Anthropic planning did not return a usable route. No AWS read was made.",
            model_errors=_failure_tokens(exc),
            model_calls=calls,
            route_status="PLANNER_FAILED",
        )

    route_status = planning.status.value
    blocked = _blocked_steps(planning)
    specialists = _specialist_names(planning)
    route_kinds = _route_kinds(planning)
    terminal_decision = planning.terminal_reason
    if planning.status is not Sprint4PlanningStatus.AUTHORIZED_PLAN or not _has_live_ops(planning):
        return _closed_result(
            payload,
            state="UNSUPPORTED" if planning.status is not Sprint4PlanningStatus.AUTHORIZED_PLAN else "UNAVAILABLE",
            answer=_planning_answer(planning.status, blocked),
            model_calls=calls,
            route_status=route_status,
            specialists=specialists,
            blocked=blocked,
            route_kinds=route_kinds,
            terminal_decision=terminal_decision,
        )

    try:
        tables, journal, principal = open_tables()
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return _closed_result(
            payload,
            state="UNAVAILABLE",
            answer="Operator AWS credentials were not available for a bounded read. No aircraft status is inferred.",
            tool_errors=[str(code)],
            model_calls=calls,
            route_status=route_status,
            specialists=specialists,
            blocked=blocked,
        )

    executed: list[ToolResult] = []
    specialist_error: list[str] = []
    try:
        result = LiveOpsSpecialist(
            provider=specialist_provider,
            handlers=_authorized_handlers(
                tables,
                executed,
                named_aircraft=named_aircraft_id(mapped.planning.canonical_user_text),
                user_text=mapped.planning.canonical_user_text,
            ),
        ).run(
            LiveOpsRunRequest(
                user_text=mapped.planning.canonical_user_text,
                request_id=mapped.planning.master_request_id,
                conversation_id=mapped.conversation_id,
                prior_turns=mapped.prior_turns,
            ),
            LiveOpsTrustedContext(
                tables=tables,
                now_epoch=now_epoch,
                correlation_id=mapped.planning.correlation_id,
                # Turn 1 reads the aircraft. Turn 2 may run one related keyed
                # query. Turn 3 must be a terminal ModelDecision. Two turns
                # made that follow-up query return BUDGET_EXHAUSTED.
                max_model_turns=3,
                max_tool_calls=2,
            ),
        )
    except Exception as exc:
        specialist_error.extend(_failure_tokens(exc))
        result = None

    return _from_tools(
        payload,
        executed=executed,
        journal=journal,
        principal=dict(principal),
        result=result,
        model_calls=calls,
        route_status=route_status,
        specialists=specialists,
        blocked=blocked,
        specialist_error=specialist_error,
        now_epoch=now_epoch,
        route_kinds=route_kinds,
        terminal_decision=terminal_decision,
    )


def _authorized_handlers(
    tables: object,
    executed: list[ToolResult],
    *,
    named_aircraft: str | None,
    user_text: str,
) -> dict[str, Callable[..., ToolResult]]:
    handlers: dict[str, Callable[..., ToolResult]] = {}
    for spec in LIVE_OPS_TOOLS:
        handlers[spec.name] = _handler(
            spec.name,
            tables,
            executed,
            named_aircraft=named_aircraft,
            user_text=user_text,
        )
    return handlers


def _handler(
    tool_name: str,
    tables: object,
    executed: list[ToolResult],
    *,
    named_aircraft: str | None,
    user_text: str,
) -> Callable[..., ToolResult]:
    def handle(call: object, **arguments: object) -> ToolResult:
        from wilvor_ai.live_ops import LiveOpsCall

        if not isinstance(call, LiveOpsCall):
            raise LocalDevClosedError("invalid_live_ops_call")
        rejection = _reject_arguments(tool_name, arguments, named_aircraft, user_text)
        if rejection is not None:
            refused = _refused_tool(call, tool_name, rejection)
            executed.append(refused)
            return refused
        resource, operation = _TOOL_BINDING[tool_name]
        outcome = run_authorized_live_ops_tool(
            _binding(tool_name, resource, operation),
            tables=tables,
            call=call,
            tool_name=tool_name,
            arguments=arguments,
        )
        executed.append(outcome.tool_result)
        return outcome.tool_result

    return handle


def _reject_arguments(
    tool_name: str,
    arguments: Mapping[str, object],
    named_aircraft: str | None,
    user_text: str,
) -> str | None:
    if tool_name == "get_observed_network_state":
        return "unbounded_population_refused"
    aircraft = arguments.get("aircraft_id")
    if isinstance(aircraft, str):
        normalized = aircraft.strip().lower()
        if named_aircraft is not None and normalized != named_aircraft:
            return "model_selector_rejected"
        if named_aircraft is None and not re.fullmatch(r"[a-z0-9]{4,32}", normalized):
            return "model_selector_rejected"
    callsign = arguments.get("callsign")
    if isinstance(callsign, str) and callsign.upper() not in user_text.upper():
        return "model_selector_rejected"
    return None


def _binding(tool_name: str, resource: str, operation: ReadOperation):
    nonce = token_hex(8)
    accepted = epoch_to_utc_z(1_700_000_000)
    deadline = epoch_to_utc_z(1_700_003_600)
    return describe_consumed_attempt_read_binding(
        owner_id=f"localdev-{nonce}",
        attempt_token=f"attempt-{nonce}",
        fence_epoch=1,
        grant_nonce=f"grant-{nonce}",
        idempotency_key=f"idem-{nonce}",
        payload_fingerprint=f"finger-{nonce}",
        ledger_state=LEDGER_EXECUTION_STATE_STARTED,
        grant_consumption_lifecycle=GRANT_CONSUMPTION_LIFECYCLE,
        master_request_id=f"mreq-{nonce}",
        planning_session_id=f"plan-{nonce}",
        step_id=f"step-{nonce}",
        invocation_id=f"inv-{nonce}",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        principal_ref=f"principal-{nonce}",
        policy_revision="local-dev-preview",
        tool_name=tool_name,
        resource_id=resource,
        operation=operation,
        region_scope=("DEV",),
        accepted_at_utc=accepted,
        deadline_utc=deadline,
        correlation_id=f"corr-{nonce}",
    )


def _refused_tool(call: Any, tool_name: str, code: str) -> ToolResult:
    from wilvor_ai.contracts import TemporalScope, ToolResultStatus

    as_of = epoch_to_utc_z(call.now_epoch)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=call.tool_call_id,
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.CURRENT,
        data={"found": False, "reason": code, "as_of_utc": as_of},
        evidence=(),
        as_of_utc=as_of,
        limitations=(code,),
        correlation_id=call.correlation_id,
    )


def _has_live_ops(planning: object) -> bool:
    decision = getattr(planning, "decision", None)
    plan = getattr(decision, "plan", None)
    steps = getattr(plan, "steps", ()) or ()
    return any(getattr(getattr(step, "invocation", None), "specialist", None) is MasterSpecialist.LIVE_OPS for step in steps)


def _route_kinds(planning: object) -> list[str]:
    decision = getattr(planning, "decision", None)
    plan = getattr(decision, "plan", None)
    steps = getattr(plan, "steps", ()) or ()
    tokens: list[str] = []
    for step in steps:
        invocation = getattr(step, "invocation", None)
        specialist = getattr(invocation, "specialist", None)
        purpose = getattr(invocation, "purpose", None)
        capability = getattr(step, "capability", None)
        if (
            isinstance(specialist, MasterSpecialist)
            and isinstance(purpose, MasterPurpose)
            and isinstance(capability, AgentCapability)
        ):
            token = route_kind_for(specialist, purpose, capability)
            if token is not None:
                tokens.append(token)
    return tokens


def _specialist_names(planning: object) -> list[str]:
    decision = getattr(planning, "decision", None)
    plan = getattr(decision, "plan", None)
    steps = getattr(plan, "steps", ()) or ()
    names: list[str] = []
    for step in steps:
        specialist = getattr(getattr(step, "invocation", None), "specialist", None)
        if isinstance(specialist, MasterSpecialist):
            names.append(specialist.value)
    return names


def _blocked_steps(planning: object) -> list[dict[str, str]]:
    decision = getattr(planning, "decision", None)
    plan = getattr(decision, "plan", None)
    steps = getattr(plan, "steps", ()) or ()
    blocked: list[dict[str, str]] = []
    for step in steps:
        specialist = getattr(getattr(step, "invocation", None), "specialist", None)
        if specialist is MasterSpecialist.LIVE_OPS or not isinstance(specialist, MasterSpecialist):
            continue
        blocked.append(
            {
                "specialist": specialist.value,
                "reason": _BLOCKED.get(specialist, "Specialist was not executed in the local preview."),
            }
        )
    return blocked


def _planning_answer(status: Sprint4PlanningStatus, blocked: list[dict[str, str]]) -> str:
    if blocked:
        return " ".join(item["reason"] for item in blocked)
    if status is Sprint4PlanningStatus.AUTHORIZED_PLAN:
        return "The authorized route did not include Live Operations. No AWS read was made."
    return (
        "The Master route was not authorized for a Live Operations read. "
        f"Planning status: {status.value}. No AWS read was made."
    )


def _from_tools(
    payload: Mapping[str, Any],
    *,
    executed: list[ToolResult],
    journal: list[dict[str, object]],
    principal: Mapping[str, str],
    result: object,
    model_calls: list[dict[str, object]],
    route_status: str,
    specialists: list[str],
    blocked: list[dict[str, str]],
    specialist_error: list[str],
    now_epoch: int,
    route_kinds: list[str] | None = None,
    terminal_decision: str | None = None,
) -> dict[str, Any]:
    tool_views = [_tool_view(item) for item in executed]
    missing = [item["aircraftId"] for item in tool_views if item["recordState"] == "MISSING" and item["aircraftId"]]
    stale = [item["aircraftId"] for item in tool_views if item["recordState"] == "STALE" and item["aircraftId"]]
    partial = [
        item["toolName"]
        for item in tool_views
        if "RELATED_RESOURCE_NOT_ALLOWLISTED" in item["limitations"] or item["recordState"] == "PARTIAL"
    ]
    tool_errors = [item["refusal"] for item in tool_views if item["refusal"]] + specialist_error
    state = "INSUFFICIENT_EVIDENCE" if any(item["recordState"] == "PRESENT" or item["recordState"] == "STALE" for item in tool_views) else "UNAVAILABLE"
    if not tool_views and not specialist_error:
        state = "UNAVAILABLE"
    answer = _qualify(tool_views, result, blocked, specialist_error)
    evidence = [_evidence_item(item, now_epoch) for item in tool_views if item["recordState"] in {"PRESENT", "STALE"}]
    evidence = [item for item in evidence if item is not None]
    limitations = list(_REQUIRED_LIMITATIONS)
    if evidence:
        limitations.append("SUPPLIED_EVIDENCE_NOT_AUTHENTICATED")
    return _envelope(
        payload,
        state=state,
        answer=answer,
        limitations=limitations,
        evidence=evidence,
        model_calls=model_calls,
        route_status=route_status,
        specialists=specialists,
        blocked=blocked,
        tool_views=tool_views,
        journal=journal,
        principal=principal,
        missing=missing,
        stale=stale,
        partial=partial,
        tool_errors=tool_errors,
        model_errors=specialist_error,
        specialist_status=_specialist_status(result),
        route_kinds=route_kinds,
        terminal_decision=terminal_decision,
    )


def _tool_view(result: ToolResult) -> dict[str, Any]:
    data = result.data if isinstance(result.data, dict) else {}
    aircraft = data.get("aircraft") if isinstance(data.get("aircraft"), dict) else {}
    found = data.get("found")
    current = data.get("aircraft_is_current")
    if found is False:
        record_state = "MISSING"
    elif found is True and current is False:
        record_state = "STALE"
    elif found is True and current is True:
        record_state = "PRESENT"
    elif found is True:
        record_state = "PARTIAL"
    else:
        record_state = "UNKNOWN"
    limitations = [str(item) for item in result.limitations]
    refusal = next((item for item in limitations if item in {"model_selector_rejected", "unbounded_population_refused"}), None)
    aircraft_id = data.get("aircraft_id") or aircraft.get("aircraft_id")
    freshness = aircraft.get("freshness_status")
    return {
        "toolName": result.tool_name,
        "toolCallId": result.tool_call_id,
        "status": result.status.value,
        "recordState": record_state,
        "aircraftId": aircraft_id if isinstance(aircraft_id, str) else None,
        "callsign": aircraft.get("callsign") if isinstance(aircraft.get("callsign"), str) else None,
        "positionTimeUtc": aircraft.get("position_time_utc") if isinstance(aircraft.get("position_time_utc"), str) else None,
        "freshnessStatus": freshness if isinstance(freshness, str) and freshness in _FRESHNESS_BANDS else None,
        "observations": _aircraft_observations(aircraft),
        "encounterCandidateCount": _encounter_candidate_count(result.tool_name, data),
        "asOfUtc": result.as_of_utc,
        "limitations": limitations,
        "refusal": refusal,
        "sourceName": _TOOL_TABLE.get(result.tool_name, "operator-dev-read"),
    }


def _format_observed(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _aircraft_observations(aircraft: Mapping[str, Any]) -> list[dict[str, str]]:
    """Allowlisted scalars that are actually present. Missing keys stay omitted."""

    observations: list[dict[str, str]] = []
    for key, label in _OBSERVED_AIRCRAFT_FIELDS:
        if key not in aircraft:
            continue
        rendered = _format_observed(aircraft.get(key))
        if rendered is None:
            continue
        observations.append({"field": key, "label": label, "value": rendered})
    return observations


def _encounter_candidate_count(tool_name: str, data: Mapping[str, Any]) -> int | None:
    if tool_name != "search_current_encounters":
        return None
    unevaluated = data.get("operationally_unevaluated")
    encounters = data.get("encounters")
    count = len(unevaluated) if isinstance(unevaluated, list) else 0
    if count == 0 and isinstance(encounters, list):
        count = len([item for item in encounters if item])
    return count


def _qualify(
    tools: list[dict[str, Any]],
    result: object,
    blocked: list[dict[str, str]],
    specialist_error: list[str],
) -> str:
    sentences: list[str] = [NOT_PRODUCTION_NOTICE]
    if not tools:
        sentences.append("No specialist tool returned a row. No aircraft, hazard, weather, or recommendation is inferred.")
    for item in tools:
        if item["recordState"] == "MISSING" and item["toolName"] == "get_aircraft_operational_context":
            sentences.append(
                f"GetItem for aircraft {item['aircraftId'] or 'the requested id'} returned no item "
                "from wilvor-dev-aircraft-current-state. The record is missing. "
                "That is not a verified zero and it is not a statement that the aircraft is inactive or clear of hazards."
            )
        elif item["recordState"] == "MISSING":
            sentences.append(
                f"Tool {item['toolName']} returned no record. No aircraft, hazard, weather, or recommendation is inferred."
            )
        elif item["recordState"] == "STALE" and item["toolName"] != "get_aircraft_operational_context":
            sentences.append(
                f"Tool {item['toolName']} returned a row that is not current. It is not treated as operational status."
            )
        elif item["recordState"] == "STALE":
            sentences.append(
                f"A stored aircraft row for {item['aircraftId']} was returned, and expires_at_epoch is not after the evaluation clock. "
                "The row is stale. It is not current operational status."
            )
            if item["callsign"]:
                sentences.append(f"Stored callsign: {item['callsign']}.")
            if item["positionTimeUtc"]:
                sentences.append(f"Stored position time: {item['positionTimeUtc']}.")
            _append_observations(sentences, item)
        elif item["recordState"] == "PRESENT":
            sentences.append(
                f"A stored aircraft row for {item['aircraftId']} was returned and expires_at_epoch is after the evaluation clock. "
                "This is operator-authenticated DEV evidence, not Lambda-role provenance and not an admitted operational claim."
            )
            if item["callsign"]:
                sentences.append(f"Stored callsign: {item['callsign']}.")
            if item["positionTimeUtc"]:
                sentences.append(f"Stored position time: {item['positionTimeUtc']}.")
            _append_observations(sentences, item)
        elif item["toolName"] == "search_current_encounters":
            count = item.get("encounterCandidateCount")
            if count:
                sentences.append(
                    f"A bounded encounter query returned {count} encounter candidates. "
                    "They are not verified impacts, and they are not a safety conclusion."
                )
            else:
                sentences.append(
                    "A bounded encounter query returned no encounter rows. "
                    "That does not mean the aircraft is clear of hazards."
                )
        elif item["refusal"]:
            sentences.append(f"Tool {item['toolName']} was refused before a read: {item['refusal']}.")
        else:
            sentences.append(f"Tool {item['toolName']} finished with status {item['status']}. No additional facts are inferred.")
        if "RELATED_RESOURCE_NOT_ALLOWLISTED" in item["limitations"] and item["toolName"] != "search_current_encounters":
            sentences.append(
                "Projection, hazard, weather, and recommendation resources were not read for this tool."
            )
    if specialist_error:
        sentences.append("The specialist or model failed closed: " + ", ".join(specialist_error) + ".")
    elif result is not None:
        status = getattr(getattr(result, "execution_status", None), "value", None)
        if status:
            sentences.append(f"Live Ops execution status: {status}. Model claims are not admitted.")
    for item in blocked:
        sentences.append(item["reason"])
    return " ".join(sentences)


def _freshness_band(item: dict[str, Any]) -> str:
    stored = item.get("freshnessStatus")
    if isinstance(stored, str) and stored in _FRESHNESS_BANDS:
        return stored
    if item["recordState"] == "STALE":
        return "STALE"
    return "UNKNOWN"


def _append_observations(sentences: list[str], item: dict[str, Any]) -> None:
    observations = item.get("observations") or []
    if observations:
        rendered = ", ".join(f"{entry['label']} {entry['value']}" for entry in observations)
        sentences.append(
            "Observed fields present on the stored row: "
            + rendered
            + ". Absent fields are omitted. These are operator-authenticated developer observations, not admitted operational claims."
        )
    stored = item.get("freshnessStatus")
    if stored:
        sentences.append(
            f"Stored freshness status is {stored}. It does not establish source authenticity."
        )
    elif item["recordState"] == "PRESENT":
        sentences.append(
            "No stored freshness status was on the row. Source freshness is not established."
        )


def _specialist_status(result: object) -> str | None:
    if result is None:
        return None
    answer = getattr(result, "answer", None)
    return answer if isinstance(answer, str) and answer.strip() else None


def _evidence_item(item: dict[str, Any], now_epoch: int) -> dict[str, Any] | None:
    if not item["aircraftId"]:
        return None
    observation = item["positionTimeUtc"] if isinstance(item["positionTimeUtc"], str) and _UTC.match(item["positionTimeUtc"]) else None
    evaluation = epoch_to_utc_z(now_epoch)
    if observation == evaluation:
        evaluation = None
    return {
        "entityKind": "AIRCRAFT",
        "entityId": item["aircraftId"],
        "callsign": item["callsign"],
        "aircraftId": item["aircraftId"],
        "hazardId": None,
        "encounterId": None,
        "toolName": item["toolName"],
        "toolCallId": item["toolCallId"],
        "sourceName": item["sourceName"],
        "sourceVersion": None,
        "observationTimeUtc": observation,
        "evaluationTimeUtc": evaluation,
        "freshnessBand": _freshness_band(item),
        "limitationCodes": ["OPERATOR_AUTHENTICATED_DEV", *item["limitations"]],
        "truncated": False,
        "hasMore": False,
        "pageEmpty": False,
        "verifiedZeroMinted": False,
    }


def _closed_result(
    payload: Mapping[str, Any],
    *,
    state: str,
    answer: str,
    model_errors: list[str] | None = None,
    tool_errors: list[str] | None = None,
    model_calls: list[dict[str, object]] | None = None,
    route_status: str = "NOT_RUN",
    specialists: list[str] | None = None,
    blocked: list[dict[str, str]] | None = None,
    route_kinds: list[str] | None = None,
    terminal_decision: str | None = None,
) -> dict[str, Any]:
    return _envelope(
        payload,
        state=state,
        answer=answer,
        limitations=list(_REQUIRED_LIMITATIONS),
        evidence=[],
        model_calls=model_calls or [],
        route_status=route_status,
        specialists=specialists or [],
        blocked=blocked or [],
        tool_views=[],
        journal=[],
        principal={"evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV", "sourceAuthenticity": "NOT_ESTABLISHED"},
        missing=[],
        stale=[],
        partial=[],
        tool_errors=tool_errors or [],
        model_errors=model_errors or [],
        specialist_status=None,
        route_kinds=route_kinds or [],
        terminal_decision=terminal_decision,
    )


def _envelope(
    payload: Mapping[str, Any],
    *,
    state: str,
    answer: str,
    limitations: list[str],
    evidence: list[dict[str, Any]],
    model_calls: list[dict[str, object]],
    route_status: str,
    specialists: list[str],
    blocked: list[dict[str, str]],
    tool_views: list[dict[str, Any]],
    journal: list[dict[str, object]],
    principal: Mapping[str, str],
    missing: list[str],
    stale: list[str],
    partial: list[str],
    tool_errors: list[str],
    model_errors: list[str],
    specialist_status: str | None,
    route_kinds: list[str] | None = None,
    terminal_decision: str | None = None,
) -> dict[str, Any]:
    status = _STATUS_TEXT[state]
    return {
        "schemaVersion": "wilvor.ai.copilot.session_query.v1",
        "standing": {
            "routeKey": "POST /ai/query",
            "routeEnabled": False,
            "productionAiActive": False,
            "transport": LOCAL_DEV_PREVIEW,
            "backendRouteOwner": "integration_owner",
        },
        "masterRequestId": payload.get("masterRequestId"),
        "planningSessionId": payload.get("planningSessionId"),
        "conversationId": payload.get("conversationId"),
        "correlationId": payload.get("correlationId"),
        "applicationRequestTimeUtc": payload.get("applicationRequestTimeUtc"),
        "state": state,
        "statusText": status,
        "assistantText": status,
        "limitationCodes": limitations,
        "claimsAdmitted": False,
        "productionAuthority": "DENIED",
        "sourceAuthenticity": "NOT_ESTABLISHED",
        "requestCoverage": "NOT_ESTABLISHED",
        "completenessNotice": _COMPLETENESS,
        "routeDisabledNotice": _ROUTE_NOTICE,
        "productionAiActive": False,
        "evidence": evidence,
        "pageEmpty": len(evidence) == 0,
        "truncated": False,
        "hasMore": False,
        "verifiedZeroMinted": False,
        "developerPreview": {
            "notProduction": True,
            "evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV",
            "sourceAuthenticity": "NOT_ESTABLISHED",
            "claimsAdmitted": False,
            "notice": NOT_PRODUCTION_NOTICE,
            "answer": answer,
            "specialistStatusText": specialist_status,
            "routeStatus": route_status,
            "routeKinds": list(route_kinds or []),
            "terminalDecision": terminal_decision,
            "specialists": specialists,
            "blockedSpecialists": blocked,
            "toolResults": [
                {
                    "toolName": item["toolName"],
                    "toolCallId": item["toolCallId"],
                    "status": item["status"],
                    "recordState": item["recordState"],
                    "aircraftId": item["aircraftId"],
                    "callsign": item["callsign"],
                    "positionTimeUtc": item["positionTimeUtc"],
                    "freshnessStatus": item.get("freshnessStatus"),
                    "observations": list(item.get("observations") or []),
                    "encounterCandidateCount": item.get("encounterCandidateCount"),
                    "asOfUtc": item["asOfUtc"],
                    "sourceName": item["sourceName"],
                    "limitations": item["limitations"],
                }
                for item in tool_views
            ],
            "awsOperations": [_public_operation(item) for item in journal],
            "modelOperations": list(model_calls),
            "principalLabel": principal.get("principalLabel"),
            "missingEvidence": missing,
            "staleEvidence": stale,
            "partialEvidence": partial,
            "toolErrors": tool_errors,
            "modelErrors": model_errors,
        },
    }


def _public_operation(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "operation": item.get("operation"),
        "table": item.get("table"),
        "indexName": item.get("indexName"),
        "limit": item.get("limit"),
        "consistentRead": item.get("consistentRead"),
        "keyAttributes": list(item.get("keyAttributes") or ()),
        "bounded": item.get("bounded") is True,
        "provenance": "OPERATOR_AUTHENTICATED_DEV",
    }


_DIAGNOSTIC_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def _planner_error_tokens(exc: ContractValidationError) -> list[str]:
    """Stable planner codes. Routing diagnostics are specialist, purpose, and capability only."""

    tokens: list[str] = []
    for item in exc.errors:
        if not isinstance(item, str) or _DIAGNOSTIC_TOKEN.fullmatch(item) is None:
            continue
        if ":" in item and not item.startswith(("specialist:", "purpose:", "capability:")):
            continue
        tokens.append(item)
    return tokens or ["planner_rejected"]


def _failure_tokens(exc: Exception) -> list[str]:
    """Stable codes and sanitized transport diagnostics. Never exception text."""

    public = getattr(exc, "public_tokens", None)
    if callable(public):
        tokens = [
            token
            for token in public()
            if isinstance(token, str) and _DIAGNOSTIC_TOKEN.fullmatch(token)
        ]
        if tokens:
            return tokens
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _DIAGNOSTIC_TOKEN.fullmatch(code):
        return [code]
    name = type(exc).__name__
    if _DIAGNOSTIC_TOKEN.fullmatch(name):
        return [name]
    return ["model_failure"]


def assert_loopback_bind(host: str) -> None:
    if host != "127.0.0.1":
        raise LocalDevClosedError("nonlocal_bind_rejected")


__all__ = [
    "LOCAL_DEV_PATH",
    "NOT_PRODUCTION_NOTICE",
    "PRODUCTION_QUERY_PATH",
    "LocalDevClosedError",
    "admit_local_request",
    "assert_loopback_bind",
    "named_aircraft_id",
    "run_local_dev_copilot",
]

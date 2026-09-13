"""Phase 2C.2 bound historical analytics adapter and catalog tests."""

from __future__ import annotations

import inspect

import pytest

from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics import (
    HISTORICAL_ANALYTICS_TOOLS,
    HistoricalAnalyticsAdapter,
    HistoricalAnalyticsCall,
)
from wilvor_ai.historical_analytics_mapping import INVALID_REQUEST_CODE
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    HAZARD_VERSION_WINDOW_LIMITATION,
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
    LIST_DEFAULT_LIMIT,
    CoverageEvidence,
    EncounterSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryErrorEvidence,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListEncountersResult,
    ListHistoricalEncountersRequest,
    QueryEvidence,
    QueryExecutionEvidence,
    RiskSummaryResult,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
    HazardVersionSummaryResult,
)


AS_OF = "2026-09-13T12:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
TOOL_CALL_ID = "tool-call-historical-adapter-001"

_TRUSTED = frozenset(
    {
        "call",
        "operations",
        "as_of_utc",
        "tool_call_id",
        "correlation_id",
        "dataset",
        "temporal_scope",
        "freshness",
        "InternalQueryId",
        "query_id",
        "sql",
        "SQL",
        "workgroup",
        "database",
        "output_location",
        "bucket",
    }
)


class FakeOperations:
    def __init__(self, response: HistoricalQueryResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def summarize_historical_encounters(self, request, *, as_of_utc):
        self.calls.append(
            {
                "method": "summarize_historical_encounters",
                "request": request,
                "as_of_utc": as_of_utc,
            }
        )
        return self.response

    def summarize_historical_risks(self, request, *, as_of_utc):
        self.calls.append(
            {
                "method": "summarize_historical_risks",
                "request": request,
                "as_of_utc": as_of_utc,
            }
        )
        return self.response

    def summarize_historical_hazard_versions(self, request, *, as_of_utc):
        self.calls.append(
            {
                "method": "summarize_historical_hazard_versions",
                "request": request,
                "as_of_utc": as_of_utc,
            }
        )
        return self.response

    def list_historical_encounters(self, request, *, as_of_utc):
        self.calls.append(
            {
                "method": "list_historical_encounters",
                "request": request,
                "as_of_utc": as_of_utc,
            }
        )
        return self.response


def _coverage(evaluability=Evaluability.EVALUABLE, reason="window_evaluable"):
    return CoverageEvidence(
        evaluability=evaluability,
        reason=reason,
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _evidence(
    operation,
    *,
    datasets,
    status_fields,
    executions=(),
    coverage_state=Evaluability.EVALUABLE,
):
    return QueryEvidence(
        query_name=operation,
        datasets=datasets,
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=coverage_state,
        collection_epoch_ids=("epoch-1",),
        query_executions=executions,
        evaluated_as_of_utc=AS_OF,
        **status_fields,
    )


def _execution(query_id):
    return QueryExecutionEvidence(
        query_id=query_id,
        query_execution_id="exec-1",
        rows_returned=1,
        data_scanned_bytes=16,
        workgroup="historical-analytics",
    )


def _adapter(response) -> tuple[HistoricalAnalyticsAdapter, FakeOperations]:
    operations = FakeOperations(response)
    adapter = HistoricalAnalyticsAdapter(
        HistoricalAnalyticsCall(
            operations=operations,
            as_of_utc=AS_OF,
            tool_call_id=TOOL_CALL_ID,
        )
    )
    return adapter, operations


def _succeeded_encounters() -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            hazard_id="hazard-1",
            hazard_type="CONVECTION",
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            status_fields={
                "semantic_match_count": 2,
                "semantic_match_count_is_exact": True,
            },
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS),),
        ),
        result=EncounterSummaryResult(
            physical_record_count=2,
            distinct_encounter_count=2,
            distinct_aircraft_count=1,
            distinct_hazard_count=1,
            distinct_dedup_count=2,
            min_event_time_utc="2026-09-11T10:00:00Z",
            max_event_time_utc="2026-09-11T10:30:00Z",
        ),
    )


def test_catalog_is_exactly_four_read_only_historical_tools():
    names = [item.name for item in HISTORICAL_ANALYTICS_TOOLS]
    assert names == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    ]
    assert len(set(names)) == 4
    assert "summarize_historical_risks_by_level" not in names
    for spec in HISTORICAL_ANALYTICS_TOOLS:
        assert spec.authority_mode is AgentAuthorityMode.READ_ONLY_ADVISORY
        assert spec.capabilities == (
            AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        )
        assert AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT not in (
            spec.capabilities
        )
        text = spec.description.lower()
        assert "sql" not in text
        assert "athena" not in text
        for claimed in (
            "callsign",
            "geography",
            "airport",
            "prediction",
            "recommendation",
        ):
            if claimed in text:
                assert "not" in text
        if spec.name == "summarize_historical_hazard_versions":
            assert "materializ" in text
            assert "validity" in text


def test_catalog_input_fields_are_the_locked_allowlist():
    expected = {
        "summarize_historical_encounters": (
            ("start_utc", True),
            ("end_utc", True),
            ("aircraft_id", False),
            ("hazard_id", False),
            ("hazard_type", False),
        ),
        "summarize_historical_risks": (
            ("start_utc", True),
            ("end_utc", True),
            ("aircraft_id", False),
            ("hazard_id", False),
            ("encounter_id", False),
            ("risk_level", False),
        ),
        "summarize_historical_hazard_versions": (
            ("start_utc", True),
            ("end_utc", True),
            ("hazard_id", False),
            ("hazard_type", False),
            ("product_type", False),
        ),
        "list_historical_encounters": (
            ("start_utc", True),
            ("end_utc", True),
            ("aircraft_id", False),
            ("hazard_id", False),
            ("limit", False),
        ),
    }
    for spec in HISTORICAL_ANALYTICS_TOOLS:
        assert [(item.name, item.required) for item in spec.input_fields] == list(
            expected[spec.name]
        )
        assert _TRUSTED.isdisjoint(item.name for item in spec.input_fields)


def test_bound_handlers_match_catalog_and_exclude_trusted_context():
    adapter, _ = _adapter(_succeeded_encounters())
    public_tools = {
        name
        for name, value in inspect.getmembers(adapter, predicate=inspect.ismethod)
        if not name.startswith("_") and name != "get_handler"
    }
    assert public_tools == {item.name for item in HISTORICAL_ANALYTICS_TOOLS}
    for spec in HISTORICAL_ANALYTICS_TOOLS:
        handler = adapter.get_handler(spec.name)
        parameters = [
            parameter
            for name, parameter in inspect.signature(handler).parameters.items()
            if name != "self"
        ]
        assert [item.name for item in spec.input_fields] == [
            parameter.name for parameter in parameters
        ]
        assert [item.required for item in spec.input_fields] == [
            parameter.default is inspect.Parameter.empty for parameter in parameters
        ]
        assert _TRUSTED.isdisjoint(parameter.name for parameter in parameters)


def test_summarize_encounters_delegates_filters_and_trusted_as_of():
    adapter, operations = _adapter(_succeeded_encounters())
    result = adapter.summarize_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_type="CONVECTION",
    )
    assert len(operations.calls) == 1
    call = operations.calls[0]
    request = call["request"]
    assert call["method"] == "summarize_historical_encounters"
    assert isinstance(request, SummarizeHistoricalEncountersRequest)
    assert request.aircraft_id == "abc123"
    assert request.hazard_id == "hazard-1"
    assert request.hazard_type == "CONVECTION"
    assert call["as_of_utc"] == AS_OF
    assert result.status is ToolResultStatus.SUCCESS
    assert result.tool_call_id == TOOL_CALL_ID
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert result.correlation_id is None


def test_summarize_risks_maps_encounter_id_and_risk_level():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        requested_scope=SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            encounter_id="proj-1#hazard-1#v1",
            risk_level="HIGH",
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            datasets=("risk",),
            status_fields={
                "semantic_match_count": 1,
                "semantic_match_count_is_exact": True,
            },
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_RISKS),),
        ),
        result=RiskSummaryResult(
            physical_record_count=1,
            distinct_risk_count=1,
            distinct_encounter_count=1,
            distinct_aircraft_count=1,
            min_risk_score=4,
            max_risk_score=4,
        ),
    )
    adapter, operations = _adapter(response)
    result = adapter.summarize_historical_risks(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        encounter_id="proj-1#hazard-1#v1",
        risk_level="HIGH",
    )
    request = operations.calls[0]["request"]
    assert isinstance(request, SummarizeHistoricalRisksRequest)
    assert request.encounter_id == "proj-1#hazard-1#v1"
    assert request.risk_level == "HIGH"
    assert operations.calls[0]["as_of_utc"] == AS_OF
    assert result.tool_call_id == TOOL_CALL_ID


def test_summarize_hazard_versions_maps_product_type():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
        requested_scope=SummarizeHistoricalHazardVersionsRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            product_type="SIGMET",
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            datasets=("hazard_version",),
            status_fields={
                "semantic_match_count": 1,
                "semantic_match_count_is_exact": True,
            },
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS),),
        ),
        result=HazardVersionSummaryResult(
            physical_record_count=1,
            distinct_hazard_count=1,
            distinct_hazard_version_count=1,
            min_materialized_at_utc="2026-09-11T10:00:00Z",
            max_materialized_at_utc="2026-09-11T10:05:00Z",
        ),
        limitations=(HAZARD_VERSION_WINDOW_LIMITATION,),
    )
    adapter, operations = _adapter(response)
    result = adapter.summarize_historical_hazard_versions(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        product_type="SIGMET",
    )
    request = operations.calls[0]["request"]
    assert isinstance(request, SummarizeHistoricalHazardVersionsRequest)
    assert request.product_type == "SIGMET"
    assert HAZARD_VERSION_WINDOW_LIMITATION in result.limitations


def test_list_encounters_default_and_explicit_limit():
    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T10:00:00Z",
        hazard_type="CONVECTION",
    )
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        requested_scope=ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            status_fields={
                "semantic_match_count": 1,
                "semantic_match_count_is_exact": True,
            },
            executions=(_execution(INTERNAL_QUERY_ID_LIST_ENCOUNTERS),),
        ),
        result=ListEncountersResult(records=(record,)),
    )
    adapter, operations = _adapter(response)
    adapter.list_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
    )
    assert operations.calls[0]["request"].limit == LIST_DEFAULT_LIMIT
    assert LIST_DEFAULT_LIMIT == 100
    adapter.list_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        hazard_id="hazard-1",
        limit=25,
    )
    assert operations.calls[1]["request"].hazard_id == "hazard-1"
    assert operations.calls[1]["request"].limit == 25


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        (
            "summarize_historical_encounters",
            {"start_utc": "2026-09-11T10:00:00", "end_utc": WINDOW[1]},
        ),
        (
            "summarize_historical_encounters",
            {
                "start_utc": "2026-09-11T10:00:00.500000Z",
                "end_utc": WINDOW[1],
            },
        ),
        (
            "summarize_historical_encounters",
            {
                "start_utc": "2026-09-01T00:00:00Z",
                "end_utc": "2026-09-10T00:00:00Z",
            },
        ),
        (
            "summarize_historical_encounters",
            {
                "start_utc": WINDOW[0],
                "end_utc": WINDOW[1],
                "aircraft_id": "bad#id",
            },
        ),
        (
            "list_historical_encounters",
            {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
        ),
        (
            "list_historical_encounters",
            {
                "start_utc": WINDOW[0],
                "end_utc": WINDOW[1],
                "aircraft_id": "abc123",
                "limit": 0,
            },
        ),
        (
            "list_historical_encounters",
            {
                "start_utc": WINDOW[0],
                "end_utc": WINDOW[1],
                "aircraft_id": "abc123",
                "limit": 201,
            },
        ),
    ],
)
def test_invalid_requests_do_not_call_operations(method, kwargs):
    adapter, operations = _adapter(_succeeded_encounters())
    result = getattr(adapter, method)(**kwargs)
    assert operations.calls == []
    assert result.status is ToolResultStatus.UNKNOWN
    assert result.data["status"] == INVALID_REQUEST_CODE
    assert result.evidence == ()
    assert result.as_of_utc is None
    assert result.tool_call_id == TOOL_CALL_ID
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert result.correlation_id is None


def test_trusted_as_of_and_tool_call_id_cannot_be_overridden():
    adapter, operations = _adapter(_succeeded_encounters())
    with pytest.raises(TypeError):
        adapter.summarize_historical_encounters(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            as_of_utc="2099-01-01T00:00:00Z",
        )
    with pytest.raises(TypeError):
        adapter.summarize_historical_encounters(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            tool_call_id="model-forged-id",
        )
    result = adapter.summarize_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )
    assert operations.calls[0]["as_of_utc"] == AS_OF
    assert result.tool_call_id == TOOL_CALL_ID


def test_verified_zero_and_blocked_and_truncated_preserve_mapping():
    zero = HistoricalQueryResponse(
        status=HistoricalQueryStatus.VERIFIED_ZERO,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            status_fields={
                "semantic_match_count": 0,
                "semantic_match_count_is_exact": True,
            },
        ),
        result=EncounterSummaryResult(
            physical_record_count=0,
            distinct_encounter_count=0,
            distinct_aircraft_count=0,
            distinct_hazard_count=0,
            distinct_dedup_count=0,
        ),
    )
    adapter, _ = _adapter(zero)
    found = adapter.summarize_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )
    assert found.status is ToolResultStatus.NOT_FOUND
    assert found.evidence[0].completeness.status == "EVALUABLE"
    assert found.evidence[0].match_cardinality.exact_count == 0
    assert found.evidence[0].match_cardinality.is_exact is True

    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T10:00:00Z",
        hazard_type="CONVECTION",
    )
    truncated = HistoricalQueryResponse(
        status=HistoricalQueryStatus.RESULT_TRUNCATED,
        operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        requested_scope=ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            limit=1,
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            status_fields={
                "semantic_match_count": None,
                "semantic_match_count_is_exact": False,
                "minimum_match_count": 2,
            },
        ),
        result=ListEncountersResult(records=(record,), truncated=True),
    )
    adapter, _ = _adapter(truncated)
    partial = adapter.list_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        limit=1,
    )
    assert partial.status is ToolResultStatus.PARTIAL
    assert partial.evidence[0].match_cardinality.minimum_count == 2

    blocked = HistoricalQueryResponse(
        status=HistoricalQueryStatus.COVERAGE_BLOCKED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(Evaluability.GAP_OR_UNCERTAIN, "gap_present"),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.GAP_OR_UNCERTAIN,
            status_fields={
                "semantic_match_count": 0,
                "semantic_match_count_is_exact": True,
            },
        ),
        error=HistoricalQueryErrorEvidence(code="gap_present", message="gap_present"),
    )
    adapter, _ = _adapter(blocked)
    unavailable = adapter.summarize_historical_encounters(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )
    assert unavailable.status is ToolResultStatus.UNAVAILABLE
    assert unavailable.status is not ToolResultStatus.NOT_FOUND


def test_module_exposes_no_unbound_tool_functions():
    import wilvor_ai.historical_analytics as module

    for spec in HISTORICAL_ANALYTICS_TOOLS:
        value = getattr(module, spec.name, None)
        assert value is None or not inspect.isfunction(value)


def test_programming_errors_are_not_mapped_as_invalid_requests():
    class BrokenOperations:
        def summarize_historical_encounters(self, request, *, as_of_utc):
            raise RuntimeError("programmer-error")

    adapter = HistoricalAnalyticsAdapter(
        HistoricalAnalyticsCall(
            operations=BrokenOperations(),
            as_of_utc=AS_OF,
            tool_call_id=TOOL_CALL_ID,
        )
    )
    with pytest.raises(RuntimeError, match="programmer-error"):
        adapter.summarize_historical_encounters(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        )


def test_get_handler_rejects_unknown_and_returns_bound_method():
    adapter, operations = _adapter(_succeeded_encounters())
    handler = adapter.get_handler("summarize_historical_encounters")
    result = handler(start_utc=WINDOW[0], end_utc=WINDOW[1])
    assert result.tool_call_id == TOOL_CALL_ID
    assert operations.calls[0]["as_of_utc"] == AS_OF
    with pytest.raises(ContractValidationError):
        adapter.get_handler("run_sql")
    with pytest.raises(ContractValidationError):
        adapter.get_handler("summarize_historical_risks_by_level")

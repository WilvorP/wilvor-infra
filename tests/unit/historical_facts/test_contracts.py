"""Historical fact contract construction and JSON-safe serialization."""

from __future__ import annotations

from decimal import Decimal

import pytest

from wilvor_historical.contracts import (
    ENCOUNTER_FACT_SCHEMA_VERSION,
    RISK_FACT_SCHEMA_VERSION,
    Dataset,
    EncounterFact,
    FactKind,
    HistoricalFactError,
    json_safe,
)
from wilvor_historical.from_events import (
    build_encounter_fact,
    build_risk_fact,
)


def _encounter_detail(**overrides):
    detail = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "aircraft_state_version": "state-v1",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "encounter_state": "DETECTED",
        "geometry_overlap_status": "INSIDE_NOW",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "OVERLAP",
        "exact_intersection_confirmed": True,
        "detected_at_epoch": 1_700_000_000,
        "detected_at_utc": "2023-11-14T22:13:20Z",
        "geometry_hash": "geom-1",
        "hazard_type": "CONVECTION",
        "inside_now": True,
        "corridor_intersects": True,
        "schema_version": "wilvor.aircraft_hazard_encounter.v4.0",
        "correlation_id": "corr-1",
    }
    detail.update(overrides)
    return detail


def test_json_safe_preserves_none_false_zero_and_empty():
    payload = json_safe(
        {
            "missing": None,
            "flag": False,
            "count": 0,
            "items": [],
            "score": Decimal("0"),
            "partial": Decimal("1.5"),
        }
    )
    assert payload["missing"] is None
    assert payload["flag"] is False
    assert payload["count"] == 0
    assert payload["items"] == []
    assert payload["score"] == 0
    assert payload["partial"] == 1.5
    assert not any(isinstance(value, Decimal) for value in payload.values())


def test_encounter_observed_envelope_and_identity():
    fact = build_encounter_fact(
        _encounter_detail(),
        producer_source="wilvor.encounter",
        producer_detail_type="encounter.updated",
    )
    payload = fact.to_dict()
    assert payload["dataset"] == Dataset.ENCOUNTER.value
    assert payload["fact_schema_version"] == ENCOUNTER_FACT_SCHEMA_VERSION
    assert payload["fact_kind"] == FactKind.ENCOUNTER_OBSERVED.value
    assert payload["record_id"] == "proj-1#hazard-1#v1"
    assert payload["dedup_id"] == (
        "proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED"
    )
    assert payload["inside_now"] is True
    assert payload["resolved_at_epoch"] is None


def test_encounter_terminal_dedup_includes_resolved_at():
    fact = build_encounter_fact(
        _encounter_detail(
            encounter_state="SUPERSEDED",
            resolved_at_epoch=1_700_000_100,
            resolved_at_utc="2023-11-14T22:15:00Z",
            resolution_reason="newer projection",
        ),
        producer_source="wilvor.encounter",
        producer_detail_type="encounter.resolved",
    )
    assert fact.fact_kind is FactKind.ENCOUNTER_TERMINAL
    assert fact.dedup_id == (
        "proj-1#hazard-1#v1|ENCOUNTER_TERMINAL|1700000000|SUPERSEDED|1700000100"
    )


def test_risk_result_identity_is_risk_id():
    fact = build_risk_fact(
        {
            "risk_id": "risk#abc",
            "encounter_id": "proj-1#hazard-1#v1",
            "aircraft_id": "abc123",
            "hazard_id": "hazard-1",
            "hazard_source_version": "v1",
            "projection_id": "proj-1",
            "risk_score": Decimal("0"),
            "risk_level": "LOW",
            "generated_at_epoch": Decimal("1700000000"),
            "generated_at_utc": "2023-11-14T22:13:20Z",
            "scoring_ruleset_version": "wilvor.risk.ruleset.v2",
            "scoring_config_version": "wilvor.risk.config.dev.v1",
            "hazard_type": "CONVECTION",
            "encounter_state": "DETECTED",
            "confidence": "MEDIUM",
            "freshness_status": "FRESH",
            "valid_until_utc": "2023-11-14T23:13:20Z",
            "schema_version": "wilvor.risk_results.v4.0",
        },
        producer_source="wilvor.risk",
        producer_detail_type="risk.updated",
    )
    payload = fact.to_dict()
    assert payload["dataset"] == "risk"
    assert payload["fact_schema_version"] == RISK_FACT_SCHEMA_VERSION
    assert payload["fact_kind"] == FactKind.RISK_RESULT.value
    assert payload["record_id"] == "risk#abc"
    assert payload["dedup_id"] == "risk#abc"
    assert payload["risk_score"] == 0
    assert not isinstance(payload["generated_at_epoch"], Decimal)


def test_mismatched_schema_version_is_rejected():
    fact = build_encounter_fact(
        _encounter_detail(),
        producer_source="wilvor.encounter",
        producer_detail_type="encounter.updated",
    )
    values = fact.__dict__.copy()
    values["fact_schema_version"] = "wilvor.historical.encounter_fact.v0"
    with pytest.raises(HistoricalFactError, match="fact_schema_version"):
        EncounterFact(**values)

"""Glue structured-table columns must match historical fact contracts."""

from __future__ import annotations

import re
import types
from collections.abc import Mapping as AbcMapping
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints

from wilvor_historical.contracts import (
    Dataset,
    EncounterFact,
    FactKind,
    HazardGeometryFact,
    HazardVersionFact,
    RiskFact,
)
from wilvor_historical.time import partition_date_utc


REPO_ROOT = Path(__file__).resolve().parents[3]
LOCALS_TF = REPO_ROOT / "modules" / "historical_analytics" / "locals.tf"
GLUE_TF = REPO_ROOT / "modules" / "historical_analytics" / "glue.tf"
FACTS_LOCALS_TF = REPO_ROOT / "modules" / "historical_facts" / "locals.tf"

COLUMN_RE = re.compile(
    r'\{\s*name\s*=\s*"([^"]+)",\s*type\s*=\s*"([^"]+)"\s*\}'
)

STRUCTURED_FACTS = {
    "encounter": EncounterFact,
    "risk": RiskFact,
    "hazard_version": HazardVersionFact,
}


def _extract_bracket_list(text: str, key: str) -> str:
    marker = f"{key} = ["
    start = text.index(marker) + len(marker)
    depth = 1
    index = start
    while index < len(text) and depth:
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        index += 1
    return text[start : index - 1]


def parse_structured_columns() -> dict[str, list[tuple[str, str]]]:
    text = LOCALS_TF.read_text(encoding="utf-8")
    return {
        dataset: COLUMN_RE.findall(_extract_bracket_list(text, dataset))
        for dataset in STRUCTURED_FACTS
    }


def _is_mapping(annotation: Any) -> bool:
    if annotation in {Mapping, AbcMapping}:
        return True
    return get_origin(annotation) in {Mapping, AbcMapping}


def glue_type_for(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (list, dict, tuple, set, frozenset):
        raise AssertionError(
            f"nested container {annotation!r} is not allowed on structured tables"
        )
    if _is_mapping(annotation):
        raise AssertionError(
            "nested mapping is not allowed on structured Glue tables"
        )
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if not non_none:
            raise AssertionError(f"unsupported empty union {annotation!r}")
        if len(non_none) == 1:
            return glue_type_for(non_none[0])
        if set(non_none) == {int, float}:
            return "double"
        raise AssertionError(f"ambiguous union {annotation!r}")
    if annotation in {str, Dataset, FactKind}:
        return "string"
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return "string"
    if annotation is int:
        return "bigint"
    if annotation is bool:
        return "boolean"
    if annotation is float:
        return "double"
    raise AssertionError(f"unsupported Glue mapping for {annotation!r}")


def expected_columns(fact_cls: type) -> list[tuple[str, str]]:
    hints = get_type_hints(fact_cls)
    return [
        (field.name, glue_type_for(hints[field.name]))
        for field in fields(fact_cls)
    ]


def test_structured_glue_columns_match_fact_contracts():
    parsed = parse_structured_columns()
    expected = {
        dataset: expected_columns(fact_cls)
        for dataset, fact_cls in STRUCTURED_FACTS.items()
    }
    assert parsed == expected


def test_structured_field_type_map_is_complete():
    parsed = parse_structured_columns()
    for dataset, fact_cls in STRUCTURED_FACTS.items():
        hints = get_type_hints(fact_cls)
        glue_types = dict(parsed[dataset])
        assert set(glue_types) == set(hints)
        for name, annotation in hints.items():
            assert glue_types[name] == glue_type_for(annotation)


def test_numeric_and_time_types_are_deliberate():
    parsed = parse_structured_columns()
    encounter = dict(parsed["encounter"])
    risk = dict(parsed["risk"])
    hazard_version = dict(parsed["hazard_version"])

    assert encounter["fact_schema_version"] == "string"
    assert encounter["event_time_utc"] == "string"
    assert encounter["event_time_epoch"] == "bigint"
    assert encounter["event_year"] == "string"
    assert encounter["event_month"] == "string"
    assert encounter["event_day"] == "string"
    assert encounter["exact_intersection_confirmed"] == "boolean"

    assert risk["risk_score"] == "double"
    assert get_type_hints(RiskFact)["risk_score"] == int | float
    assert (
        get_type_hints(HazardVersionFact)["minimum_lower_altitude_ft"]
        == int | float | None
    )

    assert hazard_version["geometry_point_count"] == "bigint"
    assert hazard_version["minimum_lower_altitude_ft"] == "double"
    assert hazard_version["maximum_upper_altitude_ft"] == "double"


def test_structured_tables_have_no_nested_contract_fields():
    for fact_cls in STRUCTURED_FACTS.values():
        hints = get_type_hints(fact_cls)
        for name, annotation in hints.items():
            assert not _is_mapping(annotation), name
            origin = get_origin(annotation)
            assert origin not in {list, dict, tuple, set}, name


def test_cataloged_datasets_match_contract_enum():
    assert {member.value for member in Dataset} == {
        "encounter",
        "risk",
        "hazard_version",
        "hazard_geometry",
    }
    locals_text = LOCALS_TF.read_text(encoding="utf-8")
    for dataset in ("encounter", "risk", "hazard_version"):
        assert f"{dataset} =" in locals_text or f"{dataset} =" in locals_text
    glue = GLUE_TF.read_text(encoding="utf-8")
    assert 'name          = "hazard_geometry"' in glue


def test_projection_layout_matches_event_time_partition_convention():
    from datetime import datetime, timezone

    year, month, day = partition_date_utc(
        datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    )
    assert (year, month, day) == ("2023", "11", "14")
    assert len(year) == 4
    assert len(month) == 2
    assert len(day) == 2

    facts_locals = FACTS_LOCALS_TF.read_text(encoding="utf-8")
    glue = GLUE_TF.read_text(encoding="utf-8")
    locals_text = LOCALS_TF.read_text(encoding="utf-8")
    assert (
        "dataset=!{partitionKeyFromLambda:dataset}/"
        "year=!{partitionKeyFromLambda:year}/"
        "month=!{partitionKeyFromLambda:month}/"
        "day=!{partitionKeyFromLambda:day}/"
        in facts_locals
    )
    assert "year=$${year}/month=$${month}/day=$${day}" in glue
    assert re.search(r'"projection\.year\.digits"\s*=\s*"4"', locals_text)
    assert re.search(r'"projection\.month\.digits"\s*=\s*"2"', locals_text)
    assert re.search(r'"projection\.day\.digits"\s*=\s*"2"', locals_text)


def test_hazard_geometry_is_intentional_raw_line_exception():
    hints = get_type_hints(HazardGeometryFact)
    assert "geometry" in hints
    assert _is_mapping(hints["geometry"])
    assert "coordinates_axis" in hints
    assert "json_record" not in hints

    glue = GLUE_TF.read_text(encoding="utf-8")
    geometry = glue.split('resource "aws_glue_catalog_table" "hazard_geometry"')[1]
    assert 'name = "json_record"' in geometry
    assert geometry.count("columns {") == 1
    assert 'name = "geometry"' not in geometry
    assert "coordinates" not in geometry
    assert "org.apache.hadoop.hive.serde2.RegexSerDe" in geometry
    assert {field.name for field in fields(HazardGeometryFact)} > {
        "record_id",
        "hazard_version_key",
        "geometry",
    }

"""Phase 3A.1 provider-neutral historical tool-schema tests."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from wilvor_ai import (
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    ToolInputField,
    ToolInputValueType,
)
from wilvor_ai.historical_analytics import (
    HISTORICAL_ANALYTICS_TOOLS,
    HistoricalAnalyticsAdapter,
    HistoricalAnalyticsToolSpec,
    historical_analytics_tool_schemas,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.tool_schema import (
    FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES,
    ToolSchema,
    build_tool_schema,
    build_tool_schemas,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SOURCE = (
    REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "tool_schema.py"
)


def test_historical_schemas_are_exactly_the_four_catalog_tools():
    schemas = historical_analytics_tool_schemas()
    assert [item.name for item in schemas] == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    ]
    assert len(schemas) == 4
    assert "summarize_historical_risks_by_level" not in {
        item.name for item in schemas
    }
    live_ops_names = {item.name for item in LIVE_OPS_TOOLS}
    assert live_ops_names.isdisjoint(item.name for item in schemas)
    for spec, schema in zip(HISTORICAL_ANALYTICS_TOOLS, schemas, strict=True):
        assert schema.name == spec.name
        assert schema.description == spec.description
        assert schema.description.strip()
        assert schema.input_fields == spec.input_fields
        assert schema.to_dict() == ToolSchema.from_dict(schema.to_dict()).to_dict()
        assert ToolSchema.from_dict(schema.to_dict()) == schema


def test_historical_field_types_and_required_flags_match_catalog():
    schemas = {item.name: item for item in historical_analytics_tool_schemas()}
    encounters = schemas["summarize_historical_encounters"]
    assert [(item.name, item.required, item.value_type) for item in encounters.input_fields] == [
        ("start_utc", True, ToolInputValueType.STRING),
        ("end_utc", True, ToolInputValueType.STRING),
        ("aircraft_id", False, ToolInputValueType.STRING),
        ("hazard_id", False, ToolInputValueType.STRING),
        ("hazard_type", False, ToolInputValueType.STRING),
    ]
    limit = schemas["list_historical_encounters"].input_fields[-1]
    assert limit.name == "limit"
    assert limit.required is False
    assert limit.value_type is ToolInputValueType.INTEGER
    assert "specialist runtime" in (limit.description or "").lower()
    for schema in schemas.values():
        names = {item.name.casefold() for item in schema.input_fields}
        assert names.isdisjoint(FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES)


def test_schema_builder_fails_closed_on_trusted_catalog_field():
    spec = HistoricalAnalyticsToolSpec(
        name="summarize_historical_encounters",
        description="fake spec with trusted field",
        authority_mode=AgentAuthorityMode.READ_ONLY_ADVISORY,
        capabilities=(AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,),
        input_fields=(
            ToolInputField(name="start_utc", required=True),
            ToolInputField(name="as_of_utc", required=False),
        ),
    )
    with pytest.raises(ContractValidationError) as exc_info:
        build_tool_schema(spec)
    assert "forbidden_tool_schema_field" in exc_info.value.errors


def test_schema_builder_uses_catalog_fields_not_unbound_signatures():
    @dataclass(frozen=True)
    class ExtraSpec:
        name: str
        description: str
        input_fields: tuple[ToolInputField, ...]

        def handler(self, start_utc: str, operations: object, sql: str) -> None:
            return None

    spec = ExtraSpec(
        name="summarize_historical_encounters",
        description="catalog wins over signature",
        input_fields=(
            ToolInputField(name="start_utc", required=True),
            ToolInputField(name="end_utc", required=True),
        ),
    )
    schema = build_tool_schema(spec)
    assert [item.name for item in schema.input_fields] == ["start_utc", "end_utc"]
    assert "operations" not in {item.name for item in schema.input_fields}
    assert "sql" not in {item.name for item in schema.input_fields}
    parameters = inspect.signature(spec.handler).parameters
    assert "operations" in parameters
    source = SCHEMA_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "inspect" not in imported
    assert "inspect.signature" not in source


def test_bound_adapter_signatures_remain_catalog_coherent():
    from tests.contracts.test_historical_analytics_tools import (
        _adapter,
        _succeeded_encounters,
    )

    adapter, _ = _adapter(_succeeded_encounters())
    assert isinstance(adapter, HistoricalAnalyticsAdapter)
    for spec, schema in zip(
        HISTORICAL_ANALYTICS_TOOLS,
        build_tool_schemas(HISTORICAL_ANALYTICS_TOOLS),
        strict=True,
    ):
        handler = adapter.get_handler(spec.name)
        parameter_names = [
            name
            for name in inspect.signature(handler).parameters
            if name != "self"
        ]
        assert [item.name for item in schema.input_fields] == parameter_names

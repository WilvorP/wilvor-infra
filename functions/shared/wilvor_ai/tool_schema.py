"""Provider-neutral tool schemas derived from trusted catalogs.

This module does not generate OpenAI JSON Schema, Anthropic tool objects,
or Bedrock ToolSpecification values. Field inclusion comes only from
catalog ``input_fields``. It does not inspect function signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from wilvor_ai.contracts import (
    ContractValidationError,
    JsonValue,
    ToolInputField,
    _required,
    _sequence,
    _validate_text,
)


FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES = frozenset(
    {
        "call",
        "operations",
        "as_of_utc",
        "tool_call_id",
        "correlation_id",
        "dataset",
        "internalqueryid",
        "query_id",
        "sql",
        "query_string",
        "workgroup",
        "database",
        "output_location",
        "bucket",
        "s3_bucket",
        "athena",
        "temporal_scope",
        "freshness",
    }
)


class ToolCatalogEntry(Protocol):
    """Minimal catalog surface required to build a ToolSchema."""

    name: str
    description: str
    input_fields: tuple[ToolInputField, ...]


@dataclass(frozen=True)
class ToolSchema:
    """Provider-neutral tool schema. Not JSON Schema."""

    name: str
    description: str
    input_fields: tuple[ToolInputField, ...]

    def __post_init__(self) -> None:
        errors = _validate_text(self.name, "name")
        errors.extend(_validate_text(self.description, "description"))
        if not isinstance(self.input_fields, tuple) or any(
            not isinstance(item, ToolInputField) for item in self.input_fields
        ):
            errors.append("invalid_input_fields")
        else:
            errors.extend(_forbidden_field_errors(self.input_fields))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "description": self.description,
            "input_fields": [item.to_dict() for item in self.input_fields],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolSchema":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_tool_schema")
        fields = _sequence(_required(data, "input_fields"), "input_fields")
        return cls(
            name=_required(data, "name"),
            description=_required(data, "description"),
            input_fields=tuple(ToolInputField.from_dict(item) for item in fields),
        )


def _forbidden_field_errors(fields: Iterable[ToolInputField]) -> list[str]:
    errors: list[str] = []
    for item in fields:
        if item.name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            errors.append("forbidden_tool_schema_field")
    return errors


def build_tool_schema(spec: ToolCatalogEntry) -> ToolSchema:
    """Build one schema from a catalog entry's ``input_fields`` only."""

    if not hasattr(spec, "input_fields"):
        raise ContractValidationError("invalid_tool_catalog_entry")
    return ToolSchema(
        name=spec.name,
        description=spec.description,
        input_fields=tuple(spec.input_fields),
    )


def build_tool_schemas(catalog: Iterable[ToolCatalogEntry]) -> tuple[ToolSchema, ...]:
    """Build schemas in catalog order. Fails closed on trusted field names."""

    return tuple(build_tool_schema(spec) for spec in catalog)


__all__ = [
    "FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES",
    "ToolCatalogEntry",
    "ToolSchema",
    "build_tool_schema",
    "build_tool_schemas",
]

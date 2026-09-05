import pytest

from evidence import EvidenceCatalog
from schemas import (
    ValidationError,
    structured_output_schema,
    validate_chat_request,
    validate_model_output,
)


def test_evidence_ids_are_stable_and_unknown_ids_drop():
    catalog = EvidenceCatalog()
    first = catalog.add(
        source_type="RiskResult",
        source_id="risk#123",
        field="risk_level",
        value="HIGH",
    )
    second = catalog.add(
        source_type="RiskResult",
        source_id="risk#123",
        field="risk_level",
        value="HIGH",
    )

    assert first == second == (
        "riskresult.risk-123.risk_level"
    )
    assert catalog.validate_references(
        [
            {"evidenceId": first, "label": "invented"},
            {"evidenceId": "fabricated", "label": "bad"},
        ]
    ) == [
        {
            "evidenceId": first,
            "label": (
                "RiskResult risk#123: risk_level"
            ),
        }
    ]


def test_safety_fields_and_required_warnings_survive():
    catalog = [
        {
            "evidenceId": "risk.r1.level",
            "label": "Risk level",
        }
    ]
    result = validate_model_output(
        {
            "answer": "Current data indicates elevated risk.",
            "evidence": [
                {
                    "evidenceId": "risk.r1.level",
                    "label": "model label",
                },
                {
                    "evidenceId": "unknown",
                    "label": "fabricated",
                },
            ],
            "confidence": "HIGH",
            "limitations": [],
            "dataFreshnessWarnings": [],
        },
        evidence_catalog=catalog,
        required_limitations=["Fuel state unavailable."],
        required_freshness_warnings=[
            "OPENSKY data freshness is STALE."
        ],
    )

    assert "qualified human review" in result[
        "answer"
    ]
    assert result["confidence"] == "LOW"
    assert result["evidence"] == [
        {
            "evidenceId": "risk.r1.level",
            "label": "Risk level",
        }
    ]
    assert result["limitations"] == [
        "Fuel state unavailable."
    ]


def test_chat_bounds_are_application_enforced():
    with pytest.raises(ValidationError):
        validate_chat_request(
            {"message": "x" * 11},
            max_message_chars=10,
            max_history_items=2,
            max_history_item_chars=10,
        )


def test_bedrock_schema_uses_supported_subset():
    schema_text = str(structured_output_schema())
    for unsupported in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    ):
        assert unsupported not in schema_text
    assert schema_text.count(
        "'additionalProperties': False"
    ) >= 2

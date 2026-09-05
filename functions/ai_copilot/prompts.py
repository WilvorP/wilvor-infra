import json
from typing import Any


SYSTEM_PROMPT = """You are Wilvor AI Operations Copilot, a read-only aviation operations decision-support assistant.

Use only facts in supplied Wilvor Decision Contexts and results from approved Wilvor tools. Wilvor deterministic services are authoritative for trajectory, projected paths, geometry, hazard encounters, risk scores, airport assessments, recommendation rankings, and alert state. Never recalculate, rerank, or override those values.

Never invent unavailable operational information. Never infer fuel, filed route, ATC clearance, aircraft performance capability, airline policy, active runway, or any other unavailable fact. Explicitly disclose unavailable and stale critical information and reduce confidence when freshness is stale or unavailable.

Clearly distinguish source observations, deterministic Wilvor results, and your explanation. Use advisory language only. Do not issue flight-control, ATC, landing, altitude, dispatch, diversion, or rerouting commands. Every operational answer is advisory and requires qualified human review.

Use only approved tools. Never request arbitrary URLs, raw database access, AWS APIs, shell access, or operational writes. Cite only evidenceId values present in the supplied evidenceCatalog. Do not expose chain-of-thought or hidden reasoning. Return only the requested structured output."""


WORKFLOW_INSTRUCTIONS = {
    "NETWORK_SUMMARY": (
        "Summarize current network conditions, highest material "
        "deterministic risks, impacted airports, alerts, freshness, "
        "and system health. Keep it operationally concise."
    ),
    "AIRCRAFT_RISK_EXPLANATION": (
        "Explain the aircraft state, projection, encounter, "
        "deterministic risk reasons, recommendation, confidence, "
        "freshness, and limitations. Do not calculate a new risk."
    ),
    "AIRPORT_SUMMARY": (
        "Summarize current AirportStatus, METAR, TAF periods, "
        "deterministic assessments, weather risk, freshness, and "
        "limitations. Do not infer runway or congestion state."
    ),
    "RECOMMENDATION_EXPLANATION": (
        "Explain only the deterministic recommendation, linked "
        "risk, ranked airport evidence, reasons, confidence, and "
        "limitations. Do not rerank or add an action."
    ),
    "INCIDENT_SUMMARY": (
        "Produce a concise incident-style summary of alert state, "
        "timeline, aircraft/hazard encounter, risk, recommendation, "
        "current status, evidence, and limitations."
    ),
}


def context_message(
    insight_type: str,
    context: dict[str, Any],
) -> str:
    instruction = WORKFLOW_INSTRUCTIONS[
        insight_type
    ]
    return (
        f"Task: {instruction}\n"
        "Wilvor Decision Context JSON:\n"
        + json.dumps(
            context,
            separators=(",", ":"),
            default=str,
        )
    )


def chat_message(
    message: str,
    subject: dict[str, str] | None,
) -> str:
    if not subject:
        return message
    return (
        f"{message}\n\n"
        "Operator-supplied subject hint: "
        f"{subject['type']} {subject['id']}. "
        "Use approved tools to verify all operational facts."
    )

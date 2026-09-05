import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from context import freshness_warnings
from evidence import EvidenceCatalog


@dataclass
class ToolExecution:
    tool_use_id: str
    name: str
    status: str
    duration_ms: int
    result: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "durationMs": self.duration_ms,
        }


class ToolRegistry:
    def __init__(self, client, builders) -> None:
        self.client = client
        self.builders = builders
        self._tools: dict[
            str,
            tuple[
                str,
                dict[str, Any],
                Callable[[dict[str, Any]], dict[str, Any]],
            ],
        ] = {
            "get_network_overview": (
                "Get compact current Wilvor network context.",
                self._empty_schema(),
                lambda _: builders.build_network_context(),
            ),
            "get_data_freshness": (
                "Get OpenSky, SIGMET, METAR, and TAF freshness.",
                self._empty_schema(),
                lambda _: self._evidenced_payload(
                    "FreshnessSnapshot",
                    "current",
                    client.freshness(),
                ),
            ),
            "get_system_health": (
                "Get current Wilvor platform and pipeline health.",
                self._empty_schema(),
                lambda _: self._system_health(),
            ),
            "get_aircraft_context": (
                "Get grounded decision context for one aircraft.",
                self._id_schema("aircraft_id"),
                lambda value: builders.build_aircraft_context(
                    value["aircraft_id"]
                ),
            ),
            "get_airport_context": (
                "Get grounded operational context for one airport.",
                self._id_schema("airport_id"),
                lambda value: builders.build_airport_context(
                    value["airport_id"]
                ),
            ),
            "get_active_encounters": (
                "Get a bounded list of active encounters and risks.",
                self._limit_schema(),
                lambda value: self._active_with_freshness(
                    "ActiveEncounters",
                    lambda: client.active_encounters(
                        self._validated_limit(value)
                    ),
                ),
            ),
            "get_active_recommendations": (
                "Get a bounded list of active recommendations.",
                self._limit_schema(),
                lambda value: self._active_with_freshness(
                    "ActiveRecommendations",
                    lambda: client.active_recommendations(
                        self._validated_limit(value)
                    ),
                ),
            ),
            "get_active_alerts": (
                "Get a bounded list of active alerts.",
                self._limit_schema(),
                lambda value: self._active_with_freshness(
                    "ActiveAlerts",
                    lambda: client.active_alerts(
                        self._validated_limit(value)
                    ),
                ),
            ),
            "get_recommendation_context": (
                "Get deterministic recommendation and ranked evidence.",
                self._id_schema("recommendation_id"),
                lambda value: (
                    builders.build_recommendation_context(
                        value["recommendation_id"]
                    )
                ),
            ),
            "get_alert_context": (
                "Get deterministic alert incident context.",
                self._id_schema("alert_id"),
                lambda value: builders.build_alert_context(
                    value["alert_id"]
                ),
            ),
            "compare_diversion_airports": (
                "Get already-ranked deterministic airport evidence; do not rerank.",
                self._id_schema("recommendation_id"),
                lambda value: self._comparison(
                    value["recommendation_id"]
                ),
            ),
        }

    @staticmethod
    def _empty_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    @staticmethod
    def _id_schema(name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                name: {"type": "string"}
            },
            "required": [name],
            "additionalProperties": False,
        }

    @staticmethod
    def _limit_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"}
            },
            "additionalProperties": False,
        }

    @staticmethod
    def _validated_limit(value: dict[str, Any]) -> int:
        limit = value.get("limit", 25)
        if isinstance(limit, bool) or not isinstance(
            limit,
            int,
        ):
            raise ValueError("limit must be an integer")
        if limit < 1 or limit > 50:
            raise ValueError(
                "limit must be between 1 and 50"
            )
        return limit

    @staticmethod
    def _validate_input(
        schema: dict[str, Any],
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(
                "Tool input must be an object"
            )
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in properties for key in value):
            raise ValueError(
                "Tool input contains unknown fields"
            )
        for name in required:
            if name not in value:
                raise ValueError(
                    f"Tool input missing {name}"
                )
        for name, item in value.items():
            expected = properties[name].get("type")
            if expected == "string":
                if (
                    not isinstance(item, str)
                    or not item.strip()
                    or len(item.strip()) > 256
                    or any(
                        ord(char) < 32
                        for char in item
                    )
                ):
                    raise ValueError(
                        f"Tool input {name} is invalid"
                    )
                value[name] = item.strip()
            elif expected == "integer" and (
                isinstance(item, bool)
                or not isinstance(item, int)
            ):
                raise ValueError(
                    f"Tool input {name} must be an integer"
                )
        return value

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "toolSpec": {
                    "name": name,
                    "description": description,
                    "inputSchema": {"json": schema},
                }
            }
            for name, (
                description,
                schema,
                _,
            ) in self._tools.items()
        ]

    def execute(
        self,
        *,
        tool_use_id: str,
        name: str,
        value: Any,
    ) -> ToolExecution:
        started = time.perf_counter()
        entry = self._tools.get(name)
        if entry is None:
            return ToolExecution(
                tool_use_id=tool_use_id,
                name=name,
                status="FAILURE",
                duration_ms=0,
                result={
                    "error": "Unknown or unapproved tool"
                },
            )
        _, schema, executor = entry
        try:
            clean = self._validate_input(
                schema,
                dict(value)
                if isinstance(value, dict)
                else value,
            )
            result = executor(clean)
            if not isinstance(result, dict):
                raise ValueError(
                    "Tool result must be an object"
                )
            status = "SUCCESS"
        except Exception:
            result = {
                "error": "Approved tool execution failed"
            }
            status = "FAILURE"
        duration_ms = int(
            (time.perf_counter() - started) * 1000
        )
        return ToolExecution(
            tool_use_id=tool_use_id,
            name=name,
            status=status,
            duration_ms=duration_ms,
            result=result,
        )

    def _comparison(
        self,
        recommendation_id: str,
    ) -> dict[str, Any]:
        context = (
            self.builders.build_recommendation_context(
                recommendation_id
            )
        )
        recommendation = (
            context.get("recommendation") or {}
        )
        return {
            "contextVersion": context.get(
                "contextVersion"
            ),
            "subject": context.get("subject"),
            "primaryActionType": recommendation.get(
                "primary_action_type"
            ),
            "preferredAirportId": recommendation.get(
                "preferred_airport_id"
            ),
            "candidateAirportSummaries": (
                recommendation.get(
                    "candidate_airport_summaries"
                )
                or []
            ),
            "airportAssessments": context.get(
                "airportAssessments",
                [],
            ),
            "limitations": context.get(
                "limitations",
                [],
            ),
            "dataFreshnessWarnings": context.get(
                "dataFreshnessWarnings",
                [],
            ),
            "evidenceCatalog": context.get(
                "evidenceCatalog",
                [],
            ),
        }

    @staticmethod
    def _evidenced_payload(
        source_type: str,
        source_id: str,
        payload: dict[str, Any],
        *,
        freshness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        catalog = EvidenceCatalog()
        catalog.add_tree(
            source_type=source_type,
            source_id=source_id,
            value=payload,
            observed_at=payload.get("generatedAt"),
        )
        if freshness:
            for source, state in (
                freshness.get("sources") or {}
            ).items():
                if isinstance(state, dict):
                    catalog.add_tree(
                        source_type="Freshness",
                        source_id=source,
                        value=state,
                        observed_at=state.get("latestAt"),
                    )
        return {
            "data": payload,
            "freshness": freshness,
            "limitations": [],
            "dataFreshnessWarnings": (
                freshness_warnings(
                    freshness or payload
                )
            ),
            "evidenceCatalog": catalog.items(),
        }

    def _active_with_freshness(
        self,
        source_type: str,
        fetcher: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            payload_future = executor.submit(fetcher)
            freshness_future = executor.submit(
                self.client.freshness
            )
            payload = payload_future.result()
            freshness = freshness_future.result()
        return self._evidenced_payload(
            source_type,
            "current",
            payload,
            freshness=freshness,
        )

    def _system_health(self) -> dict[str, Any]:
        payload = self.client.system_health()
        freshness = payload.get("dataFreshness")
        return self._evidenced_payload(
            "SystemHealth",
            "current",
            payload,
            freshness=(
                freshness
                if isinstance(freshness, dict)
                else None
            ),
        )

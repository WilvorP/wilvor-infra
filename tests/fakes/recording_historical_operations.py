"""Recording HistoricalAnalyticsOperations fake. No Athena, coverage, or AWS."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from wilvor_historical.query_contracts import HistoricalQueryResponse


class RecordingHistoricalOperations:
    """Records method/request/as_of and returns canned query responses."""

    def __init__(
        self,
        responses: Mapping[str, HistoricalQueryResponse | Sequence[HistoricalQueryResponse]],
    ) -> None:
        self._queues: dict[str, list[HistoricalQueryResponse]] = {}
        for name, value in responses.items():
            if isinstance(value, HistoricalQueryResponse):
                self._queues[name] = [value]
            else:
                self._queues[name] = list(value)
        self.calls: list[dict[str, object]] = []

    def _record(self, method: str, request: object, as_of_utc: str) -> HistoricalQueryResponse:
        self.calls.append(
            {
                "method": method,
                "request": request,
                "as_of_utc": as_of_utc,
                "order": len(self.calls) + 1,
            }
        )
        queue = self._queues.get(method)
        if not queue:
            raise AssertionError(f"no canned response for {method}")
        return queue.pop(0)

    def summarize_historical_encounters(self, request, *, as_of_utc):
        return self._record("summarize_historical_encounters", request, as_of_utc)

    def summarize_historical_risks(self, request, *, as_of_utc):
        return self._record("summarize_historical_risks", request, as_of_utc)

    def summarize_historical_hazard_versions(self, request, *, as_of_utc):
        return self._record(
            "summarize_historical_hazard_versions",
            request,
            as_of_utc,
        )

    def list_historical_encounters(self, request, *, as_of_utc):
        return self._record("list_historical_encounters", request, as_of_utc)

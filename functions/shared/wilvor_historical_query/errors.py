"""Deterministic Athena executor errors. These are not coverage errors."""

from __future__ import annotations

from enum import Enum


class AthenaExecutorErrorCode(str, Enum):
    START_FAILED = "START_FAILED"
    API_ERROR = "API_ERROR"
    QUERY_FAILED = "QUERY_FAILED"
    QUERY_CANCELED = "QUERY_CANCELED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    UNKNOWN_QUERY_STATE = "UNKNOWN_QUERY_STATE"
    WORKGROUP_MISMATCH = "WORKGROUP_MISMATCH"
    DATABASE_MISMATCH = "DATABASE_MISMATCH"
    OUTPUT_LOCATION_INVALID = "OUTPUT_LOCATION_INVALID"
    RESULT_SCHEMA_MISMATCH = "RESULT_SCHEMA_MISMATCH"
    RESULT_LIMIT_EXCEEDED = "RESULT_LIMIT_EXCEEDED"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    INVALID_REQUEST = "INVALID_REQUEST"


class AthenaExecutorError(Exception):
    """Fail-closed executor outcome. Does not carry rendered SQL."""

    def __init__(
        self,
        code: AthenaExecutorErrorCode,
        message: str,
        *,
        query_id: str | None = None,
        query_execution_id: str | None = None,
        athena_state: str | None = None,
        athena_reason: str | None = None,
        stop_failed: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.query_id = query_id
        self.query_execution_id = query_execution_id
        self.athena_state = athena_state
        self.athena_reason = athena_reason
        self.stop_failed = stop_failed

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": str(self),
            "query_id": self.query_id,
            "query_execution_id": self.query_execution_id,
            "athena_state": self.athena_state,
            "athena_reason": self.athena_reason,
            "stop_failed": self.stop_failed,
        }

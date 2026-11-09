"""Authoritative historical coverage metadata loader.

Loads the complete current control-record set from the canonical
historical bucket. Metadata keys are write/created-time partitioned, so
V1 lists every authoritative prefix and filters in memory. This module
does not create AWS clients, query Athena, or evaluate completeness.

Phase 2A.1 writers persist ``*.json`` control records only. They do not
create S3 folder-marker objects, so a listed key ending in ``/`` is
malformed coverage metadata.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from wilvor_historical.coverage import (
    collection_activation_from_dict,
    collection_deactivation_from_dict,
    collection_epoch_from_dict,
    collection_gap_from_dict,
    collection_gap_resolution_from_dict,
    coverage_interval_from_dict,
    unbound_incident_from_dict,
)
from wilvor_historical.coverage_contracts import (
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    CollectionEpochRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    CoverageIntervalRecord,
    UnboundCollectionIncident,
)
from wilvor_historical.query_contracts import COVERAGE_STORE_METADATA_PREFIXES


_BUCKET_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789.-"
)


class CoverageStoreErrorCode(str, Enum):
    COVERAGE_STORE_UNAVAILABLE = "COVERAGE_STORE_UNAVAILABLE"
    COVERAGE_METADATA_MALFORMED = "COVERAGE_METADATA_MALFORMED"
    COVERAGE_PAGINATION_MALFORMED = "COVERAGE_PAGINATION_MALFORMED"
    INVALID_REQUEST = "INVALID_REQUEST"


class CoverageStoreError(Exception):
    """Fail-closed metadata-store outcome. Does not carry object bodies."""

    def __init__(
        self,
        code: CoverageStoreErrorCode,
        message: str,
        *,
        object_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.object_key = object_key


class S3Client(Protocol):
    """Minimal S3 surface. Compatible with a later injected SDK client."""

    def list_objects_v2(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CoverageStoreConfig:
    bucket_name: str
    metadata_prefixes: tuple[str, ...] = COVERAGE_STORE_METADATA_PREFIXES

    def __post_init__(self) -> None:
        bucket = self.bucket_name.strip() if isinstance(self.bucket_name, str) else ""
        if (
            not bucket
            or any(character not in _BUCKET_CHARACTERS for character in bucket)
            or bucket.startswith(".")
            or bucket.endswith(".")
            or ".." in bucket
        ):
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "invalid coverage store bucket_name",
            )
        prefixes = self.metadata_prefixes
        if not isinstance(prefixes, tuple) or not prefixes:
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "invalid coverage store metadata_prefixes",
            )
        normalized: list[str] = []
        for prefix in prefixes:
            if not isinstance(prefix, str) or not prefix.startswith("metadata/"):
                raise CoverageStoreError(
                    CoverageStoreErrorCode.INVALID_REQUEST,
                    "invalid coverage store metadata_prefixes",
                )
            text = prefix if prefix.endswith("/") else prefix + "/"
            if text not in COVERAGE_STORE_METADATA_PREFIXES:
                raise CoverageStoreError(
                    CoverageStoreErrorCode.INVALID_REQUEST,
                    "unknown coverage store metadata prefix",
                )
            normalized.append(text)
        if tuple(normalized) != COVERAGE_STORE_METADATA_PREFIXES:
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "coverage store must load the complete authoritative prefix set",
            )
        object.__setattr__(self, "bucket_name", bucket)
        object.__setattr__(self, "metadata_prefixes", tuple(normalized))


@dataclass(frozen=True)
class CoverageMetadataSnapshot:
    epochs: tuple[CollectionEpochRecord, ...]
    activations: tuple[CollectionActivationRecord, ...]
    deactivations: tuple[CollectionDeactivationRecord, ...]
    coverage_intervals: tuple[CoverageIntervalRecord, ...]
    gaps: tuple[CollectionGapRecord, ...]
    resolutions: tuple[CollectionGapResolutionRecord, ...]
    incidents: tuple[UnboundCollectionIncident, ...]
    object_keys: tuple[str, ...]


_PARSERS: Mapping[str, Callable[[dict[str, Any]], Any]] = {
    "metadata/epoch/": collection_epoch_from_dict,
    "metadata/activation/": collection_activation_from_dict,
    "metadata/deactivation/": collection_deactivation_from_dict,
    "metadata/coverage/": coverage_interval_from_dict,
    "metadata/gaps/": collection_gap_from_dict,
    "metadata/resolutions/": collection_gap_resolution_from_dict,
    "metadata/incidents/": unbound_incident_from_dict,
}


class CoverageStore:
    """Load every authoritative control object through an injected S3 client."""

    def __init__(self, *, s3_client: S3Client, config: CoverageStoreConfig) -> None:
        if s3_client is None:
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "missing s3_client",
            )
        if not isinstance(config, CoverageStoreConfig):
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "invalid coverage store config",
            )
        self._client = s3_client
        self._config = config

    def load(self) -> CoverageMetadataSnapshot:
        listed: list[tuple[str, str]] = []
        seen_keys: set[str] = set()
        for prefix in self._config.metadata_prefixes:
            for key in self._list_prefix(prefix):
                if key in seen_keys:
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                        "duplicate object key in coverage metadata listing",
                        object_key=key,
                    )
                seen_keys.add(key)
                listed.append((prefix, key))
        listed.sort(key=lambda item: item[1])
        epochs: list[CollectionEpochRecord] = []
        activations: list[CollectionActivationRecord] = []
        deactivations: list[CollectionDeactivationRecord] = []
        coverage: list[CoverageIntervalRecord] = []
        gaps: list[CollectionGapRecord] = []
        resolutions: list[CollectionGapResolutionRecord] = []
        incidents: list[UnboundCollectionIncident] = []
        keys: list[str] = []
        buckets = {
            "metadata/epoch/": epochs,
            "metadata/activation/": activations,
            "metadata/deactivation/": deactivations,
            "metadata/coverage/": coverage,
            "metadata/gaps/": gaps,
            "metadata/resolutions/": resolutions,
            "metadata/incidents/": incidents,
        }
        for prefix, key in listed:
            record = self._read_record(prefix, key)
            buckets[prefix].append(record)
            keys.append(key)
        return CoverageMetadataSnapshot(
            epochs=tuple(epochs),
            activations=tuple(activations),
            deactivations=tuple(deactivations),
            coverage_intervals=tuple(coverage),
            gaps=tuple(gaps),
            resolutions=tuple(resolutions),
            incidents=tuple(incidents),
            object_keys=tuple(keys),
        )

    def _list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        seen_keys: set[str] = set()
        seen_tokens: set[str] = set()
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._config.bucket_name,
                "Prefix": prefix,
            }
            if token is not None:
                kwargs["ContinuationToken"] = token
            try:
                response = self._client.list_objects_v2(**kwargs)
            except CoverageStoreError:
                raise
            except Exception as exc:
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE,
                    "ListObjectsV2 failed",
                ) from exc
            if not isinstance(response, Mapping):
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                    "malformed ListObjectsV2 response",
                )
            contents = response.get("Contents")
            if contents is None:
                contents = []
            if not isinstance(contents, list):
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                    "malformed ListObjectsV2 Contents",
                )
            for item in contents:
                if not isinstance(item, Mapping):
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                        "malformed ListObjectsV2 object",
                    )
                key = item.get("Key")
                if not isinstance(key, str) or not key.strip():
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                        "malformed ListObjectsV2 object key",
                    )
                if not key.startswith(prefix):
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                        "listed key is outside the requested prefix",
                        object_key=key,
                    )
                # Phase 2A.1 writers persist *.json control records only.
                # They do not create S3 folder-marker objects under metadata/.
                if key.endswith("/"):
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                        "unexpected trailing-slash coverage metadata key",
                        object_key=key,
                    )
                if key in seen_keys:
                    raise CoverageStoreError(
                        CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                        "duplicate object key in coverage metadata listing",
                        object_key=key,
                    )
                seen_keys.add(key)
                keys.append(key)
            truncated = response.get("IsTruncated")
            if truncated in (None, False):
                return keys
            if truncated is not True:
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                    "malformed ListObjectsV2 IsTruncated",
                )
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token.strip():
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                    "truncated ListObjectsV2 is missing NextContinuationToken",
                )
            if next_token in seen_tokens:
                raise CoverageStoreError(
                    CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED,
                    "ListObjectsV2 continuation token cycle",
                )
            seen_tokens.add(next_token)
            token = next_token

    def _read_record(self, prefix: str, key: str) -> Any:
        try:
            response = self._client.get_object(
                Bucket=self._config.bucket_name,
                Key=key,
            )
        except CoverageStoreError:
            raise
        except Exception as exc:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE,
                "GetObject failed",
                object_key=key,
            ) from exc
        if not isinstance(response, Mapping):
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "malformed GetObject response",
                object_key=key,
            )
        body = response.get("Body")
        try:
            raw = body.read()
        except Exception as exc:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE,
                "GetObject body read failed",
                object_key=key,
            ) from exc
        if not isinstance(raw, (bytes, bytearray)):
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "GetObject body is not bytes",
                object_key=key,
            )
        if not raw:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "coverage metadata object is empty",
                object_key=key,
            )
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "coverage metadata is not UTF-8",
                object_key=key,
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "coverage metadata is not JSON",
                object_key=key,
            ) from exc
        if not isinstance(payload, dict):
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "coverage metadata JSON must be an object",
                object_key=key,
            )
        parser = _PARSERS[prefix]
        try:
            return parser(payload)
        except CoverageStoreError:
            raise
        except Exception as exc:
            raise CoverageStoreError(
                CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED,
                "coverage metadata record is malformed",
                object_key=key,
            ) from exc

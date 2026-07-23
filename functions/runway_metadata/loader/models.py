from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

RUNWAY_SCHEMA_VERSION = "internal.runway.v1"
RUNWAY_END_SCHEMA_VERSION = "internal.runway-end.v1"


@dataclass(frozen=True)
class AirportReference:
    faa_id: str
    icao_id: str
    site_no: str | None = None
    airport_name: str | None = None
    airport_status: str | None = None


@dataclass(frozen=True)
class RunwayEnd:
    runway_end_id: str
    true_heading_deg: float | None
    latitude: float | None
    longitude: float | None
    elevation_ft: float | None
    landing_distance_available_ft: int | None
    takeoff_run_available_ft: int | None
    takeoff_distance_available_ft: int | None = None
    accelerate_stop_distance_available_ft: int | None = None
    schema_version: str = RUNWAY_END_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class PhysicalRunway:
    site_no: str | None
    faa_id: str
    physical_runway_id: str
    length_ft: int
    width_ft: int | None
    surface_type: str | None
    surface_condition: str | None
    lighting_code: str | None


@dataclass(frozen=True)
class NormalizedRunway:
    airport_id: str
    faa_id: str
    physical_runway_id: str
    length_ft: int
    width_ft: int | None
    surface_type: str | None
    surface_condition: str | None
    lighting_code: str | None
    end_1: RunwayEnd
    end_2: RunwayEnd | None
    source: str = "FAA_NASR"
    schema_version: str = RUNWAY_SCHEMA_VERSION
    source_record_hash: str | None = None

    @property
    def record_id(self) -> str:
        canonical = self.physical_runway_id.replace("/", "-").replace(" ", "")
        return f"RUNWAY#{canonical}"

    def material_dict(self) -> dict[str, Any]:
        """Fields that define a material runway change.

        Source cycle, ingestion timestamps, and raw S3 references are intentionally
        excluded so that loading the same source content remains idempotent.
        """
        return {
            "airport_id": self.airport_id,
            "faa_id": self.faa_id,
            "record_id": self.record_id,
            "physical_runway_id": self.physical_runway_id,
            "length_ft": self.length_ft,
            "width_ft": self.width_ft,
            "surface_type": self.surface_type,
            "surface_condition": self.surface_condition,
            "lighting_code": self.lighting_code,
            "end_1": self.end_1.to_dict(),
            "end_2": self.end_2.to_dict() if self.end_2 else None,
            "source": self.source,
            "schema_version": self.schema_version,
        }

    def to_item(
        self,
        *,
        source_cycle: str,
        raw_s3_uri: str,
        ingested_at_utc: str,
    ) -> dict[str, Any]:
        item = self.material_dict()
        item.update(
            {
                "source_cycle": source_cycle,
                "source_record_hash": self.source_record_hash,
                "raw_s3_uri": raw_s3_uri,
                "ingested_at_utc": ingested_at_utc,
            }
        )
        return {key: value for key, value in item.items() if value is not None}


@dataclass(frozen=True)
class RejectedRecord:
    source_file: str
    reason: str
    row_number: int | None = None
    raw_record: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "internal.runway-bad-record.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParseResult:
    runways: list[NormalizedRunway] = field(default_factory=list)
    rejected_records: list[RejectedRecord] = field(default_factory=list)
    airport_references: dict[str, AirportReference] = field(default_factory=dict)

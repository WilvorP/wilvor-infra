"""Deterministic historical operational fact contracts.

This package maps already-produced operational data into versioned
historical facts. It does not persist facts, query AWS, or decide
operational currentness.
"""

from .contracts import (
    COORDINATES_AXIS_LONLAT,
    ENCOUNTER_FACT_SCHEMA_VERSION,
    HAZARD_GEOMETRY_FACT_SCHEMA_VERSION,
    HAZARD_VERSION_FACT_SCHEMA_VERSION,
    RISK_FACT_SCHEMA_VERSION,
    Dataset,
    EncounterFact,
    FactKind,
    HazardGeometryFact,
    HazardVersionFact,
    HistoricalFactError,
    RiskFact,
)
from .coverage import (
    DEFAULT_COLLECTION_EVALUATION_POLICY,
    CollectionEvaluationPolicy,
    evaluate_collection_window,
    required_streams_for,
)
from .coverage_contracts import (
    COLLECTION_CONTROL_DATASET,
    CONTROL_SCHEMA_VERSION,
    Evaluability,
    HistoricalCoverageError,
)
from .from_events import (
    GEOMETRY_IN_MEMORY_DETAIL_TYPE,
    HistoricalMappingError,
    build_encounter_fact,
    build_hazard_geometry_fact,
    build_hazard_version_fact,
    build_risk_fact,
    fact_from_event,
)

__all__ = [
    "COLLECTION_CONTROL_DATASET",
    "CONTROL_SCHEMA_VERSION",
    "DEFAULT_COLLECTION_EVALUATION_POLICY",
    "CollectionEvaluationPolicy",
    "Evaluability",
    "HistoricalCoverageError",
    "evaluate_collection_window",
    "required_streams_for",
    "COORDINATES_AXIS_LONLAT",
    "ENCOUNTER_FACT_SCHEMA_VERSION",
    "GEOMETRY_IN_MEMORY_DETAIL_TYPE",
    "HAZARD_GEOMETRY_FACT_SCHEMA_VERSION",
    "HAZARD_VERSION_FACT_SCHEMA_VERSION",
    "RISK_FACT_SCHEMA_VERSION",
    "Dataset",
    "EncounterFact",
    "FactKind",
    "HazardGeometryFact",
    "HazardVersionFact",
    "HistoricalFactError",
    "HistoricalMappingError",
    "RiskFact",
    "build_encounter_fact",
    "build_hazard_geometry_fact",
    "build_hazard_version_fact",
    "build_risk_fact",
    "fact_from_event",
]

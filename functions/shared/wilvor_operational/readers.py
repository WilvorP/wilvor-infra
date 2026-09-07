"""Named operational record and candidate readers.

These functions retrieve exact stored rows or DynamoDB-narrowed candidates.
They do not apply current-set semantics, acquire wall-clock time, cache,
read environment variables, or create AWS resources.
"""

from boto3.dynamodb.conditions import Attr, Key

from . import access


IDX_AIRCRAFT_CALLSIGN = "callsign-position_time_epoch-index"
IDX_AIRCRAFT_H3 = "current_h3_cell-position_time_epoch-index"
IDX_PROJECTION_AIRCRAFT_TIME = "aircraft_id-generated_at_epoch-index"
IDX_HAZARD_STATUS_VALIDITY = "status-valid_to_epoch-index"
IDX_ENCOUNTER_AIRCRAFT_TIME = "aircraft_id-detected_at_epoch-index"
IDX_ENCOUNTER_HAZARD_TIME = "hazard_id-detected_at_epoch-index"
IDX_RISK_AIRCRAFT_TIME = "aircraft_id-generated_at_epoch-index"
IDX_RISK_ENCOUNTER_TIME = "encounter_id-generated_at_epoch-index"
IDX_AIRPORT_RISK_TIME = "weather-risk-updated-index"
IDX_AIRPORT_IMPACT_TIME = "weather-impact-updated-index"
IDX_TAF_PERIOD_STATION_TIME = "station_id-period_from_epoch-index"
IDX_AIRPORT_ASSESSMENT_AIRPORT_TIME = "airport_id-created_at_epoch-index"
IDX_RECOMMENDATION_AIRCRAFT_TIME = "aircraft_id-created_at_epoch-index"
IDX_ALERT_AIRCRAFT_TIME = "aircraft_id-updated_at_epoch-index"

CURRENT_ENCOUNTER_STATES = (
    "DETECTED",
    "MONITORING",
)

CURRENT_ALERT_STATES = (
    "NEW",
    "MONITORING",
    "ESCALATED",
    "UPDATED",
)

PROJECTION_INDEX_PROJECTION = (
    "aircraft_id,"
    "projection_id,"
    "generated_at_epoch,"
    "valid_until_epoch,"
    "projection_status"
)

HAZARD_INDEX_PROJECTION = (
    "hazard_id,"
    "source_version,"
    "#hazard_status,"
    "materialization_status,"
    "valid_to_epoch"
)

ENCOUNTER_CANDIDATE_PROJECTION = (
    "encounter_id,"
    "aircraft_id,"
    "projection_id,"
    "hazard_id,"
    "hazard_version_key,"
    "hazard_source_version,"
    "hazard_type,"
    "severity,"
    "encounter_state,"
    "geometry_overlap_status,"
    "time_overlap_status,"
    "altitude_overlap_status,"
    "resolution_reason,"
    "resolved_at_utc,"
    "freshness_status,"
    "corridor_intersects,"
    "centerline_intersects,"
    "inside_now,"
    "exact_intersection_confirmed,"
    "trajectory_confidence,"
    "matched_h3_cell_count,"
    "detected_at_epoch,"
    "detected_at_utc,"
    "valid_from_utc,"
    "valid_to_utc,"
    "expires_at_epoch,"
    "projection_generated_at_utc"
)

RISK_CANDIDATE_PROJECTION = (
    "risk_id,"
    "encounter_id,"
    "aircraft_id,"
    "hazard_id,"
    "hazard_type,"
    "risk_level,"
    "risk_score,"
    "confidence,"
    "generated_at_epoch,"
    "generated_at_utc,"
    "valid_until_utc"
)

RECOMMENDATION_ACTIVE_PROJECTION = (
    "recommendation_id,"
    "risk_id,"
    "recommendation_status,"
    "valid_until_utc,"
    "aircraft_id,"
    "hazard_id,"
    "risk_level,"
    "risk_score,"
    "confidence,"
    "primary_action_type,"
    "preferred_airport_id,"
    "preferred_airport_score,"
    "created_at_utc,"
    "created_at_epoch"
)

ALERT_ACTIVE_PROJECTION = (
    "alert_state,"
    "risk_id,"
    "recommendation_id,"
    "valid_until_utc"
)


class OperationalTables:
    """Injected DynamoDB table handles. No environment or resource creation."""

    def __init__(
        self,
        *,
        aircraft,
        projections,
        projection_points,
        hazards,
        hazard_coordinates,
        encounters,
        risks,
        airports,
        metar,
        taf,
        taf_periods,
        airport_assessments,
        recommendations,
        alerts,
    ):
        self.aircraft = aircraft
        self.projections = projections
        self.projection_points = projection_points
        self.hazards = hazards
        self.hazard_coordinates = hazard_coordinates
        self.encounters = encounters
        self.risks = risks
        self.airports = airports
        self.metar = metar
        self.taf = taf
        self.taf_periods = taf_periods
        self.airport_assessments = airport_assessments
        self.recommendations = recommendations
        self.alerts = alerts


def _with_start_key(kwargs, exclusive_start_key):
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key
    return kwargs


def get_aircraft_record(table, aircraft_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"aircraft_id": aircraft_id},
        consistent_read=consistent_read,
    )


def get_airport_status_record(table, airport_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"airport_id": airport_id},
        consistent_read=consistent_read,
    )


def get_metar_record(table, station_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"station_id": station_id},
        consistent_read=consistent_read,
    )


def get_taf_record(table, station_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"station_id": station_id},
        consistent_read=consistent_read,
    )


def get_projection_record(table, projection_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"projection_id": projection_id},
        consistent_read=consistent_read,
    )


def get_hazard_record(table, hazard_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"hazard_id": hazard_id},
        consistent_read=consistent_read,
    )


def get_risk_record(table, risk_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"risk_id": risk_id},
        consistent_read=consistent_read,
    )


def get_encounter_record(table, encounter_id, *, consistent_read=True):
    return access.get_item(
        table,
        {"encounter_id": encounter_id},
        consistent_read=consistent_read,
    )


def query_aircraft_by_callsign_page(
    table,
    *,
    callsign,
    now_epoch,
    limit,
    exclusive_start_key=None,
    key=Key,
    attr=Attr,
    query_page=access.query_page,
):
    kwargs = {
        "IndexName": IDX_AIRCRAFT_CALLSIGN,
        "KeyConditionExpression": key("callsign").eq(callsign),
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return query_page(table, **kwargs)


def query_aircraft_by_h3_page(
    table,
    *,
    h3_cell,
    now_epoch,
    limit,
    exclusive_start_key=None,
    key=Key,
    attr=Attr,
    query_page=access.query_page,
):
    kwargs = {
        "IndexName": IDX_AIRCRAFT_H3,
        "KeyConditionExpression": key("current_h3_cell").eq(h3_cell),
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return query_page(table, **kwargs)


def scan_aircraft_page(
    table,
    *,
    now_epoch,
    limit,
    exclusive_start_key=None,
    attr=Attr,
    scan_page=access.scan_page,
):
    kwargs = {
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return scan_page(table, **kwargs)


def query_airports_by_impact_page(
    table,
    *,
    weather_impact,
    now_epoch,
    limit,
    weather_risk=None,
    exclusive_start_key=None,
    key=Key,
    attr=Attr,
    query_page=access.query_page,
):
    kwargs = {
        "IndexName": IDX_AIRPORT_IMPACT_TIME,
        "KeyConditionExpression": key("weather_impact_status").eq(weather_impact),
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "ScanIndexForward": False,
        "Limit": limit,
    }

    if weather_risk:
        kwargs["FilterExpression"] = (
            attr("expires_at_epoch").gt(now_epoch)
            & attr("weather_risk_level").eq(weather_risk)
        )

    _with_start_key(kwargs, exclusive_start_key)
    return query_page(table, **kwargs)


def query_airports_by_risk_page(
    table,
    *,
    weather_risk,
    now_epoch,
    limit,
    exclusive_start_key=None,
    key=Key,
    attr=Attr,
    query_page=access.query_page,
):
    kwargs = {
        "IndexName": IDX_AIRPORT_RISK_TIME,
        "KeyConditionExpression": key("weather_risk_level").eq(weather_risk),
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return query_page(table, **kwargs)


def scan_airports_page(
    table,
    *,
    now_epoch,
    limit,
    exclusive_start_key=None,
    attr=Attr,
    scan_page=access.scan_page,
):
    kwargs = {
        "FilterExpression": attr("expires_at_epoch").gt(now_epoch),
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return scan_page(table, **kwargs)


def query_active_hazard_candidates_page(
    table,
    *,
    now_epoch,
    limit,
    exclusive_start_key=None,
    key=Key,
    attr=Attr,
    query_page=access.query_page,
):
    kwargs = {
        "IndexName": IDX_HAZARD_STATUS_VALIDITY,
        "KeyConditionExpression": (
            key("status").eq("ACTIVE")
            & key("valid_to_epoch").gte(now_epoch)
        ),
        "FilterExpression": attr("materialization_status").eq("READY"),
        "ScanIndexForward": True,
        "Limit": limit,
    }
    _with_start_key(kwargs, exclusive_start_key)
    return query_page(table, **kwargs)


def query_projection_points_page(
    table,
    projection_id,
    *,
    key=Key,
    query_page=access.query_page,
):
    return query_page(
        table,
        KeyConditionExpression=key("projection_id").eq(projection_id),
        ScanIndexForward=True,
        ConsistentRead=True,
    )


def query_taf_period_candidates_page(
    table,
    *,
    station_id,
    now_epoch,
    limit=50,
    key=Key,
    query_page=access.query_page,
):
    return query_page(
        table,
        IndexName=IDX_TAF_PERIOD_STATION_TIME,
        KeyConditionExpression=(
            key("station_id").eq(station_id)
            & key("period_from_epoch").between(
                now_epoch - 21600,
                now_epoch + 129600,
            )
        ),
        ScanIndexForward=True,
        Limit=limit,
    )


def query_hazard_coordinate_rows(
    table,
    hazard_version_key,
    *,
    key=Key,
    query_all=access.query_all,
):
    return query_all(
        table,
        KeyConditionExpression=key("hazard_version_key").eq(hazard_version_key),
        ScanIndexForward=True,
        ConsistentRead=True,
    )


def scan_projection_index_candidates(
    table,
    *,
    now_epoch,
    attr=Attr,
    scan_all=access.scan_all,
):
    return scan_all(
        table,
        FilterExpression=(
            attr("projection_status").eq("READY")
            & attr("valid_until_epoch").gt(now_epoch)
        ),
        ProjectionExpression=PROJECTION_INDEX_PROJECTION,
    )


def scan_hazard_index_candidates(
    table,
    *,
    now_epoch,
    attr=Attr,
    scan_all=access.scan_all,
):
    return scan_all(
        table,
        FilterExpression=(
            attr("status").eq("ACTIVE")
            & attr("materialization_status").eq("READY")
            & attr("valid_to_epoch").gte(now_epoch)
        ),
        ProjectionExpression=HAZARD_INDEX_PROJECTION,
        ExpressionAttributeNames={
            "#hazard_status": "status",
        },
    )


def scan_encounter_candidates(
    table,
    *,
    attr=Attr,
    scan_all=access.scan_all,
    encounter_states=CURRENT_ENCOUNTER_STATES,
):
    return scan_all(
        table,
        FilterExpression=attr("encounter_state").is_in(list(encounter_states)),
        ProjectionExpression=ENCOUNTER_CANDIDATE_PROJECTION,
    )


def query_encounter_candidates_by_hazard(
    table,
    hazard_id,
    *,
    key=Key,
    attr=Attr,
    query_all=access.query_all,
    encounter_states=CURRENT_ENCOUNTER_STATES,
):
    return query_all(
        table,
        IndexName=IDX_ENCOUNTER_HAZARD_TIME,
        KeyConditionExpression=key("hazard_id").eq(hazard_id),
        FilterExpression=attr("encounter_state").is_in(list(encounter_states)),
        ProjectionExpression=ENCOUNTER_CANDIDATE_PROJECTION,
        ScanIndexForward=True,
    )


def scan_risk_candidates(
    table,
    *,
    scan_all=access.scan_all,
):
    return scan_all(
        table,
        ProjectionExpression=RISK_CANDIDATE_PROJECTION,
    )


def scan_recommendation_candidates(
    table,
    *,
    now_iso,
    project=False,
    attr=Attr,
    scan_all=access.scan_all,
):
    kwargs = {
        "FilterExpression": (
            attr("recommendation_status").eq("ACTIVE")
            & attr("valid_until_utc").gt(now_iso)
        ),
    }

    if project:
        kwargs["ProjectionExpression"] = RECOMMENDATION_ACTIVE_PROJECTION

    return scan_all(table, **kwargs)


def scan_alert_candidates(
    table,
    *,
    now_iso,
    project=False,
    attr=Attr,
    scan_all=access.scan_all,
    alert_states=CURRENT_ALERT_STATES,
):
    kwargs = {
        "FilterExpression": (
            attr("alert_state").is_in(list(alert_states))
            & attr("valid_until_utc").gt(now_iso)
        ),
    }

    if project:
        kwargs["ProjectionExpression"] = ALERT_ACTIVE_PROJECTION

    return scan_all(table, **kwargs)


def query_latest_for_partition(
    table,
    index_name,
    partition_name,
    partition_value,
    limit=10,
    *,
    key=Key,
    query_latest=access.query_latest,
):
    return query_latest(
        table,
        index_name,
        partition_name,
        partition_value,
        limit=limit,
        key=key,
    )

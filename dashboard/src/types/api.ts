/**
 * Response contracts for the Wilvor operational API.
 *
 * These types were traced from the implementation, not inferred from route
 * names:
 *   - routing and status codes: functions/operational_api/app.py
 *   - response composition:     functions/operational_api/repository.py
 *   - persisted attributes:     the pipeline writers under functions/*
 *
 * Two properties of the backend drive the modelling style here.
 *
 * 1. List endpoints return *raw DynamoDB items*. The Lambda serialises them
 *    with a `Decimal -> int|float` JSON default, so numeric attributes arrive
 *    as real JSON numbers and never need string parsing.
 *
 * 2. The pipeline writers strip `None` values before `put_item`, so nearly
 *    every attribute can legitimately be absent. Aircraft without a fix carry
 *    no `latitude`/`longitude` at all. Optionality below is therefore a
 *    faithful description of the data, not defensive padding, and the UI must
 *    never assume a field is present.
 */

/** Envelope returned by every list endpoint (`repository.py` `_page`). */
export interface PaginatedResponse<TItem> {
  items: TItem[];
  /** Number of items in *this page*. The API exposes no total count. */
  count: number;
  /** Opaque cursor for the next page, or `null` when the page is the last. */
  nextToken: string | null;
}

/* ------------------------------------------------------------------ */
/* Health and freshness                                               */
/* ------------------------------------------------------------------ */

/** `GET /health` - proves API Gateway to Lambda only. */
export interface HealthResponse {
  service?: string;
  status?: string;
  requestId?: string | null;
}

/**
 * Freshness vocabulary for aircraft, METAR and TAF sources.
 * See `_freshness_record` in repository.py.
 */
export type FreshnessStatus =
  | 'FRESH'
  | 'ACCEPTABLE'
  | 'STALE'
  | 'UNAVAILABLE';

/**
 * SIGMET intentionally uses a different vocabulary. An unchanged, still-valid
 * SIGMET must not be reported as stale simply because no newer product was
 * issued, so the backend reports availability instead of age banding.
 */
export type SigmetAvailabilityStatus = 'AVAILABLE' | 'UNAVAILABLE';

export interface FreshnessRecord {
  latestAt?: string | null;
  ageSeconds?: number | null;
  status?: FreshnessStatus | SigmetAvailabilityStatus | string | null;
  /** Present only on the SIGMET record. */
  note?: string | null;
}

/** `GET /freshness` */
export interface FreshnessResponse {
  generatedAt?: string | null;
  mode?: string | null;
  sources?: {
    opensky?: FreshnessRecord | null;
    sigmet?: FreshnessRecord | null;
    metar?: FreshnessRecord | null;
    taf?: FreshnessRecord | null;
  } | null;
}

export type SystemHealthStatus = 'HEALTHY' | 'DEGRADED' | 'CRITICAL';

export interface CloudWatchAlarmSummary {
  alarmName?: string | null;
  metricName?: string | null;
  namespace?: string | null;
  state?: string | null;
  reason?: string | null;
  updatedAt?: string | null;
}

/** `GET /system-health` */
export interface SystemHealthResponse {
  generatedAt?: string | null;
  status?: SystemHealthStatus | string | null;
  lambda?: {
    account?: {
      concurrencyLimit?: number | null;
      unreservedConcurrency?: number | null;
      reservedConcurrency?: number | null;
    } | null;
    recent?: {
      windowMinutes?: number | null;
      maxConcurrentExecutions?: number | null;
      concurrencyUtilizationPercent?: number | null;
    } | null;
    operationalApi?: {
      functionName?: string | null;
      throttlesLast5Minutes?: number | null;
    } | null;
  } | null;
  cloudWatch?: {
    activeAlarmCount?: number | null;
    activeAlarms?: CloudWatchAlarmSummary[] | null;
  } | null;
  dataFreshness?: {
    status?: string | null;
    problemSources?: string[] | null;
    sources?: FreshnessResponse['sources'];
  } | null;
}

export type CloudWatchViewerRange = '1h' | '3h' | '6h' | '12h' | '24h';

export interface CloudWatchDashboardWidget {
  id?: string;
  type?: string | null;
  x?: number | null;
  y?: number | null;
  width?: number | null;
  height?: number | null;
  title?: string | null;
  markdown?: string | null;
  supported?: boolean | null;
}

/** `GET /system-health/dashboards/{dashboardId}` */
export interface CloudWatchDashboardView {
  id?: string;
  name?: string | null;
  awsDashboardName?: string | null;
  generatedAt?: string | null;
  revision?: string | null;
  gridColumns?: number | null;
  widgets?: CloudWatchDashboardWidget[] | null;
}

/* ------------------------------------------------------------------ */
/* Domain records (raw DynamoDB items)                                */
/* ------------------------------------------------------------------ */

export type RiskLevel = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
export type ConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';

/**
 * `AircraftCurrentState` item.
 * Written by functions/shared/wilvor_aircraft/opensky_mapper.py.
 */
export interface AircraftCurrentState {
  aircraft_id?: string;
  callsign?: string;
  origin_country?: string;

  /** Absent when `has_position` is false. */
  latitude?: number;
  longitude?: number;
  has_position?: boolean;

  baro_altitude_m?: number;
  geo_altitude_m?: number;
  baro_altitude_ft?: number;
  geo_altitude_ft?: number;

  ground_speed_mps?: number;
  ground_speed_kt?: number;
  /** Heading in degrees. The attribute is `track_deg`, not `heading`. */
  track_deg?: number;

  vertical_rate_mps?: number;
  vertical_rate_fpm?: number;

  on_ground?: boolean;
  squawk?: string | number;
  spi?: boolean;
  position_source?: number;

  position_time_epoch?: number;
  position_time_utc?: string;
  last_contact_epoch?: number;
  last_contact_utc?: string;
  position_age_seconds?: number;
  freshness_status?: FreshnessStatus | string;

  current_h3_cell?: string;
  h3_resolution?: number;

  state_version?: string;
  processed_at_utc?: string;
  expires_at_epoch?: number;
  schema_version?: string;
}

/**
 * `GET /map/aircraft` response.
 *
 * Unlike the other routes this is not a raw DynamoDB item: it is a compact
 * projection built by `get_map_aircraft` in repository.py for the map layer.
 *
 * Rows are **positional**, and their meaning is defined by `columns` rather
 * than by this type. Everything is optional and widely typed on purpose —
 * the payload is validated at the boundary before any row is read, so the
 * decoder can report a contract mismatch instead of silently misreading
 * positions. See `features/map/aircraftGeoJson.ts`.
 */
export interface MapAircraftResponse {
  generatedAt?: string;
  /** Names the position of each field in every row. */
  columns?: unknown;
  /** Row count the backend believes it sent. */
  count?: number;
  /** Backend hit its internal cap; the fleet shown is incomplete. */
  truncated?: boolean;
  aircraft?: unknown;
}

/** `AircraftProjection` item. */
export interface AircraftProjection {
  projection_id?: string;
  aircraft_id?: string;
  generated_at_epoch?: number;
  generated_at_utc?: string;
  valid_until_epoch?: number;
  valid_until_utc?: string;
  projection_horizon_min?: number;
  point_count?: number;
  confidence?: ConfidenceLevel | string;
  projection_status?: string;
  trigger_hazard_ids?: string[];
  projection_algorithm_version?: string;
  schema_version?: string;
}

/** `AircraftProjectionPoints` item. */
export interface AircraftProjectionPoint {
  projection_id?: string;
  point_key?: string;
  point_sequence_number?: number;
  aircraft_id?: string;
  horizon_min?: number;
  projected_time_epoch?: number;
  projected_time_utc?: string;
  latitude?: number;
  longitude?: number;
  estimated_altitude_ft?: number;
  confidence?: ConfidenceLevel | string;
  h3_cell?: string;
  generated_at_utc?: string;
}

export type HazardStatus = 'ACTIVE' | 'CANCELLED' | 'EXPIRED';
export type HazardMaterializationStatus = 'BUILDING' | 'READY' | 'FAILED';

export interface HazardAltitudeBand {
  source_band_index?: number;
  lower_altitude_ft?: number;
  upper_altitude_ft?: number;
}

/** GeoJSON geometry as reconstructed by `_hazard_geometry` in repository.py. */
export type HazardGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] };

/**
 * `ActiveHazards` item as returned by `GET /hazards/active`.
 *
 * `geometry` is not persisted. The API reconstructs it from the
 * `HazardCoordinates` rows and attaches it only when reconstruction produced
 * at least one valid ring, so it must be treated as optional.
 */
export interface ActiveHazard {
  hazard_id?: string;
  source_version?: string;
  source_product_id?: string;
  /** Issuing office ICAO. There is no `issuing_office` attribute. */
  source_icao_id?: string;

  product_type?: 'SIGMET' | 'AIRMET' | string;
  hazard_type?: string;
  severity?: string;
  amendment_type?: string;

  status?: HazardStatus | string;
  materialization_status?: HazardMaterializationStatus | string;

  valid_from_epoch?: number;
  valid_from_utc?: string;
  valid_to_epoch?: number;
  valid_to_utc?: string;

  geometry_type?: 'POLYGON' | 'MULTIPOLYGON' | string;
  geometry_point_count?: number;
  geometry?: HazardGeometry;

  altitude_bands?: HazardAltitudeBand[];
  minimum_lower_altitude_ft?: number;
  maximum_upper_altitude_ft?: number;

  movement_direction_deg?: number;
  movement_speed_kt?: number;

  raw_text?: string;
  created_at_utc?: string;
  materialized_at_utc?: string;
  expires_at_epoch?: number;
}

export type EncounterState =
  | 'DETECTED'
  | 'MONITORING'
  | 'RESOLVED'
  | 'SUPERSEDED'
  | 'EXPIRED';

/** `AircraftHazardEncounter` item. */
export interface AircraftHazardEncounter {
  encounter_id?: string;
  aircraft_id?: string;
  projection_id?: string;
  hazard_id?: string;
  hazard_version_key?: string;
  hazard_type?: string;
  severity?: string;

  encounter_state?: EncounterState | string;
  geometry_overlap_status?: string;
  time_overlap_status?: string;
  /** OVERLAP, NO_OVERLAP, or UNKNOWN when altitude evidence is missing. */
  altitude_overlap_status?: string;
  resolution_reason?: string;
  resolved_at_utc?: string;
  freshness_status?: string;

  corridor_intersects?: boolean;
  centerline_intersects?: boolean;
  inside_now?: boolean;
  exact_intersection_confirmed?: boolean;
  trajectory_confidence?: ConfidenceLevel | string;

  matched_h3_cell_count?: number;
  detected_at_epoch?: number;
  detected_at_utc?: string;
  valid_from_utc?: string;
  valid_to_utc?: string;
  expires_at_epoch?: number;

  /** Copied from the projection that produced the encounter; not a new calculation. */
  projection_generated_at_utc?: string;
}

/** `RiskResults` item. */
export interface RiskResult {
  risk_id?: string;
  encounter_id?: string;
  aircraft_id?: string;
  hazard_id?: string;
  hazard_type?: string;
  severity?: string;

  risk_level?: RiskLevel | string;
  risk_score?: number;
  confidence?: ConfidenceLevel | string;
  freshness_status?: string;

  hazard_component_score?: number;
  geometry_component_score?: number;
  time_component_score?: number;
  altitude_component_score?: number;
  confidence_component_score?: number;
  freshness_component_score?: number;
  data_quality_component_score?: number;

  reasons?: string[];
  limitations?: string[];

  generated_at_epoch?: number;
  generated_at_utc?: string;
  /** Only the ISO form is persisted; there is no `valid_until_epoch`. */
  valid_until_utc?: string;
}

/** `AirportStatus` item. Carries coordinates, so it is directly plottable. */
export interface AirportStatus {
  airport_id?: string;
  station_id?: string;
  station_name?: string;
  iata_code?: string;
  country_code?: string;

  latitude?: number;
  longitude?: number;
  elevation_m?: number;
  faa_lid?: string;
  station_type?: string;
  is_airport?: boolean;

  has_metar?: boolean;
  has_taf?: boolean;
  metar_fetch_status?: string;
  taf_fetch_status?: string;
  metar_freshness_status?: FreshnessStatus | string;
  taf_freshness_status?: FreshnessStatus | string;
  metar_age_seconds?: number;
  taf_age_seconds?: number;

  temperature_c?: number;
  dewpoint_c?: number;
  wind_direction_deg?: number;
  wind_speed_kt?: number;
  wind_gust_kt?: number;
  visibility_sm?: number;
  ceiling_ft?: number;
  flight_category?: string;
  weather_string?: string;
  weather_codes?: string[];

  weather_risk_level?: RiskLevel | string;
  weather_impact_status?: string;
  assessment_status?: string;
  is_diversion_weather_ready?: boolean;

  /** The attribute is `status_reasons`, not `reasons`. */
  status_reasons?: string[];
  known_limitations?: string[];

  observed_time_utc?: string;
  observed_time_epoch?: number;
  issued_at_utc?: string;
  issued_at_epoch?: number;
  valid_from_utc?: string;
  valid_to_utc?: string;
  forecast_period_count?: number;
  period_materialization_status?: string;
  updated_at_epoch?: number;
  updated_at_utc?: string;
  expires_at_epoch?: number;
}

export interface MetarCloudLayer {
  cover?: string;
  base_ft?: number;
}

/** `MetarLatest` item. */
export interface MetarLatest {
  station_id?: string;
  airport_id?: string;
  station_name?: string;
  observed_time_epoch?: number;
  observed_time_utc?: string;
  temperature_c?: number;
  dewpoint_c?: number;
  wind_direction_deg?: number;
  wind_speed_kt?: number;
  wind_gust_kt?: number;
  visibility_sm?: number;
  ceiling_ft?: number;
  altimeter_hpa?: number;
  flight_category?: string;
  weather_string?: string;
  weather_codes?: string[];
  clouds?: MetarCloudLayer[];
  raw_text?: string;
  latitude?: number;
  longitude?: number;
  freshness_status?: string;
  processed_at_utc?: string;
}

/** `TafLatest` item. */
export interface TafLatest {
  station_id?: string;
  airport_id?: string;
  taf_version?: string;
  issued_at_utc?: string;
  issued_at_epoch?: number;
  valid_from_utc?: string;
  valid_to_utc?: string;
  raw_text?: string;
  forecast_period_count?: number;
  period_materialization_status?: string;
  freshness_status?: string;
  is_amendment?: boolean;
  is_correction?: boolean;
}

/** `TafForecastPeriods` item. */
export interface TafForecastPeriod {
  taf_version_key?: string;
  period_key?: string;
  period_id?: string;
  station_id?: string;
  period_from_epoch?: number;
  period_from_utc?: string;
  period_to_epoch?: number;
  period_to_utc?: string;
  change_type?: string;
  probability?: number;
  wind_direction_deg?: number;
  wind_speed_kt?: number;
  wind_gust_kt?: number;
  visibility_sm?: number;
  ceiling_ft?: number;
  /** Named `forecast_flight_category`, not `flight_category`. */
  forecast_flight_category?: string;
  weather_string?: string;
  clouds?: MetarCloudLayer[];
  sequence_number?: number;
}

/** `AirportAssessment` item. */
export interface AirportAssessment {
  evaluation_id?: string;
  airport_assessment_id?: string;
  airport_id?: string;
  airport_name?: string;
  aircraft_id?: string;
  hazard_id?: string;
  risk_id?: string;

  airport_latitude?: number;
  airport_longitude?: number;

  distance_nm?: number;
  eta_minutes?: number;
  estimated_arrival_time_utc?: string;
  eta_uncertainty_minutes?: number;

  assessment_status?: string;
  hard_filter_passed?: boolean;
  rejection_reasons?: string[];
  known_limitations?: string[];

  distance_score?: number;
  weather_score?: number;
  taf_score?: number;
  total_airport_score?: number;
  rank?: number;

  weather_risk_level?: RiskLevel | string;
  runway_evidence_status?: string;
  congestion_evidence_status?: string;
  route_safety_status?: string;

  created_at_epoch?: number;
  created_at_utc?: string;
}

export type RecommendationActionType =
  | 'EVALUATE_DIVERSION'
  | 'MONITOR_AND_PREPARE_OPTIONS'
  | 'MONITOR';

export interface RecommendationAlternativeAction {
  type?: string;
  airport_id?: string;
  airport_assessment_id?: string;
  score?: number;
  rank?: number;
}

export interface CandidateAirportSummary {
  airport_id?: string;
  airport_assessment_id?: string;
  rank?: number;
  total_airport_score?: number;
  distance_nm?: number;
  eta_minutes?: number;
  weather_risk_level?: RiskLevel | string;
}

/** Entry in `Recommendations.evidence_references`. */
export interface RecommendationEvidenceReference {
  type?: string;
  id?: string;
  airport_id?: string;
}

/**
 * `Recommendations` item. Note: there is no `encounter_id` attribute.
 *
 * `GET /recommendations/active` returns the current operational set (same
 * definition as `overview.recommendations.currentCount`), not every retained
 * ACTIVE+valid_until row.
 */
export interface Recommendation {
  recommendation_id?: string;
  recommendation_status?: 'ACTIVE' | string;
  risk_id?: string;
  aircraft_id?: string;
  hazard_id?: string;

  risk_level?: RiskLevel | string;
  risk_score?: number;
  confidence?: ConfidenceLevel | string;

  primary_action_type?: RecommendationActionType | string;
  primary_action_details?: {
    advisory?: string;
    requires_human_review?: boolean;
    candidate_count?: number;
  };
  alternative_actions?: RecommendationAlternativeAction[];
  candidate_airport_summaries?: CandidateAirportSummary[];

  preferred_airport_id?: string;
  preferred_airport_score?: number;
  no_suitable_candidate_reason?: string;

  /** There is no `rationale` attribute; explanations live in `reasons`. */
  reasons?: string[];
  limitations?: string[];
  advisory_notice?: string;

  valid_from_utc?: string;
  valid_until_utc?: string;
  created_at_utc?: string;
  created_at_epoch?: number;
  updated_at_utc?: string;

  evidence_references?: RecommendationEvidenceReference[];
  source_versions?: Record<string, unknown>;
  recommendation_version?: string;
  ruleset_version?: string;
  airport_evaluation_id?: string;
  preferred_airport_assessment_id?: string;
}

export type AlertState =
  | 'NEW'
  | 'MONITORING'
  | 'ESCALATED'
  | 'UPDATED'
  | 'RESOLVED';

/**
 * `ActiveAlerts` item. The table hash key is `fingerprint`, not `alert_id`.
 *
 * `GET /alerts/active` returns the current operational set (same definition as
 * `overview.alerts.currentCount`), not every retained non-resolved row.
 */
export interface ActiveAlert {
  fingerprint?: string;
  alert_id?: string;
  alert_type?: string;
  alert_state?: AlertState | string;
  state_reason?: string;
  /** The only user-facing text; there is no title or summary attribute. */
  message?: string;

  aircraft_id?: string;
  hazard_id?: string;
  recommendation_id?: string;
  risk_id?: string;

  risk_level?: RiskLevel | string;
  risk_score?: number;
  primary_action_type?: string;
  preferred_airport_id?: string;

  notification_count?: number;
  last_notified_at_utc?: string;
  created_at_utc?: string;
  updated_at_epoch?: number;
  updated_at_utc?: string;
  valid_until_utc?: string;
  resolved_at_utc?: string;
  superseded_by_alert_id?: string;
}

/* ------------------------------------------------------------------ */
/* Composite endpoint responses                                        */
/* ------------------------------------------------------------------ */

/**
 * One current aircraft-hazard decision chain.
 * Members are joined by stored IDs, not by latest timestamp.
 */
export interface AircraftOperationalContext {
  encounter?: AircraftHazardEncounter | null;
  risk?: RiskResult | null;
  recommendation?: Recommendation | null;
  alert?: ActiveAlert | null;
}

/** `GET /aircraft/{aircraftId}` */
export interface AircraftDetailResponse {
  aircraft?: AircraftCurrentState | null;
  projection?: AircraftProjection | null;
  projectionPoints?: AircraftProjectionPoint[] | null;
  currentContexts?: AircraftOperationalContext[] | null;
  recentEncounters?: AircraftHazardEncounter[] | null;
  recentRisks?: RiskResult[] | null;
  recentRecommendations?: Recommendation[] | null;
  recentAlerts?: ActiveAlert[] | null;
}

/** `GET /airports/{airportId}` */
export interface AirportDetailResponse {
  airport?: AirportStatus | null;
  metar?: MetarLatest | null;
  taf?: TafLatest | null;
  tafForecastPeriods?: TafForecastPeriod[] | null;
  recentAssessments?: AirportAssessment[] | null;
}

/** Item shape of `GET /encounters/active`. */
export interface ActiveEncounterItem {
  encounter?: AircraftHazardEncounter | null;
  risk?: RiskResult | null;
}

/**
 * Compact recommendation projection embedded in `/overview`.
 * This is camelCase, unlike the raw snake_case recommendation records.
 */
export interface OverviewRecommendationSummary {
  recommendationId?: string | null;
  aircraftId?: string | null;
  hazardId?: string | null;
  riskLevel?: RiskLevel | string | null;
  riskScore?: number | null;
  confidence?: ConfidenceLevel | string | null;
  action?: string | null;
  preferredAirportId?: string | null;
  preferredAirportScore?: number | null;
  validUntilUtc?: string | null;
  createdAtUtc?: string | null;
}

/** `GET /overview` */
export interface OverviewResponse {
  generatedAt?: string | null;

  aircraft?: { activeCount?: number | null } | null;

  hazards?: { activeCount?: number | null } | null;

  encounters?: {
    activeCount?: number | null;
    riskEvaluatedCount?: number | null;
    highRiskCount?: number | null;
    mediumRiskCount?: number | null;
    lowRiskCount?: number | null;
    riskCounts?: Record<string, number> | null;
  } | null;

  recommendations?: {
    /** ACTIVE rows that have not reached valid_until. Not the current-set. */
    activeCount?: number | null;
    /** Recommendations whose supporting risk/encounter is currently current. */
    currentCount?: number | null;
    latest?: OverviewRecommendationSummary[] | null;
  } | null;

  alerts?: {
    /** Non-resolved alerts that have not reached valid_until. */
    activeCount?: number | null;
    /** Alerts whose supporting current risk/recommendation is current. */
    currentCount?: number | null;
    byState?: Record<string, number> | null;
  } | null;

  airports?: {
    currentCount?: number | null;
    weatherImpactedCount?: number | null;
    byWeatherRisk?: Record<string, number> | null;
    byWeatherImpact?: Record<string, number> | null;
    /** Partial `AirportStatus` records: only projected attributes. */
    topImpacted?: AirportStatus[] | null;
  } | null;

  /** Partial `RiskResult` records: only projected attributes. */
  topRisks?: RiskResult[] | null;
}

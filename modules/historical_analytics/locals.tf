locals {
  enabled = var.enable_historical_analytics

  historical_bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"
  results_bucket_name    = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"
  glue_database_name     = "${replace(var.name_prefix, "-", "_")}_historical_facts"
  workgroup_name         = "${var.name_prefix}-historical-analytics"
  athena_results_prefix  = "athena-results/"

  historical_bucket_id  = local.enabled ? data.aws_s3_bucket.historical_facts[0].id : ""
  historical_bucket_arn = local.enabled ? data.aws_s3_bucket.historical_facts[0].arn : ""

  common_tags = merge(var.tags, {
    Component = "historical-analytics"
    DataType  = "historical-analytics-derived"
  })

  structured_schema_versions = {
    encounter      = "wilvor.historical.encounter_fact.v1"
    risk           = "wilvor.historical.risk_fact.v1"
    hazard_version = "wilvor.historical.hazard_version_fact.v1"
  }

  geometry_schema_version = "wilvor.historical.hazard_geometry_fact.v1"

  partition_projection_parameters = {
    "projection.enabled"      = "true"
    "projection.year.type"    = "integer"
    "projection.year.range"   = "${var.projection_year_min},${var.projection_year_max}"
    "projection.year.digits"  = "4"
    "projection.month.type"   = "integer"
    "projection.month.range"  = "1,12"
    "projection.month.digits" = "2"
    "projection.day.type"     = "integer"
    "projection.day.range"    = "1,31"
    "projection.day.digits"   = "2"
  }

  structured_columns = {
    encounter = [
      { name = "dataset", type = "string" },
      { name = "fact_schema_version", type = "string" },
      { name = "fact_kind", type = "string" },
      { name = "record_id", type = "string" },
      { name = "dedup_id", type = "string" },
      { name = "event_time_utc", type = "string" },
      { name = "event_time_epoch", type = "bigint" },
      { name = "event_year", type = "string" },
      { name = "event_month", type = "string" },
      { name = "event_day", type = "string" },
      { name = "source_system", type = "string" },
      { name = "producer_source", type = "string" },
      { name = "producer_detail_type", type = "string" },
      { name = "producer_schema_version", type = "string" },
      { name = "correlation_id", type = "string" },
      { name = "encounter_id", type = "string" },
      { name = "aircraft_id", type = "string" },
      { name = "aircraft_state_version", type = "string" },
      { name = "projection_id", type = "string" },
      { name = "hazard_id", type = "string" },
      { name = "hazard_source_version", type = "string" },
      { name = "hazard_version_key", type = "string" },
      { name = "encounter_state", type = "string" },
      { name = "geometry_overlap_status", type = "string" },
      { name = "time_overlap_status", type = "string" },
      { name = "altitude_overlap_status", type = "string" },
      { name = "exact_intersection_confirmed", type = "boolean" },
      { name = "detected_at_epoch", type = "bigint" },
      { name = "detected_at_utc", type = "string" },
      { name = "resolution_reason", type = "string" },
      { name = "geometry_hash", type = "string" },
      { name = "hazard_type", type = "string" },
      { name = "inside_now", type = "boolean" },
      { name = "corridor_intersects", type = "boolean" },
      { name = "resolved_at_epoch", type = "bigint" },
      { name = "resolved_at_utc", type = "string" },
    ]
    risk = [
      { name = "dataset", type = "string" },
      { name = "fact_schema_version", type = "string" },
      { name = "fact_kind", type = "string" },
      { name = "record_id", type = "string" },
      { name = "dedup_id", type = "string" },
      { name = "event_time_utc", type = "string" },
      { name = "event_time_epoch", type = "bigint" },
      { name = "event_year", type = "string" },
      { name = "event_month", type = "string" },
      { name = "event_day", type = "string" },
      { name = "source_system", type = "string" },
      { name = "producer_source", type = "string" },
      { name = "producer_detail_type", type = "string" },
      { name = "producer_schema_version", type = "string" },
      { name = "correlation_id", type = "string" },
      { name = "risk_id", type = "string" },
      { name = "encounter_id", type = "string" },
      { name = "aircraft_id", type = "string" },
      { name = "hazard_id", type = "string" },
      { name = "hazard_source_version", type = "string" },
      { name = "projection_id", type = "string" },
      { name = "risk_score", type = "double" },
      { name = "risk_level", type = "string" },
      { name = "generated_at_utc", type = "string" },
      { name = "generated_at_epoch", type = "bigint" },
      { name = "scoring_ruleset_version", type = "string" },
      { name = "scoring_config_version", type = "string" },
      { name = "hazard_type", type = "string" },
      { name = "encounter_state", type = "string" },
      { name = "confidence", type = "string" },
      { name = "freshness_status", type = "string" },
      { name = "valid_until_utc", type = "string" },
    ]
    hazard_version = [
      { name = "dataset", type = "string" },
      { name = "fact_schema_version", type = "string" },
      { name = "fact_kind", type = "string" },
      { name = "record_id", type = "string" },
      { name = "dedup_id", type = "string" },
      { name = "event_time_utc", type = "string" },
      { name = "event_time_epoch", type = "bigint" },
      { name = "event_year", type = "string" },
      { name = "event_month", type = "string" },
      { name = "event_day", type = "string" },
      { name = "source_system", type = "string" },
      { name = "producer_source", type = "string" },
      { name = "producer_detail_type", type = "string" },
      { name = "producer_schema_version", type = "string" },
      { name = "correlation_id", type = "string" },
      { name = "hazard_id", type = "string" },
      { name = "source_version", type = "string" },
      { name = "hazard_version_key", type = "string" },
      { name = "materialization_id", type = "string" },
      { name = "geometry_hash", type = "string" },
      { name = "geometry_type", type = "string" },
      { name = "geometry_point_count", type = "bigint" },
      { name = "product_type", type = "string" },
      { name = "hazard_type", type = "string" },
      { name = "status", type = "string" },
      { name = "valid_from_utc", type = "string" },
      { name = "valid_to_utc", type = "string" },
      { name = "materialized_at_utc", type = "string" },
      { name = "amendment_type", type = "string" },
      { name = "created_at_utc", type = "string" },
      { name = "received_at_utc", type = "string" },
      { name = "source_product_id", type = "string" },
      { name = "severity", type = "string" },
      { name = "minimum_lower_altitude_ft", type = "double" },
      { name = "maximum_upper_altitude_ft", type = "double" },
      { name = "source_icao_id", type = "string" },
      { name = "series_id", type = "string" },
      { name = "alpha_char", type = "string" },
      { name = "raw_s3_uri", type = "string" },
    ]
  }
}

data "aws_s3_bucket" "historical_facts" {
  count = local.enabled ? 1 : 0

  bucket = local.historical_bucket_name
}

resource "aws_glue_catalog_database" "historical_facts" {
  count = local.enabled ? 1 : 0

  name = local.glue_database_name

  description = "Wilvor historical operational facts. Athena can answer which rows are present. Collection completeness remains the coverage evaluator, not this catalog."

  lifecycle {
    precondition {
      condition     = var.projection_year_max >= var.projection_year_min
      error_message = "projection_year_max must be >= projection_year_min."
    }
  }
}

resource "aws_glue_catalog_table" "structured" {
  for_each = local.enabled ? local.structured_schema_versions : {}

  name          = each.key
  database_name = aws_glue_catalog_database.historical_facts[0].name
  table_type    = "EXTERNAL_TABLE"
  description   = "Canonical ${each.key} facts. Hive JsonSerDe. OpenX is forbidden."

  parameters = merge(
    local.partition_projection_parameters,
    {
      EXTERNAL                              = "TRUE"
      classification                        = "json"
      compressionType                       = "gzip"
      "wilvor.expected_fact_schema_version" = each.value
      "storage.location.template"           = "s3://${local.historical_bucket_id}/dataset=${each.key}/year=$${year}/month=$${month}/day=$${day}"
    }
  )

  storage_descriptor {
    location      = "s3://${local.historical_bucket_id}/dataset=${each.key}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hive.hcatalog.data.JsonSerDe"
    }

    dynamic "columns" {
      for_each = local.structured_columns[each.key]

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  partition_keys {
    name = "year"
    type = "string"
  }

  partition_keys {
    name = "month"
    type = "string"
  }

  partition_keys {
    name = "day"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "hazard_geometry" {
  count = local.enabled ? 1 : 0

  name          = "hazard_geometry"
  database_name = aws_glue_catalog_database.historical_facts[0].name
  table_type    = "EXTERNAL_TABLE"
  description   = "Canonical hazard_geometry facts as raw JSONL lines. RegexSerDe json_record preserves Polygon and MultiPolygon GeoJSON. OpenX is forbidden."

  parameters = merge(
    local.partition_projection_parameters,
    {
      EXTERNAL                              = "TRUE"
      classification                        = "json"
      compressionType                       = "gzip"
      "wilvor.expected_fact_schema_version" = local.geometry_schema_version
      "storage.location.template"           = "s3://${local.historical_bucket_id}/dataset=hazard_geometry/year=$${year}/month=$${month}/day=$${day}"
    }
  )

  storage_descriptor {
    location      = "s3://${local.historical_bucket_id}/dataset=hazard_geometry/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.RegexSerDe"

      parameters = {
        "input.regex" = "^(.*)$"
      }
    }

    columns {
      name = "json_record"
      type = "string"
    }
  }

  partition_keys {
    name = "year"
    type = "string"
  }

  partition_keys {
    name = "month"
    type = "string"
  }

  partition_keys {
    name = "day"
    type = "string"
  }
}

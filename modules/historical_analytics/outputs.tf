output "enable_historical_analytics" {
  value = var.enable_historical_analytics
}

output "results_bucket_name" {
  value = local.enabled ? aws_s3_bucket.athena_results[0].id : ""
}

output "results_bucket_arn" {
  value = local.enabled ? aws_s3_bucket.athena_results[0].arn : ""
}

output "historical_bucket_name" {
  value = local.historical_bucket_id
}

output "historical_bucket_arn" {
  value = local.historical_bucket_arn
}

output "database_name" {
  value = local.enabled ? aws_glue_catalog_database.historical_facts[0].name : ""
}

output "encounter_table_name" {
  value = local.enabled ? aws_glue_catalog_table.structured["encounter"].name : ""
}

output "risk_table_name" {
  value = local.enabled ? aws_glue_catalog_table.structured["risk"].name : ""
}

output "hazard_version_table_name" {
  value = local.enabled ? aws_glue_catalog_table.structured["hazard_version"].name : ""
}

output "hazard_geometry_table_name" {
  value = local.enabled ? aws_glue_catalog_table.hazard_geometry[0].name : ""
}

output "workgroup_name" {
  value = local.enabled ? aws_athena_workgroup.historical_analytics[0].name : ""
}

output "dashboard_name" {
  value = local.enabled ? aws_cloudwatch_dashboard.historical_analytics[0].dashboard_name : ""
}

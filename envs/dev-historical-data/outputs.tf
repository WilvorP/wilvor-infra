output "historical_bucket_name" {
  value = module.historical_facts_data.historical_bucket_name
}

output "historical_bucket_arn" {
  value = module.historical_facts_data.historical_bucket_arn
}

output "force_destroy" {
  value = module.historical_facts_data.force_destroy
}

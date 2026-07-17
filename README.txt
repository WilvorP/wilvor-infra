Copy modules/taf/monitoring.tf into your repository.
Then add this output to modules/taf/outputs.tf:

output "dashboard_name" {
  description = "Name of the TAF CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.taf_pipeline.dashboard_name
}

And add this output to envs/dev/outputs.tf:

output "taf_dashboard_name" {
  value = module.taf.dashboard_name
}

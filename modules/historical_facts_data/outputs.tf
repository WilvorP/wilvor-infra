output "historical_bucket_name" {
  value = aws_s3_bucket.historical_facts.id
}

output "historical_bucket_arn" {
  value = aws_s3_bucket.historical_facts.arn
}

output "force_destroy" {
  value = aws_s3_bucket.historical_facts.force_destroy
}

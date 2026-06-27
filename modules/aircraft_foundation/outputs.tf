output "aircraft_raw_stream_name" {
  value = aws_kinesis_stream.aircraft_raw.name
}

output "aircraft_raw_stream_arn" {
  value = aws_kinesis_stream.aircraft_raw.arn
}

output "aircraft_clean_stream_name" {
  value = aws_kinesis_stream.aircraft_clean.name
}

output "aircraft_clean_stream_arn" {
  value = aws_kinesis_stream.aircraft_clean.arn
}

output "aircraft_archive_bucket_name" {
  value = aws_s3_bucket.aircraft_archive.bucket
}

output "aircraft_current_state_table_name" {
  value = aws_dynamodb_table.aircraft_current_state.name
}

output "aircraft_current_state_table_arn" {
  value = aws_dynamodb_table.aircraft_current_state.arn
}
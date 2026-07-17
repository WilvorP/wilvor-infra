resource "aws_kinesis_stream" "taf_raw" {
  name             = "${var.name_prefix}-taf-raw"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-raw"
    Component = "taf-ingestion"
    DataType  = "raw"
  })
}

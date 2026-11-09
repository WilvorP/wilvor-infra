locals {
  bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"

  common_tags = merge(var.tags, {
    Component = "historical-facts-data"
    DataType  = "historical-facts"
  })
}

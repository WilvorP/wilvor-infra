resource "aws_secretsmanager_secret" "opensky_credentials" {
  name        = "${var.name_prefix}/opensky/credentials"
  description = "OpenSky OAuth client credentials for Wilvor aircraft ingestion"

  recovery_window_in_days = 7

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}/opensky/credentials"
    Component = "aircraft-ingestion"
  })
}
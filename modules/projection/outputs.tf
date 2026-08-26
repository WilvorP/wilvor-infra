output "aircraft_projection_points_table_name" {
  value = aws_dynamodb_table.aircraft_projection_points.name
}

output "aircraft_projection_points_table_arn" {
  value = aws_dynamodb_table.aircraft_projection_points.arn
}

output "aircraft_projection_points_aircraft_time_index_name" {
  value = "aircraft_id-projected_time_epoch-index"
}

output "aircraft_projection_table_name" {
  value = aws_dynamodb_table.aircraft_projection.name
}

output "aircraft_projection_table_arn" {
  value = aws_dynamodb_table.aircraft_projection.arn
}

output "aircraft_projection_aircraft_time_index_name" {
  value = "aircraft_id-generated_at_epoch-index"
}

output "aircraft_projection_status_validity_index_name" {
  value = "projection_status-valid_until_epoch-index"
}


output "aircraft_projection_cells_table_name" {
  value = aws_dynamodb_table.aircraft_projection_cells.name
}

output "aircraft_projection_cells_table_arn" {
  value = aws_dynamodb_table.aircraft_projection_cells.arn
}

output "aircraft_projection_cells_h3_index_name" {
  value = "h3_cell-projection_id-index"
}
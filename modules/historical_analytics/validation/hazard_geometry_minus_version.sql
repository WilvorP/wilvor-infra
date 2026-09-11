SELECT COUNT(*) AS geometry_ids_missing_from_version
FROM (
  SELECT DISTINCT json_extract_scalar(json_record, '$.record_id')
  FROM __DATABASE__.hazard_geometry
  WHERE year = '__YEAR__'
    AND month = '__MONTH__'
    AND day = '__DAY__'
  EXCEPT
  SELECT DISTINCT record_id
  FROM __DATABASE__.hazard_version
  WHERE year = '__YEAR__'
    AND month = '__MONTH__'
    AND day = '__DAY__'
)

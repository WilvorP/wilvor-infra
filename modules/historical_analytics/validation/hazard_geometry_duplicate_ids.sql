SELECT COUNT(*) AS duplicate_identity_groups
FROM (
  SELECT json_extract_scalar(json_record, '$.record_id') AS record_id
  FROM __DATABASE__.hazard_geometry
  WHERE year = '__YEAR__'
    AND month = '__MONTH__'
    AND day = '__DAY__'
  GROUP BY json_extract_scalar(json_record, '$.record_id')
  HAVING COUNT(*) > 1
)

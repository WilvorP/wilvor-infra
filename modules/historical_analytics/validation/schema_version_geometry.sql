SELECT COUNT(*) AS unexpected_or_null_versions
FROM __DATABASE__.hazard_geometry
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND (
    json_extract_scalar(json_record, '$.fact_schema_version') IS NULL
    OR json_extract_scalar(json_record, '$.fact_schema_version') <> '__SCHEMA_VERSION__'
  )

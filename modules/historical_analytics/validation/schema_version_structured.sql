SELECT COUNT(*) AS unexpected_or_null_versions
FROM __DATABASE__.__TABLE__
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND (
    fact_schema_version IS NULL
    OR fact_schema_version <> '__SCHEMA_VERSION__'
  )

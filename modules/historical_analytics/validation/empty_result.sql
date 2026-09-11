SELECT COUNT(*) AS row_count
FROM __DATABASE__.encounter
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND fact_schema_version = 'wilvor.historical.validation_empty.v0'

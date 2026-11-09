SELECT
  COUNT(*) AS physical_rows,
  COUNT(DISTINCT record_id) AS distinct_record_id,
  COUNT(DISTINCT dedup_id) AS distinct_dedup_id,
  COUNT(DISTINCT hazard_version_key) AS distinct_hazard_version_key,
  SUM(
    CASE
      WHEN record_id <> hazard_version_key
        OR dedup_id <> hazard_version_key THEN 1
      ELSE 0
    END
  ) AS identity_mismatch
FROM __DATABASE__.hazard_version
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'

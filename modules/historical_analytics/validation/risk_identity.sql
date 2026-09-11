SELECT
  COUNT(*) AS physical_rows,
  COUNT(DISTINCT risk_id) AS distinct_risk_id,
  COUNT(DISTINCT record_id) AS distinct_record_id,
  COUNT(DISTINCT dedup_id) AS distinct_dedup_id,
  SUM(
    CASE
      WHEN record_id <> risk_id OR dedup_id <> risk_id THEN 1
      ELSE 0
    END
  ) AS identity_mismatch
FROM __DATABASE__.risk
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'

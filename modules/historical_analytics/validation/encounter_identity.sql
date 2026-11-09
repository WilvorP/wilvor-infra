SELECT
  COUNT(*) AS physical_rows,
  COUNT(DISTINCT record_id) AS distinct_record_id,
  COUNT(DISTINCT encounter_id) AS distinct_encounter_id,
  COUNT(DISTINCT dedup_id) AS distinct_dedup_id,
  SUM(CASE WHEN record_id <> encounter_id THEN 1 ELSE 0 END) AS record_encounter_mismatch
FROM __DATABASE__.encounter
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'

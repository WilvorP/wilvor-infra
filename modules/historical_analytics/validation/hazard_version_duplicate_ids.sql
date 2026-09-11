SELECT COUNT(*) AS duplicate_identity_groups
FROM (
  SELECT record_id
  FROM __DATABASE__.hazard_version
  WHERE year = '__YEAR__'
    AND month = '__MONTH__'
    AND day = '__DAY__'
  GROUP BY record_id
  HAVING COUNT(*) > 1
)

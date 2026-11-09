SELECT COUNT(*) AS misaligned_or_invalid_event_time
FROM __DATABASE__.__TABLE__
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND (
    event_year IS NULL
    OR event_month IS NULL
    OR event_day IS NULL
    OR event_year <> year
    OR event_month <> month
    OR event_day <> day
    OR event_time_utc IS NULL
    OR NOT regexp_like(
      event_time_utc,
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$'
    )
  )

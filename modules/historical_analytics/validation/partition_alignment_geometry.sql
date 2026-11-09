SELECT COUNT(*) AS misaligned_or_invalid_event_time
FROM __DATABASE__.hazard_geometry
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND (
    json_extract_scalar(json_record, '$.event_year') IS NULL
    OR json_extract_scalar(json_record, '$.event_month') IS NULL
    OR json_extract_scalar(json_record, '$.event_day') IS NULL
    OR json_extract_scalar(json_record, '$.event_year') <> year
    OR json_extract_scalar(json_record, '$.event_month') <> month
    OR json_extract_scalar(json_record, '$.event_day') <> day
    OR json_extract_scalar(json_record, '$.event_time_utc') IS NULL
    OR NOT regexp_like(
      json_extract_scalar(json_record, '$.event_time_utc'),
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$'
    )
  )

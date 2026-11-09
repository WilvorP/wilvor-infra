SELECT COUNT(*) AS invalid_geometry_rows
FROM __DATABASE__.hazard_geometry
WHERE year = '__YEAR__'
  AND month = '__MONTH__'
  AND day = '__DAY__'
  AND (
    json_record IS NULL
    OR trim(json_record) = ''
    OR try(json_parse(json_record)) IS NULL
    OR json_extract_scalar(json_record, '$.record_id') IS NULL
    OR trim(json_extract_scalar(json_record, '$.record_id')) = ''
    OR json_extract_scalar(json_record, '$.hazard_version_key') IS NULL
    OR trim(json_extract_scalar(json_record, '$.hazard_version_key')) = ''
    OR json_extract_scalar(json_record, '$.fact_schema_version') IS NULL
    OR trim(json_extract_scalar(json_record, '$.fact_schema_version')) = ''
    OR json_extract(json_record, '$.geometry') IS NULL
    OR json_format(json_extract(json_record, '$.geometry')) IS NULL
    OR json_extract_scalar(json_record, '$.geometry.type') IS NULL
    OR json_extract_scalar(json_record, '$.geometry_type') IS NULL
    OR json_extract_scalar(json_record, '$.geometry_type') NOT IN (
      'POLYGON',
      'MULTIPOLYGON'
    )
    OR json_extract_scalar(json_record, '$.geometry.type') NOT IN (
      'Polygon',
      'MultiPolygon'
    )
    OR (
      CASE json_extract_scalar(json_record, '$.geometry_type')
        WHEN 'POLYGON' THEN 'Polygon'
        WHEN 'MULTIPOLYGON' THEN 'MultiPolygon'
      END
    ) <> json_extract_scalar(json_record, '$.geometry.type')
  )

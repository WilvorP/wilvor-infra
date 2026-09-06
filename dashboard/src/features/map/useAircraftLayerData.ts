import { useMemo } from 'react';

import { useMapAircraft } from '@/hooks/useOperationalQueries';

import { decodeMapAircraft, type MapAircraftResult } from './aircraftGeoJson';

export interface AircraftLayerData {
  readonly result: MapAircraftResult;
  readonly isPending: boolean;
  readonly isError: boolean;
  readonly error: unknown;
  /** True when a refresh failed but a previous picture is still displayed. */
  readonly isStale: boolean;
}

/**
 * Aircraft layer data: the `/map/aircraft` poll plus its decode.
 *
 * Shared by the map, which needs the GeoJSON collection, and by the
 * investigation drawer, which needs the currently selected aircraft. Both read
 * the same cached response, so selecting an aircraft costs no extra request
 * and the drawer's values advance with each poll instead of freezing at the
 * moment of selection.
 */
export function useAircraftLayerData(
  options: { enabled?: boolean } = {},
): AircraftLayerData {
  const query = useMapAircraft(options);

  const result = useMemo(() => decodeMapAircraft(query.data), [query.data]);

  return {
    result,
    isPending: query.isPending && query.data === undefined,
    isError: query.isError,
    error: query.error,
    isStale: query.isError && query.data !== undefined,
  };
}

import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { useApiClient } from '@/api/apiClientContext';
import { LIMITS } from '@/api/operationalApi';
import { queryKeys } from '@/api/queryKeys';
import { REFRESH, type RefreshPolicy } from '@/config/refresh';
import type {
  ActiveHazard,
  AircraftDetailResponse,
  FreshnessResponse,
  MapAircraftResponse,
  OverviewResponse,
  PaginatedResponse,
  SystemHealthResponse,
} from '@/types/api';
import { asString } from '@/utils/coerce';

/**
 * Query hooks for the operational API.
 *
 * Every hook forwards TanStack Query's `signal` to the client so an in-flight
 * request is aborted when the component unmounts or the key changes, and reads
 * its cadence from the centralised refresh policy rather than a local literal.
 */

function withPolicy(policy: RefreshPolicy): {
  refetchInterval: number | false;
  staleTime: number;
} {
  return {
    // The test query client disables polling; hook-level intervals would
    // otherwise keep `vitest run` alive after the assertions finish.
    refetchInterval:
      import.meta.env.MODE === 'test' ? false : policy.refetchIntervalMs,
    staleTime: policy.staleTimeMs,
  };
}

export function useOverview(): UseQueryResult<OverviewResponse> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.overview(),
    queryFn: ({ signal }) => client.overview({ signal }),
    ...withPolicy(REFRESH.overview),
  });
}

export function useFreshness(): UseQueryResult<FreshnessResponse> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.freshness(),
    queryFn: ({ signal }) => client.freshness({ signal }),
    ...withPolicy(REFRESH.freshness),
  });
}

export function useSystemHealth(): UseQueryResult<SystemHealthResponse> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.systemHealth(),
    queryFn: ({ signal }) => client.systemHealth({ signal }),
    ...withPolicy(REFRESH.systemHealth),
  });
}

export interface UseActiveHazardsOptions {
  /** Page size. Capped at the API's ceiling of 100. */
  limit?: number;
}

/**
 * Active hazards for the map layer.
 *
 * Deliberately single-page. `/hazards/active` is a GSI query returning at most
 * 100 hazards per page, which comfortably covers the active SIGMET/AIRMET set;
 * `nextToken` is surfaced to callers so a truncated result can be reported to
 * the operator instead of quietly showing a partial hazard picture.
 */
export function useActiveHazards(
  options: UseActiveHazardsOptions = {},
): UseQueryResult<PaginatedResponse<ActiveHazard>> {
  const client = useApiClient();
  const limit = options.limit ?? LIMITS.hazards.max;

  return useQuery({
    queryKey: queryKeys.activeHazards({ limit }),
    queryFn: ({ signal }) => client.listActiveHazards({ limit, signal }),
    ...withPolicy(REFRESH.hazards),
  });
}

/**
 * Aircraft positions for the network map.
 *
 * Single request per poll: `/map/aircraft` returns the whole renderable fleet
 * from one server-side cached scan, so there is no pagination to drive here.
 * The raw response is returned rather than a decoded one so decoding stays a
 * pure, separately testable step in `features/map/aircraftGeoJson.ts`.
 */
export function useMapAircraft(): UseQueryResult<MapAircraftResponse> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.mapAircraft(),
    queryFn: ({ signal }) => client.mapAircraft({ signal }),
    ...withPolicy(REFRESH.aircraftMap),
  });
}

/**
 * Keep a previous aircraft-detail response only when it belongs to the same
 * airframe.
 *
 * The shared query client uses `placeholderData: previous` so panels do not
 * flash empty on refresh. That default would also show aircraft A's risk on
 * aircraft B for a frame after the selection changes, so this query overrides
 * it.
 */
export function retainAircraftDetailPlaceholder(
  selectedId: string | null,
  previousData: AircraftDetailResponse | undefined,
  previousQuery: { queryKey: readonly unknown[] } | undefined,
): AircraftDetailResponse | undefined {
  const previousId = previousQuery?.queryKey[3];

  if (selectedId !== null && previousId === selectedId) {
    return previousData;
  }

  return undefined;
}

/**
 * Investigation payload for the currently selected aircraft.
 *
 * Disabled when nothing is selected. The selected id is the query key, so
 * changing selection cancels the in-flight request and does not reuse another
 * airframe's cached detail as a placeholder.
 */
export function useAircraftDetail(
  aircraftId: string | null,
): UseQueryResult<AircraftDetailResponse> {
  const client = useApiClient();
  const id = asString(aircraftId);

  return useQuery({
    queryKey: queryKeys.aircraftDetail(id ?? ''),
    queryFn: ({ signal }) => {
      if (id === null) {
        throw new Error('Aircraft detail queried without a selection.');
      }

      return client.getAircraft(id, { signal });
    },
    enabled: id !== null,
    ...withPolicy(REFRESH.aircraftDetail),
    placeholderData: (previousData, previousQuery) =>
      retainAircraftDetailPlaceholder(id, previousData, previousQuery),
  });
}

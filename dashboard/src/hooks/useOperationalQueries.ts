import { useInfiniteQuery, useQuery, type UseQueryResult } from '@tanstack/react-query';

import { useApiClient } from '@/api/apiClientContext';
import { LIMITS } from '@/api/operationalApi';
import { queryKeys } from '@/api/queryKeys';
import { REFRESH, type RefreshPolicy } from '@/config/refresh';
import type {
  ActiveHazard,
  AircraftDetailResponse,
  AirportDetailResponse,
  FreshnessResponse,
  MapAircraftResponse,
  OverviewResponse,
  PaginatedResponse,
  SystemHealthResponse,
  CloudWatchDashboardView,
  CloudWatchViewerRange,
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

export function useHealth() {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => client.health({ signal }),
    ...withPolicy(REFRESH.systemHealth),
  });
}

export function useCloudWatchDashboard(
  dashboardId: string,
): UseQueryResult<CloudWatchDashboardView> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.cloudWatchDashboard(dashboardId),
    queryFn: ({ signal }) =>
      client.getCloudWatchDashboard(dashboardId, { signal }),
    ...withPolicy(REFRESH.cloudWatchDashboard),
  });
}

export function useCloudWatchWidgetImage(
  dashboardId: string,
  widgetId: string,
  range: CloudWatchViewerRange,
  revision: string,
  enabled: boolean,
  pixelSize?: { width: number; height: number } | null,
) {
  const client = useApiClient();
  const width = pixelSize?.width;
  const height = pixelSize?.height;

  return useQuery({
    queryKey: queryKeys.cloudWatchWidgetImage(
      dashboardId,
      widgetId,
      range,
      revision,
      width,
      height,
    ),
    queryFn: ({ signal }) =>
      client.getCloudWatchWidgetImage(dashboardId, widgetId, range, {
        signal,
        width,
        height,
      }),
    enabled,
    ...withPolicy(REFRESH.cloudWatchDashboard),
  });
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
/**
 * Current aircraft listing (`GET /aircraft`).
 *
 * Unfiltered pages are a current-state scan. `callsign` is an exact GSI match.
 * `h3Cell` is mutually exclusive with callsign. Pages are walked only when
 * the operator asks for more.
 */
export function useAircraftList(filters: {
  callsign?: string;
  h3Cell?: string;
} = {}) {
  const client = useApiClient();
  const limit = LIMITS.aircraft.max;
  const callsign = asString(filters.callsign)?.toUpperCase() ?? undefined;
  const h3Cell = asString(filters.h3Cell) ?? undefined;

  return useInfiniteQuery({
    queryKey: queryKeys.aircraftList({ limit, callsign, h3Cell }),
    queryFn: ({ pageParam, signal }) =>
      client.listAircraft({
        limit,
        nextToken: pageParam,
        callsign,
        h3Cell,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextToken ?? undefined,
    ...withPolicy(REFRESH.aircraftMap),
  });
}

export function useMapAircraft(
  options: { enabled?: boolean } = {},
): UseQueryResult<MapAircraftResponse> {
  const client = useApiClient();

  return useQuery({
    queryKey: queryKeys.mapAircraft(),
    queryFn: ({ signal }) => client.mapAircraft({ signal }),
    enabled: options.enabled ?? true,
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

export function retainAirportDetailPlaceholder(
  selectedId: string | null,
  previousData: AirportDetailResponse | undefined,
  previousQuery: { queryKey: readonly unknown[] } | undefined,
): AirportDetailResponse | undefined {
  const previousId = previousQuery?.queryKey[3];

  if (selectedId !== null && previousId === selectedId) {
    return previousData;
  }

  return undefined;
}

/**
 * Current airport listing (`GET /airports`).
 *
 * Unfiltered pages are an unexpired `AirportStatus` scan. `weatherRisk` and
 * `weatherImpact` are exact GSI matches. Pages are walked only when the
 * operator asks for more.
 */
export function useAirportList(
  filters: {
    weatherRisk?: string;
    weatherImpact?: string;
  } = {},
) {
  const client = useApiClient();
  const limit = LIMITS.airports.max;
  const weatherRisk = asString(filters.weatherRisk)?.toUpperCase() ?? undefined;
  const weatherImpact =
    asString(filters.weatherImpact)?.toUpperCase() ?? undefined;

  return useInfiniteQuery({
    queryKey: queryKeys.airportList({ limit, weatherRisk, weatherImpact }),
    queryFn: ({ pageParam, signal }) =>
      client.listAirports({
        limit,
        nextToken: pageParam,
        weatherRisk,
        weatherImpact,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextToken ?? undefined,
    ...withPolicy(REFRESH.airports),
  });
}

/**
 * Investigation payload for the currently selected airport.
 *
 * Disabled when nothing is selected. Detail is not fetched for worklist rows.
 */
export function useAirportDetail(
  airportId: string | null,
): UseQueryResult<AirportDetailResponse> {
  const client = useApiClient();
  const id = asString(airportId)?.toUpperCase() ?? null;

  return useQuery({
    queryKey: queryKeys.airportDetail(id ?? ''),
    queryFn: ({ signal }) => {
      if (id === null) {
        throw new Error('Airport detail queried without a selection.');
      }

      return client.getAirport(id, { signal });
    },
    enabled: id !== null,
    ...withPolicy(REFRESH.airportDetail),
    placeholderData: (previousData, previousQuery) =>
      retainAirportDetailPlaceholder(id, previousData, previousQuery),
  });
}

/**
 * Current encounters (`GET /encounters/active`).
 *
 * The endpoint already applies the current-set definition. Pages are walked
 * only when the operator asks for more; the API caps each page at 50.
 */
export function useCurrentEncounters() {
  const client = useApiClient();
  const limit = LIMITS.encounters.max;

  return useInfiniteQuery({
    queryKey: queryKeys.activeEncounters({ limit }),
    queryFn: ({ pageParam, signal }) =>
      client.listActiveEncounters({
        limit,
        nextToken: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextToken ?? undefined,
    ...withPolicy(REFRESH.encounters),
  });
}

/**
 * Current alerts (`GET /alerts/active`).
 *
 * After the current-set contract change this is the same population as
 * `overview.alerts.currentCount`, not retained ACTIVE+valid_until rows.
 */
export function useCurrentAlerts() {
  const client = useApiClient();
  const limit = LIMITS.alerts.max;

  return useInfiniteQuery({
    queryKey: queryKeys.activeAlerts({ limit }),
    queryFn: ({ pageParam, signal }) =>
      client.listActiveAlerts({
        limit,
        nextToken: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextToken ?? undefined,
    ...withPolicy(REFRESH.alerts),
  });
}

/**
 * Current recommendations (`GET /recommendations/active`).
 *
 * Same population as `overview.recommendations.currentCount`. Disabled until
 * the operator opens the Recommendations tab so Overview mount does not add
 * another current-set scan beside encounters and alerts.
 */
export function useCurrentRecommendations(options: { enabled?: boolean } = {}) {
  const client = useApiClient();
  const limit = LIMITS.recommendations.max;

  return useInfiniteQuery({
    queryKey: queryKeys.activeRecommendations({ limit }),
    queryFn: ({ pageParam, signal }) =>
      client.listActiveRecommendations({
        limit,
        nextToken: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextToken ?? undefined,
    enabled: options.enabled ?? true,
    ...withPolicy(REFRESH.recommendations),
  });
}

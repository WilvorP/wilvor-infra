import { OperationalApiHttpClient, type RequestOptions } from './http';
import type {
  ActiveAlert,
  ActiveEncounterItem,
  ActiveHazard,
  AircraftCurrentState,
  AircraftDetailResponse,
  AirportDetailResponse,
  AirportStatus,
  FreshnessResponse,
  HealthResponse,
  MapAircraftResponse,
  OverviewResponse,
  PaginatedResponse,
  Recommendation,
  SystemHealthResponse,
} from '@/types/api';

/**
 * Typed façade over the operational API routes.
 *
 * Route strings and parameter names mirror `local.route_keys` in
 * modules/operational_api/api.tf and the dispatch in
 * functions/operational_api/app.py. Server-side `limit` ceilings are encoded
 * here so an out-of-range request fails locally instead of returning a 400.
 */

/**
 * Note on validation: the list methods are `async` so that a rejected
 * precondition surfaces as a rejected promise rather than a synchronous throw.
 * A mixed contract would force every caller to wrap invocations in try/catch
 * *and* attach a rejection handler.
 */

/** Per-route `limit` ceilings enforced by `_parse_limit` in app.py. */
export const LIMITS = {
  aircraft: { default: 50, max: 100 },
  hazards: { default: 50, max: 100 },
  encounters: { default: 25, max: 50 },
  airports: { default: 50, max: 100 },
  recommendations: { default: 50, max: 100 },
  alerts: { default: 50, max: 100 },
} as const;

export interface PageRequest extends RequestOptions {
  limit?: number;
  nextToken?: string | null;
}

export interface AircraftListRequest extends PageRequest {
  /** Mutually exclusive with `h3Cell`; the API rejects both together. */
  callsign?: string;
  h3Cell?: string;
}

export interface AirportListRequest extends PageRequest {
  weatherRisk?: string;
  weatherImpact?: string;
}

function assertLimit(
  value: number | undefined,
  ceiling: number,
  route: string,
): void {
  if (value === undefined) {
    return;
  }

  if (!Number.isInteger(value) || value < 1 || value > ceiling) {
    throw new RangeError(
      `limit for ${route} must be an integer between 1 and ${ceiling}. ` +
        `Received ${value}.`,
    );
  }
}

export class OperationalApiClient {
  constructor(private readonly http: OperationalApiHttpClient) {}

  /** Liveness only: proves API Gateway reaches the Lambda. */
  health(options: RequestOptions = {}): Promise<HealthResponse> {
    return this.http.get<HealthResponse>('/health', options);
  }

  overview(options: RequestOptions = {}): Promise<OverviewResponse> {
    return this.http.get<OverviewResponse>('/overview', options);
  }

  freshness(options: RequestOptions = {}): Promise<FreshnessResponse> {
    return this.http.get<FreshnessResponse>('/freshness', options);
  }

  systemHealth(options: RequestOptions = {}): Promise<SystemHealthResponse> {
    return this.http.get<SystemHealthResponse>('/system-health', options);
  }

  /**
   * Compact aircraft projection for the map layer.
   *
   * Unpaginated by design: the backend returns the whole renderable fleet in
   * one cached response. Prefer this over `listAircraft` for anything drawing
   * the network, and `getAircraft` for investigating one airframe.
   */
  mapAircraft(options: RequestOptions = {}): Promise<MapAircraftResponse> {
    return this.http.get<MapAircraftResponse>('/map/aircraft', options);
  }

  async listAircraft(
    request: AircraftListRequest = {},
  ): Promise<PaginatedResponse<AircraftCurrentState>> {
    const { limit, nextToken, callsign, h3Cell, ...options } = request;

    assertLimit(limit, LIMITS.aircraft.max, '/aircraft');

    if (callsign && h3Cell) {
      throw new RangeError(
        'Use either callsign or h3Cell when listing aircraft, not both.',
      );
    }

    return this.http.get<PaginatedResponse<AircraftCurrentState>>('/aircraft', {
      ...options,
      params: { limit, nextToken, callsign, h3Cell },
    });
  }

  getAircraft(
    aircraftId: string,
    options: RequestOptions = {},
  ): Promise<AircraftDetailResponse> {
    return this.http.get<AircraftDetailResponse>(
      `/aircraft/${encodeURIComponent(aircraftId)}`,
      options,
    );
  }

  async listActiveHazards(
    request: PageRequest = {},
  ): Promise<PaginatedResponse<ActiveHazard>> {
    const { limit, nextToken, ...options } = request;

    assertLimit(limit, LIMITS.hazards.max, '/hazards/active');

    return this.http.get<PaginatedResponse<ActiveHazard>>('/hazards/active', {
      ...options,
      params: { limit, nextToken },
    });
  }

  async listActiveEncounters(
    request: PageRequest = {},
  ): Promise<PaginatedResponse<ActiveEncounterItem>> {
    const { limit, nextToken, ...options } = request;

    assertLimit(limit, LIMITS.encounters.max, '/encounters/active');

    return this.http.get<PaginatedResponse<ActiveEncounterItem>>(
      '/encounters/active',
      { ...options, params: { limit, nextToken } },
    );
  }

  async listAirports(
    request: AirportListRequest = {},
  ): Promise<PaginatedResponse<AirportStatus>> {
    const { limit, nextToken, weatherRisk, weatherImpact, ...options } =
      request;

    assertLimit(limit, LIMITS.airports.max, '/airports');

    return this.http.get<PaginatedResponse<AirportStatus>>('/airports', {
      ...options,
      params: { limit, nextToken, weatherRisk, weatherImpact },
    });
  }

  getAirport(
    airportId: string,
    options: RequestOptions = {},
  ): Promise<AirportDetailResponse> {
    return this.http.get<AirportDetailResponse>(
      `/airports/${encodeURIComponent(airportId)}`,
      options,
    );
  }

  async listActiveRecommendations(
    request: PageRequest = {},
  ): Promise<PaginatedResponse<Recommendation>> {
    const { limit, nextToken, ...options } = request;

    assertLimit(limit, LIMITS.recommendations.max, '/recommendations/active');

    return this.http.get<PaginatedResponse<Recommendation>>(
      '/recommendations/active',
      { ...options, params: { limit, nextToken } },
    );
  }

  async listActiveAlerts(
    request: PageRequest = {},
  ): Promise<PaginatedResponse<ActiveAlert>> {
    const { limit, nextToken, ...options } = request;

    assertLimit(limit, LIMITS.alerts.max, '/alerts/active');

    return this.http.get<PaginatedResponse<ActiveAlert>>('/alerts/active', {
      ...options,
      params: { limit, nextToken },
    });
  }
}

export function createOperationalApiClient(options: {
  baseUrl: string;
  timeoutMs: number;
  fetchImpl?: typeof fetch;
}): OperationalApiClient {
  return new OperationalApiClient(new OperationalApiHttpClient(options));
}

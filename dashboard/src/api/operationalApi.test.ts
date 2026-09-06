import { describe, expect, it, vi } from 'vitest';

import { createOperationalApiClient } from './operationalApi';

function stubFetch() {
  return vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ items: [], count: 0, nextToken: null }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
}

function clientWith(fetchImpl: ReturnType<typeof stubFetch>) {
  return createOperationalApiClient({
    baseUrl: 'https://api.example.test',
    timeoutMs: 5_000,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
}

function requestedUrl(fetchImpl: ReturnType<typeof stubFetch>): string {
  return fetchImpl.mock.calls[0]![0];
}

describe('OperationalApiClient routes', () => {
  it('targets the documented route paths', async () => {
    const cases: Array<[string, (c: ReturnType<typeof clientWith>) => unknown]> =
      [
        ['/overview', (c) => c.overview()],
        ['/freshness', (c) => c.freshness()],
        ['/system-health', (c) => c.systemHealth()],
        ['/map/aircraft', (c) => c.mapAircraft()],
        ['/hazards/active', (c) => c.listActiveHazards()],
        ['/encounters/active', (c) => c.listActiveEncounters()],
        ['/recommendations/active', (c) => c.listActiveRecommendations()],
        ['/alerts/active', (c) => c.listActiveAlerts()],
        ['/airports', (c) => c.listAirports()],
      ];

    for (const [path, invoke] of cases) {
      const fetchImpl = stubFetch();
      await invoke(clientWith(fetchImpl));

      expect(requestedUrl(fetchImpl)).toContain(path);
    }
  });

  it('requests the map layer without pagination parameters', async () => {
    // `/map/aircraft` returns the whole renderable fleet in one response;
    // sending `limit` or `nextToken` would be rejected as unknown parameters.
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).mapAircraft();

    expect(requestedUrl(fetchImpl)).toBe(
      'https://api.example.test/map/aircraft',
    );
  });

  it('does not route the map layer through the aircraft detail path', async () => {
    // `/map/aircraft` and `/aircraft/{id}` are distinct routes in the Lambda
    // dispatch; confusing them would return a 404 for the layer.
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).mapAircraft();

    expect(requestedUrl(fetchImpl)).not.toContain('/aircraft/map');
  });

  it('percent-encodes path parameters', async () => {
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).getAircraft('a b/c');

    expect(requestedUrl(fetchImpl)).toBe(
      'https://api.example.test/aircraft/a%20b%2Fc',
    );
  });

  it('returns the aircraft-detail envelope without reshaping fields', async () => {
    const body = {
      aircraft: { aircraft_id: 'a174bf', callsign: 'UAL123' },
      projection: { projection_id: 'projection-1', confidence: 'HIGH' },
      projectionPoints: [{ point_sequence_number: 1, latitude: 40.1 }],
      recentEncounters: [{ encounter_id: 'enc-1', altitude_overlap_status: 'UNKNOWN' }],
      recentRisks: [{ risk_id: 'risk-1', risk_score: 82, risk_level: 'HIGH' }],
      recentRecommendations: [
        { recommendation_id: 'rec-1', primary_action_type: 'EVALUATE_DIVERSION' },
      ],
      recentAlerts: [{ alert_id: 'alert-1', alert_state: 'NEW' }],
    };
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const result = await clientWith(fetchImpl).getAircraft('a174bf');

    expect(requestedUrl(fetchImpl)).toBe(
      'https://api.example.test/aircraft/a174bf',
    );
    expect(result).toEqual(body);
    expect(result.recentEncounters?.[0]?.altitude_overlap_status).toBe(
      'UNKNOWN',
    );
  });

  it('forwards airport weather filters and an opaque nextToken', async () => {
    const token = 'eyJhaXJwb3J0X2lkIjp7IlMiOiJLREVOIn19';
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).listAirports({
      limit: 50,
      nextToken: token,
      weatherRisk: 'HIGH',
      weatherImpact: 'WEATHER_IMPACTED',
    });

    const url = requestedUrl(fetchImpl);

    expect(url).toContain('/airports');
    expect(url).toContain('limit=50');
    expect(url).toContain('weatherRisk=HIGH');
    expect(url).toContain('weatherImpact=WEATHER_IMPACTED');
    expect(url).toContain(`nextToken=${encodeURIComponent(token)}`);
  });

  it('returns the airport-detail envelope without reshaping fields', async () => {
    const body = {
      airport: { airport_id: 'KDEN', weather_impact_status: 'NORMAL' },
      metar: { station_id: 'KDEN', flight_category: 'VFR' },
      taf: { station_id: 'KDEN', forecast_period_count: 2 },
      tafForecastPeriods: [{ period_id: 'p1', change_type: 'BASE' }],
      recentAssessments: [{ airport_assessment_id: 'aa#1' }],
    };
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const result = await clientWith(fetchImpl).getAirport('kden');

    expect(requestedUrl(fetchImpl)).toBe(
      'https://api.example.test/airports/kden',
    );
    expect(result).toEqual(body);
  });

  it('forwards aircraft filters as query parameters', async () => {
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).listAircraft({ limit: 25, callsign: 'UAL123' });

    const url = requestedUrl(fetchImpl);

    expect(url).toContain('limit=25');
    expect(url).toContain('callsign=UAL123');
  });
});

describe('OperationalApiClient guard rails', () => {
  it('rejects a limit above the per-route server ceiling', async () => {
    const fetchImpl = stubFetch();
    const client = clientWith(fetchImpl);

    // `/encounters/active` caps at 50, unlike the other list routes at 100.
    await expect(client.listActiveEncounters({ limit: 51 })).rejects.toThrow(
      RangeError,
    );
    await expect(client.listAircraft({ limit: 101 })).rejects.toThrow(
      RangeError,
    );

    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('rejects a non-integer or zero limit', async () => {
    const client = clientWith(stubFetch());

    await expect(client.listAircraft({ limit: 0 })).rejects.toThrow(RangeError);
    await expect(client.listAircraft({ limit: 2.5 })).rejects.toThrow(
      RangeError,
    );
  });

  it('forwards an opaque nextToken unchanged on current list routes', async () => {
    const token = 'eyJvZmZzZXQiOnsiTiI6IjIifX0=';
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).listActiveEncounters({
      limit: 50,
      nextToken: token,
    });
    await clientWith(fetchImpl).listActiveAlerts({
      limit: 100,
      nextToken: token,
    });
    await clientWith(fetchImpl).listActiveRecommendations({
      limit: 100,
      nextToken: token,
    });

    expect(fetchImpl.mock.calls[0]![0]).toContain(
      `nextToken=${encodeURIComponent(token)}`,
    );
    expect(fetchImpl.mock.calls[1]![0]).toContain(
      `nextToken=${encodeURIComponent(token)}`,
    );
    expect(fetchImpl.mock.calls[2]![0]).toContain(
      `nextToken=${encodeURIComponent(token)}`,
    );
  });

  it('omits nextToken when the caller has no cursor', async () => {
    const fetchImpl = stubFetch();

    await clientWith(fetchImpl).listActiveAlerts({ nextToken: null });

    expect(requestedUrl(fetchImpl)).not.toContain('nextToken');
  });

  it('accepts a limit at the ceiling', async () => {
    const fetchImpl = stubFetch();

    await expect(
      clientWith(fetchImpl).listActiveEncounters({ limit: 50 }),
    ).resolves.toBeDefined();
  });

  it('rejects combining callsign and h3Cell, which the API forbids', async () => {
    const fetchImpl = stubFetch();

    await expect(
      clientWith(fetchImpl).listAircraft({
        callsign: 'UAL123',
        h3Cell: '8428347ffffffff',
      }),
    ).rejects.toThrow(RangeError);

    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

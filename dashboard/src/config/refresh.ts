/**
 * Centralised polling cadence for every operational view.
 *
 * These are deliberately in one place so refresh behaviour can be reasoned
 * about as a whole (AWS cost, API Gateway throttling, Lambda concurrency)
 * rather than being rediscovered as magic numbers across features.
 *
 * Backend context that constrains these values:
 *   - `/overview` and `/system-health` are cached inside the Lambda for 45s
 *     and 15s respectively, and `/freshness` for 15s
 *     (functions/operational_api/repository.py). Polling faster than the cache
 *     window costs invocations without producing newer data.
 *   - The dev API Gateway stage throttles at 25 req/s with the operational API
 *     Lambda pinned to `reserved_concurrent_executions = 2`
 *     (envs/dev/main.tf), so aggregate polling must stay modest.
 */

const SECOND = 1_000;

export interface RefreshPolicy {
  /** Poll interval while the view is mounted and the tab is visible. */
  readonly refetchIntervalMs: number;
  /** Age at which cached data is considered stale and eligible for refetch. */
  readonly staleTimeMs: number;
}

function policy(intervalSeconds: number): RefreshPolicy {
  return {
    refetchIntervalMs: intervalSeconds * SECOND,
    // Treating data as stale slightly before the next poll keeps remounts and
    // window refocus responsive without triggering a request per render.
    staleTimeMs: Math.max(0, intervalSeconds * SECOND - 2 * SECOND),
  };
}

export const REFRESH = {
  /** Network map aircraft layer. */
  aircraftMap: policy(20),
  /** Operations overview KPIs. Matches the 20s server-side overview cache. */
  overview: policy(20),
  /** Active aircraft/hazard encounters. */
  encounters: policy(20),
  /** Active alerts feed. */
  alerts: policy(20),
  /** Active recommendations feed. */
  recommendations: policy(20),
  /** Active hazard geometry. Hazards change far more slowly than aircraft. */
  hazards: policy(45),
  /** Airport operational status list. */
  airports: policy(45),
  /** Source freshness strip. */
  freshness: policy(30),
  /** Platform and pipeline health. */
  systemHealth: policy(60),
  /**
   * Selected CloudWatch dashboard only. Images share this cadence so
   * unselected dashboards are never pre-rendered.
   */
  cloudWatchDashboard: policy(60),
  /** Currently selected aircraft investigation detail. */
  aircraftDetail: policy(12),
  /** Currently selected airport investigation detail. */
  airportDetail: policy(20),
} as const satisfies Record<string, RefreshPolicy>;

export type RefreshKey = keyof typeof REFRESH;

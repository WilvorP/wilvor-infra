import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { AirportStatus } from '@/types/api';
import { asString } from '@/utils/coerce';
import { formatUtcTime } from '@/utils/format';
import { presentRiskLevel, presentWeatherImpact } from '@/utils/status';

import styles from './FeedPanel.module.css';

export interface ImpactedAirportsPanelProps {
  airports: readonly AirportStatus[] | undefined;
  loading: boolean;
  failed: boolean;
}

/**
 * Airports with elevated weather risk or impact, from `overview.airports.topImpacted`.
 *
 * These records are a projected subset of `AirportStatus`; only the attributes
 * listed in the overview's `ProjectionExpression` are present, so nothing else
 * may be read from them here.
 */
export function ImpactedAirportsPanel({
  airports,
  loading,
  failed,
}: ImpactedAirportsPanelProps) {
  const items = airports ?? [];

  return (
    <Panel title="Weather-impacted airports" meta={failed ? undefined : `top ${items.length}`}>
      {loading ? <LoadingState label="Loading airport status" /> : null}

      {!loading && failed ? (
        <EmptyState
          title="Airport status unavailable"
          detail="The overview request failed, so airport weather impact cannot be shown."
        />
      ) : null}

      {!loading && !failed && items.length === 0 ? (
        <EmptyState
          title="No impacted airports"
          detail="No reporting airport currently has elevated weather risk or impact."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((airport, index) => (
            <li
              key={asString(airport.airport_id) ?? `airport-${index}`}
              className={styles.row}
            >
              <div className={styles.rowHeader}>
                <span className={styles.primary}>
                  {asString(airport.airport_id) ?? '????'}
                </span>
                <StatusPill
                  size="sm"
                  prefix="Wx risk"
                  presentation={presentRiskLevel(airport.weather_risk_level)}
                />
                <span className={`${styles.timestamp} wv-numeric`}>
                  {formatUtcTime(airport.updated_at_utc)}
                </span>
              </div>

              <div className={styles.rowBody}>
                <StatusPill
                  size="sm"
                  presentation={presentWeatherImpact(
                    airport.weather_impact_status,
                  )}
                />
                <span className={styles.secondary}>
                  {airport.is_diversion_weather_ready === true
                    ? 'Diversion weather ready'
                    : 'Not diversion weather ready'}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

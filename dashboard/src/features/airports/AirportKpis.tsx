import { KpiTile } from '@/components/KpiTile';
import type { OverviewResponse } from '@/types/api';
import { asNumber } from '@/utils/coerce';
import { formatCount } from '@/utils/format';

import { AIRPORT_KPI_FILTER } from './airportList';
import styles from './AirportKpis.module.css';

export type AirportKpiSelection = 'all' | 'impacted' | 'highRisk' | 'unknownRisk';

export interface AirportKpisProps {
  data: OverviewResponse | undefined;
  weatherRisk?: string;
  weatherImpact?: string;
  onKpiSelect?: (selection: AirportKpiSelection) => void;
  stale?: boolean;
}

const MISSING = '—';

function count(value: unknown): string {
  const numeric = asNumber(value);

  return numeric === null ? MISSING : formatCount(numeric);
}

/**
 * Network airport counts from `GET /overview`.
 *
 * Tile values are server-side overview counts. Clicking a tile writes the
 * existing `/airports` weatherRisk / weatherImpact filters, which the API
 * does support. KPI clicks are exclusive: one weather dimension at a time.
 */
export function AirportKpis({
  data,
  weatherRisk = '',
  weatherImpact = '',
  onKpiSelect,
  stale = false,
}: AirportKpisProps) {
  const airports = data?.airports;

  return (
    <div className={styles.grid}>
      <KpiTile
        label="Monitored"
        value={count(airports?.currentCount)}
        unit="current airports"
        stale={stale}
        active={weatherRisk === '' && weatherImpact === ''}
        onSelect={onKpiSelect ? () => onKpiSelect('all') : undefined}
      />
      <KpiTile
        label="Weather impacted"
        value={count(airports?.weatherImpactedCount)}
        unit="stored impact"
        stale={stale}
        active={
          weatherImpact === AIRPORT_KPI_FILTER.impacted && weatherRisk === ''
        }
        onSelect={onKpiSelect ? () => onKpiSelect('impacted') : undefined}
      />
      <KpiTile
        label="High weather risk"
        value={count(airports?.byWeatherRisk?.HIGH)}
        unit="stored risk"
        stale={stale}
        active={
          weatherRisk === AIRPORT_KPI_FILTER.highRisk && weatherImpact === ''
        }
        onSelect={onKpiSelect ? () => onKpiSelect('highRisk') : undefined}
      />
      <KpiTile
        label="Unknown weather risk"
        value={count(airports?.byWeatherRisk?.UNKNOWN)}
        unit="not Normal"
        stale={stale}
        active={
          weatherRisk === AIRPORT_KPI_FILTER.unknownRisk && weatherImpact === ''
        }
        onSelect={onKpiSelect ? () => onKpiSelect('unknownRisk') : undefined}
      />
    </div>
  );
}

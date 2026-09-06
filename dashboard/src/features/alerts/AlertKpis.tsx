import { KpiTile } from '@/components/KpiTile';
import type { OverviewResponse } from '@/types/api';
import { asNumber } from '@/utils/coerce';
import { formatCount } from '@/utils/format';

import { ALERT_STATE_KPI, type AlertStateKpi } from './alertList';
import styles from './AlertKpis.module.css';

export interface AlertKpisProps {
  data: OverviewResponse | undefined;
  loadedStates?: {
    new: number;
    updated: number;
    escalated: number;
    monitoring: number;
  };
  stateFilter?: string;
  onStateFilterChange?: (state: AlertStateKpi) => void;
  problem?: string;
  stale?: boolean;
}

const MISSING = '—';

function count(value: unknown): string {
  const numeric = asNumber(value);

  return numeric === null ? MISSING : formatCount(numeric);
}

/**
 * Network current total from `GET /overview`.
 *
 * State tiles are loaded-page counts only. Overview `byState` is retained
 * history, not the current set. Clicking a tile writes the existing worklist
 * state filter; it does not query the server.
 */
export function AlertKpis({
  data,
  loadedStates,
  stateFilter = '',
  onStateFilterChange,
  problem,
  stale = false,
}: AlertKpisProps) {
  return (
    <div className={styles.grid}>
      <KpiTile
        label="Current Alerts"
        value={count(data?.alerts?.currentCount)}
        unit="current"
        problem={problem}
        stale={stale}
        active={stateFilter === ALERT_STATE_KPI.all}
        onSelect={
          onStateFilterChange
            ? () => onStateFilterChange(ALERT_STATE_KPI.all)
            : undefined
        }
      />
      <KpiTile
        label="New"
        value={loadedStates ? formatCount(loadedStates.new) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={stateFilter === ALERT_STATE_KPI.new}
        onSelect={
          onStateFilterChange
            ? () => onStateFilterChange(ALERT_STATE_KPI.new)
            : undefined
        }
      />
      <KpiTile
        label="Updated"
        value={loadedStates ? formatCount(loadedStates.updated) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={stateFilter === ALERT_STATE_KPI.updated}
        onSelect={
          onStateFilterChange
            ? () => onStateFilterChange(ALERT_STATE_KPI.updated)
            : undefined
        }
      />
      <KpiTile
        label="Escalated"
        value={loadedStates ? formatCount(loadedStates.escalated) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={stateFilter === ALERT_STATE_KPI.escalated}
        onSelect={
          onStateFilterChange
            ? () => onStateFilterChange(ALERT_STATE_KPI.escalated)
            : undefined
        }
      />
      <KpiTile
        label="Monitoring"
        value={loadedStates ? formatCount(loadedStates.monitoring) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={stateFilter === ALERT_STATE_KPI.monitoring}
        onSelect={
          onStateFilterChange
            ? () => onStateFilterChange(ALERT_STATE_KPI.monitoring)
            : undefined
        }
      />
    </div>
  );
}

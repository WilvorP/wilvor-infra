import { KpiTile } from '@/components/KpiTile';
import type { OverviewResponse } from '@/types/api';
import { asNumber } from '@/utils/coerce';
import { formatCount } from '@/utils/format';

import {
  RECOMMENDATION_ACTION_KPI,
  type RecommendationActionKpi,
} from './recommendationList';
import styles from './RecommendationKpis.module.css';

export interface RecommendationKpisProps {
  data: OverviewResponse | undefined;
  loadedActions?: {
    monitor: number;
    prepare: number;
    diversion: number;
  };
  actionFilter?: string;
  onActionFilterChange?: (action: RecommendationActionKpi) => void;
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
 * Action tiles are loaded-page counts only. Overview does not return
 * MONITOR / PREPARE / DIVERSION totals for the full current set. Clicking a
 * tile writes the existing worklist action filter; it does not query the
 * server.
 */
export function RecommendationKpis({
  data,
  loadedActions,
  actionFilter = '',
  onActionFilterChange,
  problem,
  stale = false,
}: RecommendationKpisProps) {
  return (
    <div className={styles.grid}>
      <KpiTile
        label="Current Recommendations"
        value={count(data?.recommendations?.currentCount)}
        unit="current"
        problem={problem}
        stale={stale}
        active={actionFilter === RECOMMENDATION_ACTION_KPI.all}
        onSelect={
          onActionFilterChange
            ? () => onActionFilterChange(RECOMMENDATION_ACTION_KPI.all)
            : undefined
        }
      />
      <KpiTile
        label="Monitor"
        value={loadedActions ? formatCount(loadedActions.monitor) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={actionFilter === RECOMMENDATION_ACTION_KPI.monitor}
        onSelect={
          onActionFilterChange
            ? () => onActionFilterChange(RECOMMENDATION_ACTION_KPI.monitor)
            : undefined
        }
      />
      <KpiTile
        label="Monitor and prepare"
        value={loadedActions ? formatCount(loadedActions.prepare) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={actionFilter === RECOMMENDATION_ACTION_KPI.prepare}
        onSelect={
          onActionFilterChange
            ? () => onActionFilterChange(RECOMMENDATION_ACTION_KPI.prepare)
            : undefined
        }
      />
      <KpiTile
        label="Evaluate diversion"
        value={loadedActions ? formatCount(loadedActions.diversion) : MISSING}
        unit="loaded pages"
        problem={problem}
        stale={stale}
        active={actionFilter === RECOMMENDATION_ACTION_KPI.diversion}
        onSelect={
          onActionFilterChange
            ? () => onActionFilterChange(RECOMMENDATION_ACTION_KPI.diversion)
            : undefined
        }
      />
    </div>
  );
}

import { KpiTile } from '@/components/KpiTile';
import type { OverviewResponse } from '@/types/api';
import { asNumber } from '@/utils/coerce';
import { formatCount } from '@/utils/format';

import { ENCOUNTER_RISK_KPI, type EncounterRiskKpi } from './encounterList';
import styles from './EncounterKpis.module.css';

export interface EncounterKpisProps {
  data: OverviewResponse | undefined;
  riskFilter?: string;
  onRiskFilterChange?: (riskLevel: EncounterRiskKpi) => void;
  problem?: string;
  stale?: boolean;
}

const MISSING = '—';

function count(value: unknown): string {
  const numeric = asNumber(value);

  return numeric === null ? MISSING : formatCount(numeric);
}

/**
 * Current-set encounter totals from `GET /overview`.
 *
 * Tile values are network totals, not loaded-page sums. Clicking a tile
 * writes the existing worklist risk filter; `/encounters/active` has no
 * risk query parameter.
 */
export function EncounterKpis({
  data,
  riskFilter = '',
  onRiskFilterChange,
  problem,
  stale = false,
}: EncounterKpisProps) {
  const encounters = data?.encounters;

  return (
    <div className={styles.grid}>
      <KpiTile
        label="Current Encounters"
        value={count(encounters?.activeCount)}
        unit="current"
        problem={problem}
        stale={stale}
        active={riskFilter === ENCOUNTER_RISK_KPI.all}
        onSelect={
          onRiskFilterChange
            ? () => onRiskFilterChange(ENCOUNTER_RISK_KPI.all)
            : undefined
        }
      />
      <KpiTile
        label="LOW"
        value={count(encounters?.lowRiskCount)}
        unit="stored risk"
        problem={problem}
        stale={stale}
        active={riskFilter === ENCOUNTER_RISK_KPI.low}
        onSelect={
          onRiskFilterChange
            ? () => onRiskFilterChange(ENCOUNTER_RISK_KPI.low)
            : undefined
        }
      />
      <KpiTile
        label="MEDIUM"
        value={count(encounters?.mediumRiskCount)}
        unit="stored risk"
        problem={problem}
        stale={stale}
        active={riskFilter === ENCOUNTER_RISK_KPI.medium}
        onSelect={
          onRiskFilterChange
            ? () => onRiskFilterChange(ENCOUNTER_RISK_KPI.medium)
            : undefined
        }
      />
      <KpiTile
        label="HIGH"
        value={count(encounters?.highRiskCount)}
        unit="stored risk"
        problem={problem}
        stale={stale}
        active={riskFilter === ENCOUNTER_RISK_KPI.high}
        onSelect={
          onRiskFilterChange
            ? () => onRiskFilterChange(ENCOUNTER_RISK_KPI.high)
            : undefined
        }
      />
    </div>
  );
}

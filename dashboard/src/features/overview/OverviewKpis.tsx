import { KpiTile, type KpiBreakdownEntry } from '@/components/KpiTile';
import type { OverviewResponse } from '@/types/api';
import { asNumber } from '@/utils/coerce';
import { formatCount, NOT_REPORTED } from '@/utils/format';

import styles from './OverviewKpis.module.css';

export interface OverviewKpisProps {
  data: OverviewResponse | undefined;
  /** Rendered in place of values when the overview could not be loaded. */
  problem?: string;
  /** Values are from an earlier successful refresh. */
  stale?: boolean;
}

const MISSING = NOT_REPORTED;

function count(value: unknown): string {
  const numeric = asNumber(value);

  return numeric === null ? MISSING : formatCount(numeric);
}

/**
 * Network KPI strip driven entirely by `GET /overview`.
 *
 * Every figure is read straight from the response. No totals are derived or
 * inferred in the browser: the backend already counts these with DynamoDB
 * `Select=COUNT` queries, and recomputing them here would risk disagreeing
 * with the authoritative figure.
 */
export function OverviewKpis({ data, problem, stale }: OverviewKpisProps) {
  const encounters = data?.encounters;
  const alerts = data?.alerts;
  const airports = data?.airports;

  const encounterBreakdown: KpiBreakdownEntry[] = [
    {
      label: 'High',
      value: count(encounters?.highRiskCount),
      tone: 'high',
    },
    {
      label: 'Med',
      value: count(encounters?.mediumRiskCount),
      tone: 'medium',
    },
    { label: 'Low', value: count(encounters?.lowRiskCount), tone: 'low' },
  ];

  return (
    <div className={styles.grid}>
      <KpiTile
        variant="readout"
        label="Aircraft"
        value={count(data?.aircraft?.activeCount)}
        unit="tracked"
        problem={problem}
        stale={stale}
      />

      <KpiTile
        variant="readout"
        label="Active hazards"
        value={count(data?.hazards?.activeCount)}
        unit="SIGMET / AIRMET"
        problem={problem}
        stale={stale}
      />

      <KpiTile
        variant="readout"
        label="Current encounters"
        value={count(encounters?.activeCount)}
        breakdown={problem ? undefined : encounterBreakdown}
        problem={problem}
        stale={stale}
      />

      <KpiTile
        variant="readout"
        label="Current recommendations"
        value={count(data?.recommendations?.currentCount)}
        problem={problem}
        stale={stale}
      />

      <KpiTile
        variant="readout"
        label="Current alerts"
        value={count(alerts?.currentCount)}
        problem={problem}
        stale={stale}
      />

      <KpiTile
        variant="readout"
        label="Airports"
        value={count(airports?.currentCount)}
        unit="reporting"
        breakdown={
          problem
            ? undefined
            : [
                {
                  label: 'Wx impacted',
                  value: count(airports?.weatherImpactedCount),
                  tone: 'high',
                },
              ]
        }
        problem={problem}
        stale={stale}
      />
    </div>
  );
}

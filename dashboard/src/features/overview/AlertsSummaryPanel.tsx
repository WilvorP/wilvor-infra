import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { asCountMap } from '@/utils/coerce';
import { formatCount } from '@/utils/format';
import { presentAlertState } from '@/utils/status';

import styles from './FeedPanel.module.css';

export interface AlertsSummaryPanelProps {
  activeCount: number | null | undefined;
  byState: Record<string, number> | null | undefined;
  loading: boolean;
  failed: boolean;
}

/**
 * Active alert counts by lifecycle state.
 *
 * `/overview` returns state counts only; the individual alert objects come
 * from `GET /alerts/active`, which the dedicated Alerts workflow will consume.
 * That limitation is stated in the panel rather than filled with placeholder
 * rows.
 */
export function AlertsSummaryPanel({
  activeCount,
  byState,
  loading,
  failed,
}: AlertsSummaryPanelProps) {
  const states = asCountMap(byState);
  const entries = Object.entries(states).sort(([a], [b]) => a.localeCompare(b));

  return (
    <Panel
      title="Active alerts"
      meta={failed || activeCount == null ? undefined : formatCount(activeCount)}
    >
      {loading ? <LoadingState label="Loading alert states" /> : null}

      {!loading && failed ? (
        <EmptyState
          title="Alert summary unavailable"
          detail="The overview request failed, so alert state counts cannot be shown."
        />
      ) : null}

      {!loading && !failed && entries.length === 0 ? (
        <EmptyState
          title="No active alerts"
          detail="No alert is currently in a NEW, MONITORING, ESCALATED or UPDATED state."
        />
      ) : null}

      {!loading && !failed && entries.length > 0 ? (
        <div className={styles.stateGrid}>
          {entries.map(([state, value]) => (
            <div key={state} className={styles.stateCell}>
              <StatusPill size="sm" presentation={presentAlertState(state)} />
              <span className={`${styles.stateValue} wv-numeric`}>
                {formatCount(value)}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {!loading && !failed && entries.length > 0 ? (
        <div className={styles.footnote}>
          <Notice>
            Counts only. Individual alerts, including their message and
            supporting evidence, arrive with the Alerts workflow.
          </Notice>
        </div>
      ) : null}
    </Panel>
  );
}

import { useQueryClient } from '@tanstack/react-query';

import { describeApiError } from '@/api/errors';
import { queryKeys } from '@/api/queryKeys';
import { EmptyState, LoadingState } from '@/components/QueryState';
import {
  CLOUDWATCH_TIME_RANGES,
  cloudWatchConsoleUrl,
  type CloudWatchDashboardEntry,
  type CloudWatchTimeRange,
} from '@/config/cloudwatchDashboards';
import { useCloudWatchDashboard } from '@/hooks/useOperationalQueries';
import { asString } from '@/utils/coerce';

import { CloudWatchWidgetCell } from './CloudWatchWidgetCell';
import styles from './CloudWatchViewer.module.css';

export interface CloudWatchViewerProps {
  dashboard: CloudWatchDashboardEntry;
  range: CloudWatchTimeRange;
  invalidRequestedId?: string | null;
  onRangeChange: (range: CloudWatchTimeRange) => void;
}

export function CloudWatchViewer({
  dashboard,
  range,
  invalidRequestedId = null,
  onRangeChange,
}: CloudWatchViewerProps) {
  const consoleUrl = cloudWatchConsoleUrl(dashboard.name);
  const queryClient = useQueryClient();
  const view = useCloudWatchDashboard(dashboard.id);
  const revision = asString(view.data?.revision) ?? 'pending';
  const widgets = view.data?.widgets ?? [];

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.cloudWatchDashboard(dashboard.id),
    });
    void queryClient.invalidateQueries({
      queryKey: [...queryKeys.all, 'cloudwatch-widget-image', dashboard.id],
    });
  };

  return (
    <section
      className={styles.viewer}
      aria-label="Selected CloudWatch dashboard"
      data-testid="cloudwatch-viewer"
      data-dashboard-id={dashboard.id}
    >
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>{dashboard.label}</h2>
          <p className={`${styles.name} wv-numeric`}>{dashboard.name}</p>
          <p className={styles.description}>{dashboard.description}</p>
        </div>
        <div className={styles.controls}>
          <div
            className={styles.ranges}
            role="group"
            aria-label="CloudWatch time range"
          >
            {CLOUDWATCH_TIME_RANGES.map((token) => (
              <button
                key={token}
                type="button"
                aria-pressed={token === range}
                className={`${styles.range} ${token === range ? styles.rangeActive : ''}`}
                onClick={() => onRangeChange(token)}
              >
                {token}
              </button>
            ))}
          </div>
          <button type="button" className={styles.refresh} onClick={refresh}>
            Refresh
          </button>
          <a
            className={styles.open}
            href={consoleUrl}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${dashboard.name} in CloudWatch`}
          >
            Open in CloudWatch ↗
          </a>
        </div>
      </header>

      {invalidRequestedId ? (
        <p className={styles.note}>
          Unknown dashboard &quot;{invalidRequestedId}&quot;. Showing{' '}
          {dashboard.label}.
        </p>
      ) : null}

      {view.isPending && view.data === undefined ? (
        <LoadingState label="Loading CloudWatch dashboard" />
      ) : null}

      {view.isError && view.data === undefined ? (
        <EmptyState
          title="Unable to load this CloudWatch dashboard"
          detail={describeApiError(view.error)}
        >
          <button type="button" className={styles.refresh} onClick={refresh}>
            Retry
          </button>
          <a className={styles.open} href={consoleUrl} target="_blank" rel="noreferrer">
            Open {dashboard.name} in CloudWatch ↗
          </a>
        </EmptyState>
      ) : null}

      {view.data ? (
        <div
          className={styles.grid}
          data-testid="cloudwatch-grid"
          style={{
            gridTemplateColumns: `repeat(${view.data.gridColumns ?? 24}, minmax(0, 1fr))`,
          }}
        >
          {widgets.map((widget, index) => (
            <CloudWatchWidgetCell
              key={asString(widget.id) ?? `widget-${index}`}
              catalog={dashboard}
              widget={widget}
              range={range}
              revision={revision}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

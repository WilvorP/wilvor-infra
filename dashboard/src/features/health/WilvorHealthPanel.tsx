import { DataField, DataFieldGrid } from '@/components/DataField';
import { KpiTile } from '@/components/KpiTile';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { describeApiError } from '@/api/errors';
import {
  useFreshness,
  useHealth,
  useSystemHealth,
} from '@/hooks/useOperationalQueries';
import type { CloudWatchAlarmSummary } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import { formatAge, formatCount, formatUtcDateTime, NOT_REPORTED } from '@/utils/format';
import { presentFreshness, presentSystemStatus } from '@/utils/status';

import { summariseFreshness } from './freshness';
import styles from './WilvorHealthPanel.module.css';

/**
 * Wilvor-native operational health.
 *
 * Values come only from GET /health, /freshness and /system-health.
 * Missing fields render as — / Unavailable. React does not score health.
 */
export function WilvorHealthPanel() {
  const api = useHealth();
  const freshness = useFreshness();
  const platform = useSystemHealth();
  const sources = summariseFreshness(freshness.data);

  const apiFailed = api.isError && api.data === undefined;
  const freshnessFailed = freshness.isError && freshness.data === undefined;
  const platformFailed = platform.isError && platform.data === undefined;
  const freshnessStale = freshness.isError && freshness.data !== undefined;
  const platformStale = platform.isError && platform.data !== undefined;

  const alarmCount = asNumber(platform.data?.cloudWatch?.activeAlarmCount);
  const alarms = platform.data?.cloudWatch?.activeAlarms ?? [];
  const problemSources = platform.data?.dataFreshness?.problemSources ?? [];
  const concurrency = asNumber(
    platform.data?.lambda?.recent?.concurrencyUtilizationPercent,
  );
  const throttles = asNumber(
    platform.data?.lambda?.operationalApi?.throttlesLast5Minutes,
  );

  return (
    <section
      className={styles.panel}
      aria-label="Wilvor health"
      data-testid="wilvor-health"
    >
      <header className={styles.header}>
        <h1 className={styles.title}>System Health</h1>
        <p className={styles.lede}>
          Wilvor-native operational health from /health, /freshness and
          /system-health. CloudWatch graphs stay in the observability catalog
          below.
        </p>
      </header>

      <div className={styles.kpis}>
        <KpiTile
          label="Overall"
          value={presentSystemStatus(platform.data?.status).label}
          unit="platform"
          problem={platformFailed ? describeApiError(platform.error) : undefined}
          stale={platformStale}
        />
        <KpiTile
          label="API"
          value={asString(api.data?.status)?.toUpperCase() ?? NOT_REPORTED}
          unit={asString(api.data?.service) ?? 'liveness'}
          problem={apiFailed ? describeApiError(api.error) : undefined}
        />
        <KpiTile
          label="Data freshness"
          value={
            asString(platform.data?.dataFreshness?.status) ??
            (freshnessFailed ? NOT_REPORTED : '—')
          }
          unit={
            problemSources.length > 0
              ? `${problemSources.length} problem source${problemSources.length === 1 ? '' : 's'}`
              : 'sources'
          }
          problem={
            platformFailed && freshnessFailed
              ? describeApiError(freshness.error)
              : undefined
          }
          stale={freshnessStale || platformStale}
        />
        <KpiTile
          label="Active alarms"
          value={alarmCount === null ? NOT_REPORTED : formatCount(alarmCount)}
          unit="CloudWatch ALARM"
          problem={platformFailed ? describeApiError(platform.error) : undefined}
          stale={platformStale}
        />
      </div>

      {apiFailed ? (
        <Notice tone="warning">
          API liveness (/health) is unavailable. {describeApiError(api.error)}
        </Notice>
      ) : null}
      {freshnessFailed ? (
        <Notice tone="warning">
          Source freshness is unavailable. {describeApiError(freshness.error)}
        </Notice>
      ) : null}
      {platformFailed ? (
        <Notice tone="warning">
          Platform health (/system-health) is unavailable.{' '}
          {describeApiError(platform.error)}
        </Notice>
      ) : null}

      <div className={styles.grid}>
        <section className={styles.block}>
          <h2 className={styles.heading}>Sources</h2>
          {freshness.isPending && freshness.data === undefined ? (
            <LoadingState label="Loading source freshness" />
          ) : (
            <ul className={styles.sourceList}>
              {sources.sources.map((source) => (
                <li
                  key={source.key}
                  className={styles.sourceRow}
                  data-testid={`source-${source.key}`}
                >
                  <StatusPill
                    size="sm"
                    prefix={source.label}
                    presentation={presentFreshness(
                      source.status ?? 'UNAVAILABLE',
                    )}
                  />
                  <span className={`${styles.age} wv-numeric`}>
                    {source.ageSeconds === null
                      ? NOT_REPORTED
                      : formatAge(source.ageSeconds)}
                  </span>
                  <span className={`${styles.when} wv-numeric`}>
                    {source.latestAt
                      ? formatUtcDateTime(source.latestAt)
                      : NOT_REPORTED}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {sources.sources.find((source) => source.key === 'sigmet')?.note ? (
            <p className={styles.note}>
              {sources.sources.find((source) => source.key === 'sigmet')?.note}
            </p>
          ) : null}
        </section>

        <section className={styles.block}>
          <h2 className={styles.heading}>Components</h2>
          {platform.isPending && platform.data === undefined ? (
            <LoadingState label="Loading platform health" />
          ) : platformFailed ? (
            <EmptyState
              title="Platform components unavailable"
              detail="Lambda capacity and alarm details come from GET /system-health."
            />
          ) : (
            <DataFieldGrid columns={2}>
              <DataField
                label="Concurrency limit"
                value={formatOptionalCount(
                  platform.data?.lambda?.account?.concurrencyLimit,
                )}
                numeric
              />
              <DataField
                label="Unreserved"
                value={formatOptionalCount(
                  platform.data?.lambda?.account?.unreservedConcurrency,
                )}
                numeric
              />
              <DataField
                label="Reserved"
                value={formatOptionalCount(
                  platform.data?.lambda?.account?.reservedConcurrency,
                )}
                numeric
              />
              <DataField
                label="Max concurrent (5m)"
                value={formatOptionalCount(
                  platform.data?.lambda?.recent?.maxConcurrentExecutions,
                )}
                numeric
              />
              <DataField
                label="Utilization"
                value={
                  concurrency === null ? NOT_REPORTED : `${concurrency}%`
                }
                numeric
              />
              <DataField
                label="API throttles (5m)"
                value={throttles === null ? NOT_REPORTED : formatCount(throttles)}
                numeric
              />
              <DataField
                label="API function"
                value={asString(platform.data?.lambda?.operationalApi?.functionName)}
              />
              <DataField
                label="Generated"
                value={formatUtcDateTime(platform.data?.generatedAt)}
                numeric
              />
            </DataFieldGrid>
          )}
        </section>
      </div>

      <section className={styles.block}>
        <h2 className={styles.heading}>Active CloudWatch alarms</h2>
        {platformFailed ? (
          <p className={styles.note}>Unavailable</p>
        ) : alarms.length === 0 ? (
          <p className={styles.note}>
            {alarmCount === 0
              ? 'No Wilvor alarms are currently in ALARM.'
              : 'No alarm details returned.'}
          </p>
        ) : (
          <ul className={styles.alarmList}>
            {alarms.map((alarm, index) => (
              <AlarmRow key={asString(alarm.alarmName) ?? `alarm-${index}`} alarm={alarm} />
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}

function formatOptionalCount(value: unknown): string {
  const numeric = asNumber(value);

  return numeric === null ? NOT_REPORTED : formatCount(numeric);
}

function AlarmRow({ alarm }: { alarm: CloudWatchAlarmSummary }) {
  return (
    <li className={styles.alarm}>
      <StatusPill
        size="sm"
        presentation={{
          tone: 'high',
          label: asString(alarm.state) ?? 'ALARM',
          glyph: '▲',
        }}
      />
      <span className={`${styles.alarmName} wv-numeric`}>
        {asString(alarm.alarmName) ?? NOT_REPORTED}
      </span>
      <span className={styles.alarmMeta}>
        {asString(alarm.metricName) ?? NOT_REPORTED}
        {asString(alarm.namespace) ? ` · ${asString(alarm.namespace)}` : ''}
      </span>
    </li>
  );
}

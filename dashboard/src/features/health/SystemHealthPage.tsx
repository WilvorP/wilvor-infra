import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import {
  CLOUDWATCH_DASHBOARDS,
  dashboardById,
  resolveDashboardSelection,
  resolveTimeRange,
  type CloudWatchCategory,
} from '@/config/cloudwatchDashboards';
import { asString } from '@/utils/coerce';

import { CloudWatchViewer } from './CloudWatchViewer';
import { DashboardNavigator } from './DashboardNavigator';
import { WilvorHealthPanel } from './WilvorHealthPanel';
import styles from './SystemHealthPage.module.css';

const DASHBOARD_PARAM = 'dashboard';
const RANGE_PARAM = 'range';

/**
 * Engineering / NOC surface.
 *
 * Native Wilvor health is independent of CloudWatch observability. The
 * catalog is static (Terraform names). Only the selected dashboard is shown.
 */
export function SystemHealthPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedId = asString(searchParams.get(DASHBOARD_PARAM));
  const requestedRange = asString(searchParams.get(RANGE_PARAM));
  const selected = resolveDashboardSelection(requestedId);
  const range = resolveTimeRange(requestedRange);
  const invalidRequestedId =
    requestedId !== null && dashboardById(requestedId) === null
      ? requestedId
      : null;

  const [category, setCategory] = useState<CloudWatchCategory>('all');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (requestedId === null || requestedRange === null) {
      setSearchParams(
        {
          [DASHBOARD_PARAM]: requestedId ?? selected.id,
          [RANGE_PARAM]: range,
        },
        { replace: true },
      );
    }
  }, [range, requestedId, requestedRange, selected.id, setSearchParams]);

  const selectDashboard = useCallback(
    (id: string) => {
      setSearchParams({ [DASHBOARD_PARAM]: id, [RANGE_PARAM]: range });
    },
    [range, setSearchParams],
  );

  const selectRange = useCallback(
    (nextRange: typeof range) => {
      setSearchParams({
        [DASHBOARD_PARAM]: selected.id,
        [RANGE_PARAM]: nextRange,
      });
    },
    [selected.id, setSearchParams],
  );

  return (
    <div className={styles.page} data-testid="system-health-page">
      <WilvorHealthPanel />

      <div className={styles.observability}>
        <aside className={styles.navigator}>
          <DashboardNavigator
            items={CLOUDWATCH_DASHBOARDS}
            category={category}
            search={search}
            selectedId={selected.id}
            onCategoryChange={setCategory}
            onSearchChange={setSearch}
            onSelect={selectDashboard}
          />
        </aside>

        <CloudWatchViewer
          dashboard={selected}
          range={range}
          invalidRequestedId={invalidRequestedId}
          onRangeChange={selectRange}
        />
      </div>
    </div>
  );
}

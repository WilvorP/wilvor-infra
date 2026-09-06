import {
  CLOUDWATCH_CATEGORIES,
  CLOUDWATCH_CATEGORY_LABELS,
  filterDashboards,
  type CloudWatchCategory,
  type CloudWatchDashboardEntry,
} from '@/config/cloudwatchDashboards';

import styles from './DashboardNavigator.module.css';

export interface DashboardNavigatorProps {
  items: readonly CloudWatchDashboardEntry[];
  category: CloudWatchCategory;
  search: string;
  selectedId: string;
  onCategoryChange: (category: CloudWatchCategory) => void;
  onSearchChange: (search: string) => void;
  onSelect: (id: string) => void;
}

export function DashboardNavigator({
  items,
  category,
  search,
  selectedId,
  onCategoryChange,
  onSearchChange,
  onSelect,
}: DashboardNavigatorProps) {
  const visible = filterDashboards(items, category, search);

  return (
    <nav
      className={styles.nav}
      aria-label="CloudWatch dashboards"
      data-testid="dashboard-navigator"
    >
      <h2 className={styles.heading}>Infrastructure observability</h2>
      <p className={styles.lede}>
        Existing Wilvor CloudWatch dashboards. Only the selected dashboard is
        opened.
      </p>

      <label className={styles.search}>
        <span>Search dashboards</span>
        <input
          type="search"
          value={search}
          placeholder="METAR, risk, Kinesis, alerts…"
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>

      <div className={styles.categories} role="group" aria-label="Dashboard category">
        {CLOUDWATCH_CATEGORIES.map((token) => {
          const selected = token === category;

          return (
            <button
              key={token}
              type="button"
              aria-pressed={selected}
              className={`${styles.category} ${selected ? styles.categoryActive : ''}`}
              onClick={() => onCategoryChange(token)}
            >
              {CLOUDWATCH_CATEGORY_LABELS[token]}
            </button>
          );
        })}
      </div>

      {visible.length === 0 ? (
        <p className={styles.empty}>No dashboards match this filter.</p>
      ) : (
        <ul className={styles.list}>
          {visible.map((entry) => {
            const current = entry.id === selectedId;

            return (
              <li key={entry.id}>
                <button
                  type="button"
                  className={`${styles.item} ${current ? styles.itemActive : ''}`}
                  aria-current={current ? 'true' : undefined}
                  onClick={() => onSelect(entry.id)}
                >
                  <span className={styles.itemLabel}>{entry.label}</span>
                  <span className={`${styles.itemName} wv-numeric`}>
                    {entry.name}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}

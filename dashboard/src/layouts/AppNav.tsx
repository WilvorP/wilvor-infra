import { NavLink } from 'react-router-dom';

import { StatusPill } from '@/components/StatusPill';
import { useSystemHealth } from '@/hooks/useOperationalQueries';
import { ROUTES } from '@/routes/routeDefinitions';
import { presentSystemStatus } from '@/utils/status';

import { NavIcon } from './NavIcon';
import styles from './AppNav.module.css';

/**
 * Sidebar: brand, the seven operational workflows, and aggregate platform
 * status. Folds into a horizontal bar on narrow viewports.
 */
export function AppNav() {
  const health = useSystemHealth();

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true">
          W
        </span>
        <span className={styles.brandText}>
          <span className={styles.wordmark}>Wilvor</span>
          <span className={styles.product}>Operations</span>
        </span>
      </div>

      <nav className={styles.nav} aria-label="Operational workflows">
        <ul className={styles.list}>
          {ROUTES.map((route) => (
            <li key={route.path}>
              <NavLink
                to={route.path}
                end={route.path === '/'}
                className={({ isActive }) =>
                  `${styles.link} ${isActive ? styles.active : ''}`
                }
              >
                <span className={styles.icon}>
                  <NavIcon path={route.path} />
                </span>
                <span className={styles.label}>{route.navLabel}</span>
                {route.implemented ? null : (
                  <span className={styles.pending} title="Not yet implemented">
                    soon
                  </span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className={styles.footer}>
        <span className={styles.footerLabel}>Platform</span>
        <StatusPill
          size="sm"
          presentation={presentSystemStatus(
            health.isError ? null : health.data?.status,
          )}
        />
      </div>
    </aside>
  );
}

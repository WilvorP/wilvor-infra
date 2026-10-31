import { Outlet } from 'react-router-dom';

import { AppHeader } from './AppHeader';
import { AppNav } from './AppNav';
import styles from './AppShell.module.css';

/**
 * Persistent console chrome.
 *
 * The sidebar (brand, workflows, platform status) and the context bar
 * (source freshness, data time, advisory) are mounted once and survive route
 * changes, so freshness polling is continuous and the operator never loses
 * sight of how current the picture is while navigating.
 */
export function AppShell() {
  return (
    <div className={styles.shell}>
      <a className="wv-skip-link" href="#main-content">
        Skip to main content
      </a>

      <AppNav />

      <div className={styles.workspace}>
        <AppHeader />

        <main id="main-content" className={styles.main} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

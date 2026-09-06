import { Outlet } from 'react-router-dom';

import { FreshnessStrip } from '@/features/health/FreshnessStrip';

import { AppHeader } from './AppHeader';
import { AppNav } from './AppNav';
import styles from './AppShell.module.css';

/**
 * Persistent console chrome.
 *
 * The header, navigation and source freshness strip are mounted once and
 * survive route changes, so freshness polling is continuous and the operator
 * never loses sight of how current the picture is while navigating.
 */
export function AppShell() {
  return (
    <div className={styles.shell}>
      <a className="wv-skip-link" href="#main-content">
        Skip to main content
      </a>

      <AppHeader />
      <AppNav />
      <FreshnessStrip />

      <main id="main-content" className={styles.main} tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

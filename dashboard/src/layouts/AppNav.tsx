import { NavLink } from 'react-router-dom';

import { ROUTES } from '@/routes/routeDefinitions';

import styles from './AppNav.module.css';

/** Primary navigation across the seven operational workflows. */
export function AppNav() {
  return (
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
              {route.navLabel}
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
  );
}

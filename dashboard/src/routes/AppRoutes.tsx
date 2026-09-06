import { Navigate, Route, Routes } from 'react-router-dom';

import { OverviewPage } from '@/features/overview/OverviewPage';
import { AppShell } from '@/layouts/AppShell';

import { PlaceholderPage } from './PlaceholderPage';
import { ROUTES } from './routeDefinitions';

/**
 * Endpoints each unbuilt workflow will consume, stated up front so the
 * placeholder pages document the intended API dependency rather than guessing.
 */
const PLANNED_ENDPOINTS: Record<string, readonly string[]> = {
  '/aircraft': ['/aircraft', '/aircraft/{aircraftId}'],
  '/airports': ['/airports', '/airports/{airportId}'],
  '/encounters': ['/encounters/active'],
  '/recommendations': ['/recommendations/active'],
  '/alerts': ['/alerts/active'],
  '/health': ['/system-health', '/freshness', '/health'],
};

export interface AppRoutesProps {
  mapStyleUrl: string | null;
}

export function AppRoutes({ mapStyleUrl }: AppRoutesProps) {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage mapStyleUrl={mapStyleUrl} />} />

        {ROUTES.filter((route) => !route.implemented).map((route) => (
          <Route
            key={route.path}
            path={route.path}
            element={
              <PlaceholderPage
                route={route}
                plannedEndpoints={PLANNED_ENDPOINTS[route.path] ?? []}
              />
            }
          />
        ))}

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

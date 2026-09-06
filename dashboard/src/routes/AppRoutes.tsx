import { Navigate, Route, Routes } from 'react-router-dom';

import { AircraftPage } from '@/features/aircraft/AircraftPage';
import { AirportsPage } from '@/features/airports/AirportsPage';
import { EncountersPage } from '@/features/encounters/EncountersPage';
import { OverviewPage } from '@/features/overview/OverviewPage';
import { RecommendationsPage } from '@/features/recommendations/RecommendationsPage';
import { AppShell } from '@/layouts/AppShell';

import { PlaceholderPage } from './PlaceholderPage';
import { ROUTES } from './routeDefinitions';

/**
 * Endpoints each unbuilt workflow will consume, stated up front so the
 * placeholder pages document the intended API dependency rather than guessing.
 */
const PLANNED_ENDPOINTS: Record<string, readonly string[]> = {
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
        <Route
          path="/aircraft/:aircraftId?"
          element={<AircraftPage mapStyleUrl={mapStyleUrl} />}
        />
        <Route
          path="/airports/:airportId?"
          element={<AirportsPage mapStyleUrl={mapStyleUrl} />}
        />
        <Route
          path="/encounters"
          element={<EncountersPage mapStyleUrl={mapStyleUrl} />}
        />
        <Route
          path="/recommendations"
          element={<RecommendationsPage mapStyleUrl={mapStyleUrl} />}
        />

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

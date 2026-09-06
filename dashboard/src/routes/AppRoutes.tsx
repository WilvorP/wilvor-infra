import { Navigate, Route, Routes, useSearchParams } from 'react-router-dom';

import { AircraftPage } from '@/features/aircraft/AircraftPage';
import { AirportsPage } from '@/features/airports/AirportsPage';
import { AlertsPage } from '@/features/alerts/AlertsPage';
import { EncountersPage } from '@/features/encounters/EncountersPage';
import { SystemHealthPage } from '@/features/health/SystemHealthPage';
import { OverviewPage } from '@/features/overview/OverviewPage';
import { RecommendationsPage } from '@/features/recommendations/RecommendationsPage';
import { AppShell } from '@/layouts/AppShell';

import { PlaceholderPage } from './PlaceholderPage';
import { ROUTES } from './routeDefinitions';

/**
 * Endpoints each unbuilt workflow will consume, stated up front so the
 * placeholder pages document the intended API dependency rather than guessing.
 */
const PLANNED_ENDPOINTS: Record<string, readonly string[]> = {};

/** Preserve `?dashboard=` when operators still have the previous /health URL. */
function LegacyHealthRedirect() {
  const [params] = useSearchParams();
  const search = params.toString();

  return (
    <Navigate
      to={search ? `/system-health?${search}` : '/system-health'}
      replace
    />
  );
}

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
        <Route
          path="/alerts"
          element={<AlertsPage mapStyleUrl={mapStyleUrl} />}
        />
        <Route path="/system-health" element={<SystemHealthPage />} />
        <Route path="/health" element={<LegacyHealthRedirect />} />

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

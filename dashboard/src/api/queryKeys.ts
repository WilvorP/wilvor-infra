/**
 * Query key factory.
 *
 * Centralised so invalidation and prefetching cannot drift from the keys used
 * by the hooks.
 */
export const queryKeys = {
  all: ['wilvor'] as const,

  health: () => [...queryKeys.all, 'health'] as const,
  overview: () => [...queryKeys.all, 'overview'] as const,
  freshness: () => [...queryKeys.all, 'freshness'] as const,
  systemHealth: () => [...queryKeys.all, 'system-health'] as const,
  cloudWatchDashboard: (dashboardId: string) =>
    [...queryKeys.all, 'cloudwatch-dashboard', dashboardId] as const,
  cloudWatchWidgetImage: (
    dashboardId: string,
    widgetId: string,
    range: string,
    revision: string,
    width?: number,
    height?: number,
  ) =>
    [
      ...queryKeys.all,
      'cloudwatch-widget-image',
      dashboardId,
      widgetId,
      range,
      revision,
      width ?? 'auto',
      height ?? 'auto',
    ] as const,

  aircraftList: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'aircraft', 'list', params] as const,
  aircraftDetail: (aircraftId: string) =>
    [...queryKeys.all, 'aircraft', 'detail', aircraftId] as const,
  /** Map layer projection. Unparameterised: the endpoint takes no arguments. */
  mapAircraft: () => [...queryKeys.all, 'map', 'aircraft'] as const,

  activeHazards: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'hazards', 'active', params] as const,

  activeEncounters: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'encounters', 'active', params] as const,

  airportList: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'airports', 'list', params] as const,
  airportDetail: (airportId: string) =>
    [...queryKeys.all, 'airports', 'detail', airportId] as const,

  activeRecommendations: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'recommendations', 'active', params] as const,

  activeAlerts: (params: Record<string, unknown> = {}) =>
    [...queryKeys.all, 'alerts', 'active', params] as const,
} as const;

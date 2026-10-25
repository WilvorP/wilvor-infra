# Wilvor Live Operations Console

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, UNIT
**Production authorization:** no grant found. Visible copy states "Advisory only — not ATC" (source: `dashboard/src/layouts/AppHeader.tsx`). Wilvor is advisory decision support. It is not air traffic control and not a flight-control system.

## Purpose

Web client for the Wilvor operational API. It is a **presentation and
investigation layer only**: risk scoring, airport assessment, trajectory
projection, encounter geometry and recommendation logic all remain in the
deterministic backend services. Nothing in this application recomputes them.

Pointer page: [`docs/dashboard/README.md`](../docs/dashboard/README.md). API contracts: [`docs/api/operational-api.md`](../docs/api/operational-api.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `dashboard/src/` (`App.tsx`, `routes/AppRoutes.tsx`, `routes/routeDefinitions.ts`) |
| Terraform | TF absent | search: `*.tf`, `*.yml`, `*.ps1` for `dashboard/dist` or `wilvor-dashboard` hosting. Operational API Terraform is separate: `modules/operational_api/api.tf` |
| Unit tests | UNIT | Vitest under `dashboard/src/**/*.test.{ts,tsx}` |
| Contract tests | none found | search: `tests/contracts/` for dashboard |
| Infrastructure tests | none found | search: `tests/infrastructure/` for dashboard hosting |
| Integration tests | none found | no Playwright suite under `dashboard/` |
| Live harness | none found | search: `scripts/validate_*` for dashboard |
| Deployment | UNKNOWN | `not verified`. No in-repo apply record for hosting `dashboard/dist/` |
| Production authorization | no grant found | `dashboard/src/layouts/AppHeader.tsx`; this README |

## Behavior

### Architecture

```
React (this app)
      |  HTTPS
      v
API Gateway HTTP API        modules/operational_api/api.tf
      |
      v
Operational API Lambda      functions/operational_api/{app,repository}.py
      |
      v
DynamoDB / CloudWatch
```

The browser never talks to DynamoDB. Every read goes through the operational
API. Dependencies: React 19, react-router-dom 7, TanStack Query 5, MapLibre GL 5,
Vite 6 (source: `dashboard/package.json`).

There is no `/ai` route in `dashboard/src/routes/AppRoutes.tsx` or
`routeDefinitions.ts`.

### Source layout

| Path | Responsibility |
| --- | --- |
| `src/api/` | Typed HTTP client, route façade, error taxonomy, query client and keys |
| `src/config/` | Environment resolution, polling cadence, CloudWatch catalog |
| `src/hooks/useOperationalQueries.ts` | TanStack Query hooks for operational routes |
| `src/types/api.ts` | Response contracts traced from the Lambda and pipeline writers |
| `src/components/` | Design-system primitives (panel, KPI tile, status pill, states) |
| `src/features/` | `overview/`, `map/`, `aircraft/`, `airports/`, `encounters/`, `recommendations/`, `alerts/`, `health/`, `worklist/` |
| `src/layouts/` | Persistent shell: header, navigation, freshness strip |
| `src/routes/` | Route table. All seven workflows are `implemented: true` |
| `src/utils/` | Coercion, formatting and status vocabularies |
| `src/styles/` | Design tokens and base styles |
| `src/test/` | Vitest harness (`renderWithProviders`) |

### Design principles enforced in code

- **Status is never colour-only.** `StatusPill` always renders a glyph and a
  text label; tone is supplementary.
- **Absent is not zero.** Missing attributes render as `—`, never `0`. The
  pipeline writers strip `None` before `put_item`, so almost every attribute is
  legitimately optional.
- **Freshness is always visible.** The shell mounts the source freshness strip
  on every route.
- **No fabricated data.** Unbuilt workflows render an explicit placeholder
  rather than sample content, and an aircraft with no reported heading is
  drawn without one. At `8abd2d5` every `ROUTES` entry is `implemented: true`.
- **Positional payloads are validated, not trusted.** `/map/aircraft` returns
  rows plus the column names describing them; the decoder resolves fields by
  name and refuses to draw against an unexpected layout.

### Routes and operational pages

Source: `dashboard/src/routes/routeDefinitions.ts`, `dashboard/src/routes/AppRoutes.tsx`.

| Path | Component | Data |
| --- | --- | --- |
| `/` | `OverviewPage` | `GET /overview` KPIs; map hazards and fleet; worklists |
| `/aircraft`, `/aircraft/:aircraftId?` | `AircraftPage` | `GET /aircraft` listing; `GET /aircraft/{id}` investigation |
| `/airports`, `/airports/:airportId?` | `AirportsPage` / `AirportInvestigation` | `GET /airports`; `GET /airports/{id}` |
| `/encounters` | `EncountersPage` | `GET /encounters/active`; optional `?encounterId=` |
| `/recommendations` | `RecommendationsPage` | `GET /recommendations/active`; optional `?recommendationId=` |
| `/alerts` | `AlertsPage` | `GET /alerts/active`; optional `?alertId=` |
| `/system-health` | `SystemHealthPage` | `GET /health`, `/freshness`, `/system-health`; CloudWatch PNGs; `?dashboard=` `?range=` |
| `/health` | `LegacyHealthRedirect` | redirects to `/system-health`, preserving `?dashboard=` |
| `*` | `Navigate` to `/` | — |

Aircraft investigation query context is built by
`dashboard/src/features/aircraft/investigation.ts`
(`hazardId`, `encounterId`, `riskId`, `recommendationId`, `alertId`,
`fingerprint`, `source`).

Header copy: "Advisory only — not ATC" (source: `dashboard/src/layouts/AppHeader.tsx`).
Recommendations page states the list is backend-stored advisory decision support
(source: `dashboard/src/features/recommendations/RecommendationsPage.tsx`).
Investigation panels present stored risk, recommendation, and overlap values
and do not recalculate them (source: `dashboard/src/features/aircraft/AircraftSelectionPanel.tsx`).

### API client and request handling

Base URL: required `VITE_WILVOR_API_BASE_URL` (`dashboard/src/config/env.ts`).
Default timeout `DEFAULT_API_TIMEOUT_MS = 30_000`. Client: `dashboard/src/api/http.ts`.

- GET only; `cache: 'no-store'`; CORS mode
- No HTTP-level retries (TanStack Query owns retries)
- Concurrency queue `maxConcurrent` default 2
- Errors: `ApiError` kinds `network`, `timeout`, `client`, `server`, `parse`

Query retries: up to 2, exponential backoff capped at 8 s; no retry on `client`
or `parse` (`dashboard/src/api/queryClient.ts`).

Façade routes in `dashboard/src/api/operationalApi.ts` match the Operational API
GET surface except the client does not call `/airports/status` (the backend
treats that path as identical to `/airports`).

The HTTP client comments still say the Lambda is pinned to
`reserved_concurrent_executions = 2`. `envs/dev/main.tf` at `8abd2d5` sets
`lambda_reserved_concurrency = 5`. The client queue of 2 remains the implemented
browser limit.

### Refresh and caching

Intervals: `dashboard/src/config/refresh.ts`. Polling is off when
`import.meta.env.MODE === 'test'`. TanStack Query uses
`refetchIntervalInBackground: false`, `refetchOnWindowFocus: true`, and
`placeholderData` to keep the last good picture during refetch.

| Key | Poll interval |
| --- | --- |
| `overview`, `aircraftMap`, `encounters`, `alerts`, `recommendations` | 20 s |
| `freshness` | 30 s |
| `hazards`, `airports` | 45 s |
| `systemHealth`, `cloudWatchDashboard` | 60 s |
| `aircraftDetail` | 12 s |
| `airportDetail` | 20 s |

Lambda process caches (source: `functions/operational_api/repository.py`):
`/overview` 45 s; `/freshness` and `/system-health` 15 s; `/map/aircraft` 15 s;
current-set snapshots 15 s. Polling `/overview` every 20 s can reuse a 45 s
server cache.

### Geospatial visualization

Library: MapLibre GL. Shared map: `dashboard/src/features/map/OperationsMap.tsx`.
Built-in dark CARTO raster: `dashboard/src/features/map/mapStyle.ts`. Override:
`VITE_WILVOR_MAP_STYLE_URL`. Optional tile key: `VITE_CARTO_BASEMAP_KEY`.

| Layer | Hook | Source API |
| --- | --- | --- |
| Hazards | `useHazardLayer` | `GET /hazards/active` |
| Aircraft | `useAircraftLayer` | `GET /map/aircraft` |
| Trajectory | `useTrajectoryLayer` | `projectionPoints` from `GET /aircraft/{id}` |
| Airports | `useAirportLayer` | loaded `GET /airports` pages only |

Honesty constraints: aircraft without track render as dots, not 0° arrows; the
map does not color aircraft by risk (`useAircraftLayer.ts`). No callsign symbol
layers: the built-in style declares no `glyphs`.

### Loading, unavailable, and stale-data states

Primitives: `LoadingState`, `ErrorState`, `EmptyState`, `Notice`
(`dashboard/src/components/QueryState.tsx`).

- Fatal load (`isError && !data`): `ErrorState` with retry
- Stale while cached (`isError && data`): `Notice` plus KPI `stale` badge
- Freshness strip: "Source freshness unavailable" vs "Last refresh failed"
- Missing API URL: `ConfigurationError` instead of mounting the console
- Empty vs unknown: empty-state copy must say which

## Development

Requires Node 18, 20 or 22. Vite 6 and Vitest 3 are pinned deliberately: Vite 7
requires Node `^20.19.0 || >=22.12.0`, which excludes the Node 20.10 toolchain
this repository was developed against.

```bash
cd dashboard
npm install
cp .env.example .env      # then set VITE_WILVOR_API_BASE_URL
npm run dev               # http://localhost:5173
```

The dev server uses `strictPort: true` on port 5173. This is intentional: the
API Gateway CORS allowlist is exact-origin and contains only
`http://localhost:3000` and `http://localhost:5173`
(`modules/operational_api/variables.tf`). Silently falling back to 5174 would
produce opaque CORS failures against the real API.

### Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Vite dev server on port 5173 |
| `npm run build` | Typecheck, then production build to `dist/` |
| `npm run preview` | Serve the production build |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest, single run |
| `npm run test:watch` | Vitest in watch mode |
| `npm run verify` | lint + typecheck + test + build |

### Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `VITE_WILVOR_API_BASE_URL` | yes | — | Operational API origin, no trailing slash |
| `VITE_WILVOR_API_TIMEOUT_MS` | no | `30000` | Request abort budget (1000–120000) |
| `VITE_WILVOR_MAP_STYLE_URL` | no | built-in dark basemap | MapLibre style override |
| `VITE_CARTO_BASEMAP_KEY` | no | unset | CARTO key for built-in tiles; without it tiles may watermark |

A missing or malformed base URL renders a configuration screen instead of
mounting the console, so a setup mistake is not mistaken for an outage.

Retrieve the endpoint from Terraform:

```bash
cd envs/dev
terraform output -raw operational_api_endpoint
```

That output exists when the `dev` stack has been applied. Whether it is applied
now is UNKNOWN.

## API dependency

Endpoints consumed at `8abd2d5`:

| Endpoint | Used by | Poll interval |
| --- | --- | --- |
| `GET /health` | System Health panel | with system health (60 s) |
| `GET /overview` | KPI strip, risk / recommendation / airport panels | 20 s |
| `GET /freshness` | Source freshness strip, header timestamp | 30 s |
| `GET /system-health` | Header platform status, System Health | 60 s |
| `GET /system-health/dashboards/...` | CloudWatch viewer | 60 s when a dashboard is selected |
| `GET /hazards/active` | Map hazard layer, investigation drawer | 45 s |
| `GET /map/aircraft` | Map aircraft layer, worklist callsign lookup | 20 s |
| `GET /aircraft` | Aircraft listing | listing pages |
| `GET /aircraft/{aircraftId}` | Investigation, overview projection, worklist selection | 12 s |
| `GET /encounters/active` | Overview worklist, Encounters page | 20 s |
| `GET /airports` | Airports page, airport map markers | 45 s |
| `GET /airports/{airportId}` | Airport investigation | 20 s |
| `GET /recommendations/active` | Overview worklist (tab-enabled), Recommendations page | 20 s |
| `GET /alerts/active` | Overview worklist, Alerts page | 20 s |

Intervals live in `src/config/refresh.ts`, not in feature code.

Polling suspends while the browser tab is hidden
(`refetchIntervalInBackground: false`). The HTTP client queues at most two
in-flight requests (`maxConcurrent`, default 2).

### Response handling

Responses are raw DynamoDB items. `src/types/api.ts` models attributes as
optional because the writers omit `None` values. Field names were traced from
the writers rather than guessed — notably `track_deg` (not `heading`),
`latitude`/`longitude` (not `lat`/`lon`), `status_reasons` on airport status
(not `reasons`), and `forecast_flight_category` on TAF periods (not
`flight_category`).

The only frontend transformations are in
`src/features/health/freshness.ts` (reconciling the two freshness vocabularies
the backend emits) and `src/features/map/hazardGeoJson.ts` (reshaping hazard
records into a GeoJSON source). Neither derives operational meaning.

---

## The aircraft map layer

The map renders aircraft from `GET /map/aircraft` as a single WebGL GeoJSON
source with three layers over it: a rotated icon for aircraft with a reported
track, a plain dot for those without one, and a selection halo. There are no
per-aircraft DOM markers — several thousand absolutely positioned elements
repositioned on every pan does not stay interactive at network scale.

The rows are positional, so `src/features/map/aircraftGeoJson.ts` resolves
every field by name from the response's `columns` array before reading any
row. If an expected column is absent the layer draws nothing and says why,
rather than decoding against an unknown layout and placing traffic at wrong
positions. Reordered and additive column changes both decode correctly.

Two deliberate honesty constraints in the layer:

- **Aircraft with no reported track render as dots, not arrows.** An arrow at
  0° would assert a heading the platform never received.
- **No risk colouring.** Risk level is not part of the `/map/aircraft`
  projection, and deriving it in the browser would duplicate backend scoring.

### Why `/aircraft` could not back this layer

`GET /aircraft` cannot back a network map, which is why the endpoint above was
added:

- Unfiltered listing is a DynamoDB **`Scan`** (`repository.py`, `list_aircraft`),
  not a Query.
- `limit` is capped at **100** (`_parse_limit` in `app.py`). DynamoDB applies
  `Limit` to items *evaluated before* the `expires_at_epoch` filter, so a page
  can return fewer than 100 items and still yield a `nextToken`.
- Pagination is **strictly sequential** — each request needs the previous
  response's token — so it cannot be parallelised. Roughly 5,000 aircraft means
  **50+ chained round trips per refresh cycle**.
- Each item is the full ~38-attribute state record (`raw_s3_uri`,
  `idempotency_key`, `correlation_id`, `schema_version`, …) at roughly 1.1 KB,
  of which a map layer needs about 120 bytes. That is ~5.5 MB per full network
  load, ~90 % discarded.
- The dev stage throttles at 25 req/s (`envs/dev/main.tf`). One browser tab
  paginating the fleet would consume the API's concurrency budget.

Working around this in React — by paginating aggressively, or by rendering a
silently truncated subset — would either exhaust the API or present a partial
traffic picture indistinguishable from a complete one. On a decision-support
surface that is a worse failure than showing no layer at all.

### The additive endpoint

`GET /map/aircraft` — a lean, map-oriented projection. Implemented in
`repository.get_map_aircraft`, routed in `app.py`, declared in
`modules/operational_api/api.tf`.

```json
{
  "generatedAt": "2026-09-03T12:00:00Z",
  "columns": [
    "aircraftId",
    "callsign",
    "longitude",
    "latitude",
    "trackDeg",
    "baroAltitudeFt",
    "groundSpeedKt",
    "positionTimeEpoch"
  ],
  "count": 3412,
  "truncated": false,
  "aircraft": [
    ["a1b2c3", "UAL123", -122.375, 37.6188, 270.5, 35000, 450, 1786515880]
  ]
}
```

Rows are positional. `columns` is returned alongside them so the compact
encoding stays self-describing rather than depending on documentation, and so
the client can fail loudly if the order ever changes — which is exactly what
`aircraftGeoJson.ts` does with it.

Design notes:

- **Reuses the existing pattern.** `_cached(...)` already backs `/overview`,
  `/freshness` and `/system-health`; a 15 s cache here means the fleet is
  assembled at most four times a minute regardless of viewer count.
- **Projection, not full items.** A `ProjectionExpression` limited to the eight
  attributes above cuts the payload by roughly 90 %, which is the same
  technique `get_overview` already uses.
- **Array-of-arrays** rather than objects removes repeated key names, roughly
  halving the remaining payload.
- **One response, no client pagination.** At ~90 bytes per aircraft, 5,000
  aircraft is ~450 KB, comfortably inside the 6 MB Lambda response limit.
  `truncated` reports an internal cap honestly rather than silently trimming.
- **No risk join.** Risk level lives in a separate table and would require a
  second full scan per cache window, so it is deliberately omitted. The map
  will highlight risk from the encounter and risk APIs instead.
- **Optional viewport filter.** `?bbox=west,south,east,north` is a design note
  only. It is not implemented in `app.py` or `repository.py` at `8abd2d5`.

Actual change: one route in `local.route_keys`
(`modules/operational_api/api.tf`), one branch in `app.py`, one function in
`repository.py`. No DynamoDB schema change, no new table, no new IAM
permission, and no change to any existing response contract.

Two honest caveats. `truncated` caps the *response*, but `_scan_all` has
already read every matching item into Lambda memory by that point, so the cap
bounds payload size and client work rather than backend cost — the same
tradeoff `get_overview` already makes. And the endpoint is still Scan-based;
it removes the round-trip and payload problems, not the underlying table
access pattern. A viewport or H3 variant is the next step if the fleet grows
enough to matter.

## Known limitations

- **No callsign labels on the map.** The built-in basemap style declares no
  `glyphs` source, so symbol layers cannot render text. Adding labels needs a
  glyph endpoint first. Callsigns appear in selection panels.
- **Airport markers are only the loaded `/airports` pages**, not a dedicated
  map endpoint.
- **System Health renders CloudWatch dashboards as server-side PNGs.** The
  AWS console chrome, hover tooltips and interactive zoom are not available
  inside Wilvor.
- **`deck.gl` is not used.** MapLibre's own GeoJSON source, with
  `icon-allow-overlap` and collision detection disabled, handles the fleet as
  a single GPU-backed source.
- **Altitude overlap is often `UNKNOWN`** in encounter records when bands are
  unavailable. The UI must not imply otherwise.
- **`/map/aircraft` remains Scan-based** and may set `truncated: true` at 8000
  rows.
- **Overview recommendations worklist query is enabled only when that tab is
  selected** (`OperationalWorklist.tsx`).
- **No Terraform in this repository hosts `dashboard/dist/`.**
- **HTTP client comments still cite reserved concurrency 2**; Terraform at
  `8abd2d5` sets 5 (`envs/dev/main.tf`, `lambda_reserved_concurrency`).
- Current Vitest pass count is UNKNOWN. This page does not record a suite result.

## Testing

```bash
npm test
```

Coverage focuses on behaviour that would silently corrupt an operational
reading: API client error classification (network, timeout, 4xx, 5xx, non-JSON
body, caller abort), per-route `limit` ceilings, environment validation, the
two freshness vocabularies, hazard GeoJSON conversion including hazards that
cannot be drawn, and loading / error / empty / stale rendering.

The aircraft layer's decoder carries the densest tests, because it is the one
place where a wrong answer is invisible: that fields are resolved from
`columns` rather than fixed offsets, that a reordered payload decodes
identically, that unknown columns are ignored but a missing one disables the
layer, that longitude precedes latitude, that absent measurements stay `null`
instead of becoming `0`, and that unusable, duplicate and non-array rows are
counted rather than dropped silently. The icon is asserted to point north and
to be symmetric about its vertical axis, since an asymmetric bitmap appears to
wobble as it rotates with the track.

Snapshot tests are deliberately avoided.

### Backend tests

`GET /map/aircraft` is covered by `tests/unit/operational_api/`:

```bash
python -m pytest tests/unit/operational_api -q
```

Those tests cover payload contract, read behaviour, and routing (method
rejection, and that `/map/aircraft` does not shadow `/aircraft/{id}`). Current
pass counts for that suite are UNKNOWN.

## Historical record

The following sentences described an earlier dashboard milestone and are not
current at `8abd2d5`:

- Feature slices listed only `overview/`, `map/`, `aircraft/`, and `health/`.
  Current `src/features/` also includes `alerts/`, `airports/`, `encounters/`,
  `recommendations/`, and `worklist/`.
- `src/routes/` held "placeholders for unbuilt workflows". All seven `ROUTES`
  entries are `implemented: true`.
- "Endpoints consumed today" listed only `/overview`, `/freshness`,
  `/system-health`, `/hazards/active`, and `/map/aircraft`.
- "`/overview` 20 s" as the Lambda cache TTL. `repository.get_overview` caches
  for 45 seconds.
- "Selecting an aircraft does not yet load its investigation detail" and
  "`GET /aircraft/{aircraftId}` … is not called yet". `useAircraftDetail` is
  used on Aircraft, Overview projection, and worklist flows.
- "Airports are not yet on the map". `useAirportLayer` and `AirportsPage`
  render loaded airport pages.
- `VITE_WILVOR_API_TIMEOUT_MS` default `20000`. Code default is `30000`
  (`dashboard/src/config/env.ts`, `dashboard/.env.example`).
- Operational API Lambda "pinned to `reserved_concurrent_executions = 2`".
  `envs/dev/main.tf` assigns `lambda_reserved_concurrency = 5`. Client source
  comments still mention 2.
- A dated pytest note that `tests/unit` had 20 pre-existing failures, and that
  `GET /map/aircraft` had nineteen tests, is not restated as a current suite
  result.

## Sources

- Source: `dashboard/src/routes/routeDefinitions.ts` (`ROUTES`)
- Source: `dashboard/src/routes/AppRoutes.tsx`
- Source: `dashboard/src/api/http.ts`; `dashboard/src/api/operationalApi.ts`; `dashboard/src/config/{env,refresh}.ts`
- Source: `dashboard/src/features/map/OperationsMap.tsx`; `dashboard/src/layouts/AppHeader.tsx`
- Source: `dashboard/package.json`; `dashboard/vite.config.ts`
- Source: `functions/operational_api/repository.py` (`get_overview` cache 45 s)
- Source: `envs/dev/main.tf` (`lambda_reserved_concurrency = 5`)
- Tests: `dashboard/src/**/*.test.{ts,tsx}`; `tests/unit/operational_api/test_map_aircraft.py`

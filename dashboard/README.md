# Wilvor Live Operations Console

Web client for the Wilvor operational API. It is a **presentation and
investigation layer only**: risk scoring, airport assessment, trajectory
projection, encounter geometry and recommendation logic all remain in the
deterministic backend services. Nothing in this application recomputes them.

Wilvor is **advisory decision support**. It is not air traffic control and not
a flight-control system.

---

## Architecture

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
API.

### Source layout

| Path | Responsibility |
| --- | --- |
| `src/api/` | Typed HTTP client, route façade, error taxonomy, query client and keys |
| `src/config/` | Environment resolution and centralised polling cadence |
| `src/types/api.ts` | Response contracts traced from the Lambda and pipeline writers |
| `src/components/` | Design-system primitives (panel, KPI tile, status pill, states) |
| `src/features/` | Feature slices: `overview/`, `map/`, `aircraft/`, `health/` |
| `src/layouts/` | Persistent shell: header, navigation, freshness strip |
| `src/routes/` | Route table and placeholders for unbuilt workflows |
| `src/utils/` | Coercion, formatting and status vocabularies |
| `src/styles/` | Design tokens and base styles |

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
  drawn without one.
- **Positional payloads are validated, not trusted.** `/map/aircraft` returns
  rows plus the column names describing them; the decoder resolves fields by
  name and refuses to draw against an unexpected layout.

---

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
| `VITE_WILVOR_API_TIMEOUT_MS` | no | `20000` | Request abort budget (1000–120000) |
| `VITE_WILVOR_MAP_STYLE_URL` | no | built-in dark basemap | MapLibre style override |

A missing or malformed base URL renders a configuration screen instead of
mounting the console, so a setup mistake is not mistaken for an outage.

Retrieve the endpoint from Terraform:

```bash
cd envs/dev
terraform output -raw operational_api_endpoint
```

---

## API dependency

Endpoints consumed **today**:

| Endpoint | Used by | Poll interval |
| --- | --- | --- |
| `GET /overview` | KPI strip, risk / alert / recommendation / airport panels | 20 s |
| `GET /freshness` | Source freshness strip, header timestamp | 30 s |
| `GET /system-health` | Header platform status | 60 s |
| `GET /hazards/active` | Map hazard layer, investigation drawer | 45 s |
| `GET /map/aircraft` | Map aircraft layer, aircraft selection panel | 20 s |

Intervals live in `src/config/refresh.ts`, not in feature code. They are
aligned to the Lambda's own response caches (`/overview` 20 s,
`/freshness` and `/system-health` 15 s in `repository.py`); polling faster
would cost invocations without producing newer data.

Polling suspends while the browser tab is hidden
(`refetchIntervalInBackground: false`), which matters because the operational
API Lambda is pinned to `reserved_concurrent_executions = 2`.

The HTTP client also queues at most two in-flight requests (`maxConcurrent`,
default 2). Live validation showed that mounting Overview's five queries at
once overflowed that reserved concurrency and API Gateway returned 503,
blanking the console. Queuing is a client-side match for the existing Lambda
limit, not a backend change.

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
- The dev stage throttles at 25 req/s with the Lambda at
  `reserved_concurrent_executions = 2` (`envs/dev/main.tf`). One browser tab
  paginating the fleet would consume the entire API's concurrency budget.

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
- **Optional viewport filter.** `?bbox=west,south,east,north` could be applied
  server-side, or the existing `current_h3_cell` GSI reused for an H3-oriented
  variant, if a full-fleet response later proves too large.

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

### Other current limitations

- **Selecting an aircraft does not yet load its investigation detail.**
  Clicking establishes the selection, halos the aircraft and opens a panel
  showing the map projection's own fields. Projection, encounters, risk and
  recommendations require `GET /aircraft/{aircraftId}`, which is not called
  yet; the panel names that endpoint instead of rendering empty sections.
- **No callsign labels on the map.** The built-in basemap style declares no
  `glyphs` source, so symbol layers cannot render text. Adding labels needs a
  glyph endpoint first. Callsigns appear in the selection panel.
- **Airports are not yet on the map**, though `AirportStatus` does carry
  `latitude`/`longitude`, so this needs no backend change — only pagination
  handling for the `/airports` scan.
- **Alerts and recommendations show overview projections only.** The full
  objects come from `/alerts/active` and `/recommendations/active`, which the
  dedicated workflows will consume.
- **Aircraft, Airports, Encounters, Recommendations, Alerts and System Health
  routes are registered but not implemented.** They render explicit
  placeholders and no data.
- **`deck.gl` is not used.** MapLibre's own GeoJSON source, with
  `icon-allow-overlap` and collision detection disabled, handles the fleet as
  a single GPU-backed source. It stays worth re-measuring once aircraft
  trajectories and airports are drawn at the same time.
- **Altitude overlap is always `UNKNOWN`** in encounter records; the encounter
  processor does not yet evaluate it. The UI must not imply otherwise.

---

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

The `GET /map/aircraft` endpoint added for this milestone is covered by
`tests/unit/operational_api/`, which is the first test coverage the operational
API layer has had:

```bash
python -m pytest tests/unit/operational_api -q
```

Nineteen tests cover the payload contract (row/column agreement, GeoJSON
longitude-before-latitude order, absent attributes staying `null` rather than
becoming `0`), read behaviour (projection contents, filter presence, scan
pagination drain, truncation reporting, response caching) and routing
(method rejection, and that `/map/aircraft` does not shadow `/aircraft/{id}`).

The remaining backend suites are unaffected by this work. Note that
`tests/unit` has 20 pre-existing failures on a clean checkout, unrelated to
the dashboard: 15 in `aircraft` and 1 in `airport_status` fail in isolation,
and 4 in `sigmet` pass alone but fail in a full run, indicating order-dependent
state leakage.

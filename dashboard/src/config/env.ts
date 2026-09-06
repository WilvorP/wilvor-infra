/**
 * Runtime configuration resolved from Vite environment variables.
 *
 * Configuration problems are represented as data rather than thrown at import
 * time, so the application shell can render a readable operator-facing error
 * instead of a blank page.
 */

export interface AppConfig {
  /** Operational API origin, without a trailing slash. */
  readonly apiBaseUrl: string;
  /** Milliseconds before an in-flight API request is aborted. */
  readonly apiTimeoutMs: number;
  /** Optional MapLibre style URL. `null` selects the built-in dark basemap. */
  readonly mapStyleUrl: string | null;
}

export interface ConfigResult {
  readonly config: AppConfig | null;
  /** Human-readable problems that prevent the console from operating. */
  readonly errors: readonly string[];
}

const DEFAULT_API_TIMEOUT_MS = 30_000;
const MIN_API_TIMEOUT_MS = 1_000;
const MAX_API_TIMEOUT_MS = 120_000;

export type RawEnv = Record<string, string | boolean | undefined>;

function readString(env: RawEnv, key: string): string | null {
  const value = env[key];

  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();

  return trimmed.length > 0 ? trimmed : null;
}

function parseTimeout(raw: string | null, errors: string[]): number {
  if (raw === null) {
    return DEFAULT_API_TIMEOUT_MS;
  }

  const parsed = Number(raw);

  if (!Number.isFinite(parsed)) {
    errors.push(
      `VITE_WILVOR_API_TIMEOUT_MS must be a number. Received "${raw}".`,
    );
    return DEFAULT_API_TIMEOUT_MS;
  }

  if (parsed < MIN_API_TIMEOUT_MS || parsed > MAX_API_TIMEOUT_MS) {
    errors.push(
      `VITE_WILVOR_API_TIMEOUT_MS must be between ${MIN_API_TIMEOUT_MS} and ` +
        `${MAX_API_TIMEOUT_MS}. Received "${raw}".`,
    );
    return DEFAULT_API_TIMEOUT_MS;
  }

  return parsed;
}

/**
 * Resolve application configuration from a Vite-style environment object.
 *
 * Exported separately from {@link appConfig} so it can be tested without
 * mutating `import.meta.env`.
 */
export function resolveConfig(env: RawEnv): ConfigResult {
  const errors: string[] = [];

  const rawBaseUrl = readString(env, 'VITE_WILVOR_API_BASE_URL');
  const apiTimeoutMs = parseTimeout(
    readString(env, 'VITE_WILVOR_API_TIMEOUT_MS'),
    errors,
  );
  const mapStyleUrl = readString(env, 'VITE_WILVOR_MAP_STYLE_URL');

  if (rawBaseUrl === null) {
    errors.push(
      'VITE_WILVOR_API_BASE_URL is not set. Copy dashboard/.env.example to ' +
        'dashboard/.env and set it to the operational API endpoint.',
    );

    return { config: null, errors };
  }

  let normalisedBaseUrl: string;

  try {
    const parsed = new URL(rawBaseUrl);

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      errors.push(
        `VITE_WILVOR_API_BASE_URL must use http or https. Received ` +
          `"${parsed.protocol}".`,
      );
      return { config: null, errors };
    }

    normalisedBaseUrl = `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, '');
  } catch {
    errors.push(
      `VITE_WILVOR_API_BASE_URL is not a valid absolute URL. ` +
        `Received "${rawBaseUrl}".`,
    );
    return { config: null, errors };
  }

  return {
    config: {
      apiBaseUrl: normalisedBaseUrl,
      apiTimeoutMs,
      mapStyleUrl,
    },
    errors,
  };
}

export const configResult: ConfigResult = resolveConfig(
  import.meta.env as unknown as RawEnv,
);

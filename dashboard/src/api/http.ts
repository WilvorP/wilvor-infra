import { ApiError } from './errors';

/**
 * Minimal typed HTTP layer for the Wilvor operational API.
 *
 * The operational API is GET-only and unauthenticated at the edge. JSON
 * routes use `content-type: application/json`. CloudWatch widget images
 * return `image/png`. This client stays small: no interceptors, no retry
 * policy (TanStack Query owns retries), no request body handling.
 */

export type QueryValue = string | number | boolean | null | undefined;

export type QueryParams = Record<string, QueryValue>;

export interface HttpClientOptions {
  readonly baseUrl: string;
  readonly timeoutMs: number;
  /** Injectable for tests. Defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch;
  /**
   * Maximum in-flight API requests from this client.
   *
   * The operational API Lambda is pinned to
   * `reserved_concurrent_executions = 2`. Five Overview queries mounting at
   * once overflow that budget and API Gateway returns 503. Default matches
   * the reserved concurrency so the console queues instead of throttling.
   */
  readonly maxConcurrent?: number;
}

export interface RequestOptions {
  readonly params?: QueryParams;
  /** Caller cancellation, typically TanStack Query's per-query signal. */
  readonly signal?: AbortSignal;
}

/** Shape of an operational API error body, e.g. `{"message": "..."}`. */
interface ApiErrorBody {
  message?: unknown;
  requestId?: unknown;
}

export function buildUrl(
  baseUrl: string,
  path: string,
  params?: QueryParams,
): string {
  const normalisedBase = baseUrl.replace(/\/+$/, '');
  const normalisedPath = path.startsWith('/') ? path : `/${path}`;

  const search = new URLSearchParams();

  for (const [key, value] of Object.entries(params ?? {})) {
    // Omitted parameters must not become "undefined"/"null" strings: the API
    // validates `limit` and `nextToken` and would reject them with a 400.
    if (value === null || value === undefined) {
      continue;
    }

    search.set(key, String(value));
  }

  const queryString = search.toString();

  return queryString.length > 0
    ? `${normalisedBase}${normalisedPath}?${queryString}`
    : `${normalisedBase}${normalisedPath}`;
}

/**
 * Combine a caller-supplied signal with a timeout into one abortable unit.
 *
 * Implemented manually rather than with `AbortSignal.any` so the client works
 * in older runtimes and in the jsdom test environment.
 */
function withTimeout(
  timeoutMs: number,
  external?: AbortSignal,
): { signal: AbortSignal; dispose: () => void; didTimeOut: () => boolean } {
  const controller = new AbortController();
  let timedOut = false;

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const onExternalAbort = () => controller.abort();

  if (external) {
    if (external.aborted) {
      controller.abort();
    } else {
      external.addEventListener('abort', onExternalAbort, { once: true });
    }
  }

  return {
    signal: controller.signal,
    didTimeOut: () => timedOut,
    dispose: () => {
      clearTimeout(timer);
      external?.removeEventListener('abort', onExternalAbort);
    },
  };
}

function extractErrorBody(body: unknown): {
  message: string | null;
  requestId: string | null;
} {
  if (typeof body !== 'object' || body === null) {
    return { message: null, requestId: null };
  }

  const candidate = body as ApiErrorBody;

  return {
    message: typeof candidate.message === 'string' ? candidate.message : null,
    requestId:
      typeof candidate.requestId === 'string' ? candidate.requestId : null,
  };
}

export class OperationalApiHttpClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly maxConcurrent: number;
  private inFlight = 0;
  private readonly waiters: Array<() => void> = [];

  constructor(options: HttpClientOptions) {
    this.baseUrl = options.baseUrl;
    this.timeoutMs = options.timeoutMs;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.maxConcurrent = options.maxConcurrent ?? 2;
  }

  private acquire(signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) {
      return Promise.reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    }

    if (this.inFlight < this.maxConcurrent) {
      this.inFlight += 1;
      return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
      const waiter = () => {
        signal?.removeEventListener('abort', onAbort);
        this.inFlight += 1;
        resolve();
      };

      const onAbort = () => {
        const index = this.waiters.indexOf(waiter);
        if (index !== -1) {
          this.waiters.splice(index, 1);
        }
        reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
      };

      this.waiters.push(waiter);
      signal?.addEventListener('abort', onAbort, { once: true });
    });
  }

  private release(): void {
    this.inFlight = Math.max(0, this.inFlight - 1);
    const next = this.waiters.shift();
    if (next) {
      next();
    }
  }

  async get<T>(path: string, options: RequestOptions = {}): Promise<T> {
    await this.acquire(options.signal);

    try {
      if (options.signal?.aborted) {
        throw (
          options.signal.reason ??
          new DOMException('Aborted', 'AbortError')
        );
      }

      const url = buildUrl(this.baseUrl, path, options.params);
      // Timeout starts after a concurrency slot is held so queued requests do
      // not burn their budget waiting behind a long /overview or /map/aircraft
      // scan.
      const timeout = withTimeout(this.timeoutMs, options.signal);

      let response: Response;

      try {
        response = await this.fetchImpl(url, {
          method: 'GET',
          headers: { accept: 'application/json' },
          signal: timeout.signal,
          // The API sets `cache-control: no-store`; this prevents any
          // intermediate HTTP cache from serving a stale operational picture.
          cache: 'no-store',
          mode: 'cors',
        });
      } catch (cause) {
        if (timeout.didTimeOut()) {
          throw new ApiError(
            `Request to ${path} timed out after ${this.timeoutMs}ms.`,
            { kind: 'timeout', url, cause },
          );
        }

        // A caller-initiated abort is normal control flow (component unmount,
        // query key change). Re-throw it so TanStack Query can ignore it rather
        // than surfacing it to the operator as a failure.
        if (options.signal?.aborted) {
          throw cause;
        }

        throw new ApiError(`Request to ${path} failed.`, {
          kind: 'network',
          url,
          cause,
        });
      } finally {
        timeout.dispose();
      }

      const rawBody = await response.text();

      let parsedBody: unknown = null;
      let parseFailed = false;

      if (rawBody.length > 0) {
        try {
          parsedBody = JSON.parse(rawBody);
        } catch {
          parseFailed = true;
        }
      }

      if (!response.ok) {
        const { message, requestId } = extractErrorBody(parsedBody);

        throw new ApiError(
          message ?? `Request to ${path} failed with status ${response.status}.`,
          {
            kind: response.status >= 500 ? 'server' : 'client',
            status: response.status,
            url,
            requestId,
          },
        );
      }

      if (parseFailed || typeof parsedBody !== 'object' || parsedBody === null) {
        throw new ApiError(
          `Response from ${path} was not a JSON object.`,
          { kind: 'parse', status: response.status, url },
        );
      }

      return parsedBody as T;
    } finally {
      this.release();
    }
  }

  /**
   * Binary GET for CloudWatch widget PNGs.
   *
   * JSON error bodies from the operational API are still parsed so a 404
   * or 400 surfaces as an ApiError instead of a generic blob failure.
   */
  async getBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
    await this.acquire(options.signal);

    try {
      if (options.signal?.aborted) {
        throw (
          options.signal.reason ??
          new DOMException('Aborted', 'AbortError')
        );
      }

      const url = buildUrl(this.baseUrl, path, options.params);
      const timeout = withTimeout(this.timeoutMs, options.signal);

      let response: Response;

      try {
        response = await this.fetchImpl(url, {
          method: 'GET',
          headers: { accept: 'image/png, application/json' },
          signal: timeout.signal,
          cache: 'no-store',
          mode: 'cors',
        });
      } catch (cause) {
        if (timeout.didTimeOut()) {
          throw new ApiError(
            `Request to ${path} timed out after ${this.timeoutMs}ms.`,
            { kind: 'timeout', url, cause },
          );
        }

        if (options.signal?.aborted) {
          throw cause;
        }

        throw new ApiError(`Request to ${path} failed.`, {
          kind: 'network',
          url,
          cause,
        });
      } finally {
        timeout.dispose();
      }

      if (!response.ok) {
        let parsedBody: unknown = null;

        try {
          parsedBody = await response.json();
        } catch {
          parsedBody = null;
        }

        const { message, requestId } = extractErrorBody(parsedBody);

        throw new ApiError(
          message ?? `Request to ${path} failed with status ${response.status}.`,
          {
            kind: response.status >= 500 ? 'server' : 'client',
            status: response.status,
            url,
            requestId,
          },
        );
      }

      return response.blob();
    } finally {
      this.release();
    }
  }
}

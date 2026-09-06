import { describe, expect, it, vi } from 'vitest';

import { ApiError } from './errors';
import { buildUrl, OperationalApiHttpClient } from './http';

const BASE_URL = 'https://api.example.test';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function client(fetchImpl: typeof fetch, timeoutMs = 5_000) {
  return new OperationalApiHttpClient({
    baseUrl: BASE_URL,
    timeoutMs,
    fetchImpl,
  });
}

describe('buildUrl', () => {
  it('joins the base URL and path without duplicating slashes', () => {
    expect(buildUrl('https://api.example.test/', '/overview')).toBe(
      'https://api.example.test/overview',
    );
    expect(buildUrl('https://api.example.test', 'overview')).toBe(
      'https://api.example.test/overview',
    );
  });

  it('omits null and undefined parameters', () => {
    // The API validates `limit` and `nextToken`, so serialising an absent
    // value as the string "undefined" would produce a 400.
    const url = buildUrl(BASE_URL, '/aircraft', {
      limit: 50,
      nextToken: null,
      callsign: undefined,
    });

    expect(url).toBe('https://api.example.test/aircraft?limit=50');
  });

  it('encodes parameter values', () => {
    const url = buildUrl(BASE_URL, '/aircraft', { nextToken: 'a+b/c=' });

    expect(url).toContain('nextToken=a%2Bb%2Fc%3D');
  });

  it('emits no query string when every parameter is absent', () => {
    expect(buildUrl(BASE_URL, '/overview', { limit: undefined })).toBe(
      'https://api.example.test/overview',
    );
  });
});

describe('OperationalApiHttpClient.get', () => {
  it('returns the parsed JSON body on success', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ generatedAt: '2026-09-03T12:00:00Z' }),
    );

    const result = await client(fetchImpl as unknown as typeof fetch).get<{
      generatedAt: string;
    }>('/overview');

    expect(result.generatedAt).toBe('2026-09-03T12:00:00Z');
  });

  it('requests JSON and bypasses HTTP caches', async () => {
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({}),
    );

    await client(fetchImpl as unknown as typeof fetch).get('/overview');

    const init = fetchImpl.mock.calls[0]![1]!;

    expect(init.method).toBe('GET');
    expect(init.cache).toBe('no-store');
    expect((init.headers as Record<string, string>).accept).toBe(
      'application/json',
    );
  });

  it('maps a 4xx to a non-retryable client error carrying the API message', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ message: 'limit must be between 1 and 100' }, 400),
    );

    const error = await client(fetchImpl as unknown as typeof fetch)
      .get('/aircraft')
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe('client');
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).message).toBe(
      'limit must be between 1 and 100',
    );
    expect((error as ApiError).isRetryable).toBe(false);
  });

  it('maps a 5xx to a retryable server error and keeps the requestId', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        { message: 'AWS data access failed', requestId: 'req-42' },
        500,
      ),
    );

    const error = await client(fetchImpl as unknown as typeof fetch)
      .get('/overview')
      .catch((caught: unknown) => caught);

    expect((error as ApiError).kind).toBe('server');
    expect((error as ApiError).requestId).toBe('req-42');
    expect((error as ApiError).isRetryable).toBe(true);
  });

  it('reports a parse error when the body is not JSON', async () => {
    // A misconfigured base URL typically returns an HTML page rather than the
    // operational API's JSON, and that must not surface as a network outage.
    const fetchImpl = vi.fn(
      async () =>
        new Response('<!doctype html><title>Nope</title>', {
          status: 200,
          headers: { 'content-type': 'text/html' },
        }),
    );

    const error = await client(fetchImpl as unknown as typeof fetch)
      .get('/overview')
      .catch((caught: unknown) => caught);

    expect((error as ApiError).kind).toBe('parse');
    expect((error as ApiError).isRetryable).toBe(false);
  });

  it('reports a parse error when the body is JSON but not an object', async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response('42', {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const error = await client(fetchImpl as unknown as typeof fetch)
      .get('/overview')
      .catch((caught: unknown) => caught);

    expect((error as ApiError).kind).toBe('parse');
  });

  it('classifies a rejected fetch as a network error', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });

    const error = await client(fetchImpl as unknown as typeof fetch)
      .get('/overview')
      .catch((caught: unknown) => caught);

    expect((error as ApiError).kind).toBe('network');
    expect((error as ApiError).isRetryable).toBe(true);
  });

  it('aborts and reports a timeout when the request exceeds the budget', async () => {
    const fetchImpl = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        }),
    );

    const error = await client(fetchImpl as unknown as typeof fetch, 10)
      .get('/overview')
      .catch((caught: unknown) => caught);

    expect((error as ApiError).kind).toBe('timeout');
  });

  it('propagates a caller abort instead of reporting it as a failure', async () => {
    // Unmounting a component or changing a query key aborts in-flight work.
    // That is normal control flow, not an operational error.
    const controller = new AbortController();

    const fetchImpl = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        }),
    );

    const promise = client(fetchImpl as unknown as typeof fetch)
      .get('/overview', { signal: controller.signal })
      .catch((caught: unknown) => caught);

    // Abort can land in the microtask after acquire() and before fetch().
    // The client must not hang waiting for a fetch that never starts.
    controller.abort();

    const error = await promise;

    expect(error).not.toBeInstanceOf(ApiError);
    expect((error as DOMException).name).toBe('AbortError');
  });

  it('keeps at most two requests in flight, matching Lambda reserved concurrency', async () => {
    let current = 0;
    let peak = 0;

    const fetchImpl = vi.fn(async () => {
      current += 1;
      peak = Math.max(peak, current);
      await new Promise((resolve) => {
        setTimeout(resolve, 20);
      });
      current -= 1;
      return jsonResponse({});
    });

    const http = client(fetchImpl as unknown as typeof fetch);

    await Promise.all([
      http.get('/overview'),
      http.get('/freshness'),
      http.get('/system-health'),
      http.get('/map/aircraft'),
      http.get('/hazards/active'),
    ]);

    expect(fetchImpl).toHaveBeenCalledTimes(5);
    expect(peak).toBe(2);
  });
});

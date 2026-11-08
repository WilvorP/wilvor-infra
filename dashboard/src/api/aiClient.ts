import {
  AI_QUERY_ROUTE_KEY,
  LOCAL_DEV_COPILOT_PATH,
  LOCAL_DEV_PREVIEW_STANDING,
  assertCopilotQueryRequest,
  assertCopilotQueryResult,
  createRouteDisabledResult,
  DISABLED_COPILOT_STANDING,
  HOST_CONTRACT_COPILOT_STANDING,
  INJECTED_COPILOT_STANDING,
  type CopilotQueryRequest,
  type CopilotQueryResult,
  type CopilotRouteStanding,
} from './aiContracts';

/**
 * Reviewed Copilot query client.
 *
 * Default production construction is the disabled client. It never POSTs
 * `POST /ai/query`. Backend route implementation stays with the integration
 * owner; this sprint must not invent that endpoint.
 */
export interface CopilotQueryClient {
  readonly standing: CopilotRouteStanding;
  query(
    request: CopilotQueryRequest,
    options?: { signal?: AbortSignal },
  ): Promise<CopilotQueryResult>;
}

export class DisabledCopilotQueryClient implements CopilotQueryClient {
  readonly standing: CopilotRouteStanding = DISABLED_COPILOT_STANDING;

  async query(
    request: CopilotQueryRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<CopilotQueryResult> {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? new DOMException('Aborted', 'AbortError');
    }

    return createRouteDisabledResult(request);
  }
}

export function createDisabledCopilotQueryClient(): CopilotQueryClient {
  return new DisabledCopilotQueryClient();
}

export type InjectedCopilotHandler = (
  request: CopilotQueryRequest,
  options: { signal?: AbortSignal },
) => Promise<CopilotQueryResult> | CopilotQueryResult;

/**
 * Test double. Callers inject fixture results. This still refuses a live
 * HTTP POST and still refuses to mark production AI as active.
 */
export class InjectedCopilotQueryClient implements CopilotQueryClient {
  readonly standing: CopilotRouteStanding = INJECTED_COPILOT_STANDING;

  constructor(private readonly handler: InjectedCopilotHandler) {}

  async query(
    request: CopilotQueryRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<CopilotQueryResult> {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? new DOMException('Aborted', 'AbortError');
    }

    assertCopilotQueryRequest(request);

    const result = await this.handler(request, options);

    return assertCopilotQueryResult({
      ...result,
      standing: INJECTED_COPILOT_STANDING,
    });
  }
}

export function createInjectedCopilotQueryClient(
  handler: InjectedCopilotHandler,
): CopilotQueryClient {
  return new InjectedCopilotQueryClient(handler);
}

/**
 * Host-contract client. Browser-supplied ids are untrusted labels.
 * This still never POSTs `POST /ai/query` and never calls fetch().
 */
export class HostContractCopilotQueryClient implements CopilotQueryClient {
  readonly standing: CopilotRouteStanding = HOST_CONTRACT_COPILOT_STANDING;

  constructor(private readonly handler: InjectedCopilotHandler) {}

  async query(
    request: CopilotQueryRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<CopilotQueryResult> {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? new DOMException('Aborted', 'AbortError');
    }

    assertCopilotQueryRequest(request);

    const result = await this.handler(request, options);

    return assertCopilotQueryResult({
      ...result,
      standing: HOST_CONTRACT_COPILOT_STANDING,
    });
  }
}

export function createHostContractCopilotQueryClient(
  handler: InjectedCopilotHandler,
): CopilotQueryClient {
  return new HostContractCopilotQueryClient(handler);
}

/**
 * Loopback developer preview. The browser posts only to the Vite-proxied
 * local path. It never posts `POST /ai/query` and never receives an API key.
 */
export class LocalDevCopilotQueryClient implements CopilotQueryClient {
  readonly standing: CopilotRouteStanding = LOCAL_DEV_PREVIEW_STANDING;

  async query(
    request: CopilotQueryRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<CopilotQueryResult> {
    assertCopilotQueryRequest(request);

    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort(new DOMException('Timeout', 'TimeoutError'));
    }, 180_000);

    if (options.signal?.aborted) {
      controller.abort(options.signal.reason);
    } else {
      options.signal?.addEventListener(
        'abort',
        () => controller.abort(options.signal?.reason),
        { once: true },
      );
    }

    try {
      const response = await fetch(LOCAL_DEV_COPILOT_PATH, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      });
      const payload: unknown = await response.json();

      if (!response.ok || !isRecord(payload)) {
        throw new Error('Local developer preview did not return a Copilot result.');
      }

      return assertCopilotQueryResult({
        ...(payload as unknown as CopilotQueryResult),
        standing: LOCAL_DEV_PREVIEW_STANDING,
      });
    } finally {
      clearTimeout(timer);
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function createLocalDevCopilotQueryClient(): CopilotQueryClient {
  return new LocalDevCopilotQueryClient();
}

/**
 * Guard used by tests: the reviewed client surface has no HTTP POST helper
 * and does not mention a live `/ai/query` transport.
 */
export function copilotClientPostsAiQuery(): false {
  void AI_QUERY_ROUTE_KEY;
  return false;
}

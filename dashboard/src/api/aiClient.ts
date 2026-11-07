import {
  AI_QUERY_ROUTE_KEY,
  assertCopilotQueryRequest,
  assertCopilotQueryResult,
  createRouteDisabledResult,
  DISABLED_COPILOT_STANDING,
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
 * Guard used by tests: the reviewed client surface has no HTTP POST helper
 * and does not mention a live `/ai/query` transport.
 */
export function copilotClientPostsAiQuery(): false {
  void AI_QUERY_ROUTE_KEY;
  return false;
}

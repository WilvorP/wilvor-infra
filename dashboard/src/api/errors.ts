/**
 * Error taxonomy for the operational API boundary.
 *
 * The console distinguishes these cases because they require different
 * operator responses: a network failure means "the API may be down", a 5xx
 * means "the API is up but data access failed", and a parse failure usually
 * means the base URL points somewhere that is not the operational API.
 */

export type ApiErrorKind =
  /** Request never produced an HTTP response (DNS, CORS, offline, refused). */
  | 'network'
  /** Request exceeded the configured timeout or was cancelled. */
  | 'timeout'
  /** Server returned 4xx. */
  | 'client'
  /** Server returned 5xx. */
  | 'server'
  /** Response body was not the JSON object shape the API contract promises. */
  | 'parse';

export interface ApiErrorOptions {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly url?: string;
  readonly requestId?: string | null;
  readonly cause?: unknown;
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly url: string | null;
  /** `requestId` echoed by the operational API on 5xx responses. */
  readonly requestId: string | null;

  constructor(message: string, options: ApiErrorOptions) {
    super(message, { cause: options.cause });

    this.name = 'ApiError';
    this.kind = options.kind;
    this.status = options.status ?? null;
    this.url = options.url ?? null;
    this.requestId = options.requestId ?? null;
  }

  /**
   * Whether retrying the identical request could plausibly succeed.
   *
   * 4xx responses from this API indicate a malformed request (bad `limit`,
   * invalid `nextToken`, unknown route), so retrying is pointless.
   */
  get isRetryable(): boolean {
    return this.kind !== 'client' && this.kind !== 'parse';
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/**
 * Operator-facing description of a failure.
 *
 * Kept free of stack traces and internal identifiers other than the API's own
 * `requestId`, which is the value needed to correlate with CloudWatch logs.
 */
export function describeApiError(error: unknown): string {
  if (!isApiError(error)) {
    return error instanceof Error
      ? error.message
      : 'An unexpected error occurred.';
  }

  const suffix = error.requestId ? ` (request ${error.requestId})` : '';

  switch (error.kind) {
    case 'network':
      return `Cannot reach the operational API. Check the API base URL, network connectivity, and that the API Gateway CORS allowlist includes this origin.${suffix}`;
    case 'timeout':
      return `The operational API did not respond in time.${suffix}`;
    case 'client':
      return `${error.message}${suffix}`;
    case 'server':
      return `The operational API failed to serve this request.${suffix}`;
    case 'parse':
      return `The operational API returned an unexpected response format. Verify that the API base URL points at the Wilvor operational API.${suffix}`;
  }
}

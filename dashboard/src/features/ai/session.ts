import {
  COPILOT_SESSION_QUERY_SCHEMA,
  LIVE_OPS_PUBLIC_SCHEMA_VERSION,
  PRIOR_TURN_MAX,
  PRIOR_TURN_TEXT_MAX_LENGTH,
  type CopilotPriorTurn,
  type CopilotQueryRequest,
  type CopilotSelectorHints,
} from '@/api/aiContracts';

export interface CopilotSessionIdentity {
  readonly planningSessionId: string;
  readonly conversationId: string;
}

export interface CopilotRequestIdentity {
  readonly masterRequestId: string;
  readonly correlationId: string;
}

export interface CopilotDisplayedTurn {
  readonly role: 'user' | 'assistant';
  readonly text: string;
}

function newToken(): string {
  return crypto.randomUUID().replace(/-/g, '');
}

function distinctPair(
  left: string,
  right: string,
  label: string,
): void {
  if (left === right) {
    throw new Error(`${label} must be pairwise distinct`);
  }
}

export function createCopilotSessionIdentity(
  randomId: () => string = newToken,
): CopilotSessionIdentity {
  const planningSessionId = `plan-${randomId()}`;
  const conversationId = `conv-${randomId()}`;

  distinctPair(planningSessionId, conversationId, 'session identities');

  return { planningSessionId, conversationId };
}

export function createCopilotRequestIdentity(
  session: CopilotSessionIdentity,
  randomId: () => string = newToken,
): CopilotRequestIdentity {
  const masterRequestId = `mreq-${randomId()}`;
  const correlationId = `corr-${randomId()}`;

  distinctPair(masterRequestId, correlationId, 'request identities');
  distinctPair(masterRequestId, session.planningSessionId, 'request identities');
  distinctPair(masterRequestId, session.conversationId, 'request identities');
  distinctPair(correlationId, session.planningSessionId, 'request identities');
  distinctPair(correlationId, session.conversationId, 'request identities');

  return { masterRequestId, correlationId };
}

export function toUtcTimestamp(now: Date = new Date()): string {
  return now.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export function priorTurnsFromHistory(
  turns: readonly CopilotDisplayedTurn[],
): CopilotPriorTurn[] {
  return turns
    .filter((turn) => turn.role === 'user' || turn.role === 'assistant')
    .slice(-PRIOR_TURN_MAX)
    .map((turn) => ({
      role: turn.role,
      text: turn.text.slice(0, PRIOR_TURN_TEXT_MAX_LENGTH),
    }));
}

export function selectorHintsFromSearch(
  params: URLSearchParams,
): CopilotSelectorHints | null {
  const aircraftId = params.get('aircraftId')?.trim() || undefined;
  const callsign = params.get('callsign')?.trim() || undefined;
  const hazardId = params.get('hazardId')?.trim() || undefined;
  const airportId = params.get('airportId')?.trim() || undefined;

  if (
    aircraftId === undefined &&
    callsign === undefined &&
    hazardId === undefined &&
    airportId === undefined
  ) {
    return null;
  }

  return { aircraftId, callsign, hazardId, airportId };
}

export function buildCopilotQueryRequest(input: {
  session: CopilotSessionIdentity;
  request: CopilotRequestIdentity;
  userText: string;
  priorTurns: readonly CopilotDisplayedTurn[];
  applicationRequestTimeUtc: string;
  selectorHints?: CopilotSelectorHints | null;
}): CopilotQueryRequest {
  return {
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    liveOpsSchemaVersion: LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    masterRequestId: input.request.masterRequestId,
    planningSessionId: input.session.planningSessionId,
    conversationId: input.session.conversationId,
    correlationId: input.request.correlationId,
    applicationRequestTimeUtc: input.applicationRequestTimeUtc,
    userText: input.userText.trim(),
    priorTurns: priorTurnsFromHistory(input.priorTurns),
    selectorHints: input.selectorHints ?? null,
  };
}

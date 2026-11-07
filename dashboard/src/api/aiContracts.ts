/**
 * Reviewed Copilot session-query interface.
 *
 * This module copies existing Sprint 3C / 3D Master and Live Ops contracts
 * into a dashboard-owned client shape. It does not invent a backend route,
 * enable `POST /ai/query`, or claim production AI is active.
 *
 * Mapping onto a live HTTP handler is reserved for the integration owner.
 * Until that owner enables the route, the only legal transports are the
 * local disabled client and an injected test double.
 *
 * Authoritative Python mapping: `wilvor_ai.sprint4_copilot_contract_map`.
 * This TypeScript module is a client projection. It does not redefine
 * Master, MA5, or Live Ops backend contracts.
 */

export const COPILOT_SESSION_QUERY_SCHEMA =
  'wilvor.ai.copilot.session_query.v1' as const;

/** Existing Live Ops public schema. Copied, not redefined. */
export const LIVE_OPS_PUBLIC_SCHEMA_VERSION =
  'wilvor.ai.live_ops_public.v1' as const;

/** Existing Master parallel-contracts schema. Copied, not redefined. */
export const MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION =
  'wilvor.ai.master_parallel_contracts.v1' as const;

export const AI_QUERY_ROUTE_KEY = 'POST /ai/query' as const;

export const USER_TEXT_MAX_LENGTH = 8192;
export const PRIOR_TURN_MAX = 4;
export const PRIOR_TURN_TEXT_MAX_LENGTH = 2048;
export const PRIOR_TURNS_TOTAL_MAX_LENGTH = 8192;
export const TOKEN_MAX_LENGTH = 256;

export const MASTER_RESPONSE_POLICY_STATES = [
  'NOT_EVALUATED',
  'UNSUPPORTED',
  'UNAVAILABLE',
  'INSUFFICIENT_EVIDENCE',
] as const;

export type MasterResponsePolicyState =
  (typeof MASTER_RESPONSE_POLICY_STATES)[number];

/**
 * Fixed status sentences from `master_response_renderer.py`.
 * The Copilot UI must not replace these with model prose.
 */
export const MASTER_RESPONSE_STATUS_TEXT = {
  NOT_EVALUATED: 'Response policy has not been evaluated.',
  UNSUPPORTED:
    'The request is unsupported. No operational conclusion is available.',
  UNAVAILABLE:
    'Required evidence is unavailable. No count or zero is inferred.',
  INSUFFICIENT_EVIDENCE:
    'Supplied evidence is not sufficient for an operational claim.',
} as const satisfies Record<MasterResponsePolicyState, string>;

export const REQUIRED_LIMITATION_CODES = [
  'SOURCE_AUTHENTICITY_NOT_ESTABLISHED',
  'PRODUCTION_AUTHORITY_DENIED',
  'NO_OPERATIONAL_CLAIM',
  'EXECUTION_DENIED',
] as const;

export const ALLOWED_LIMITATION_CODES = [
  ...REQUIRED_LIMITATION_CODES,
  'REQUEST_COVERAGE_NOT_ESTABLISHED',
  'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
  'COMPARABILITY_NOT_OPERATIONAL_AGREEMENT',
  'LIVE_OPS_REFS_NOT_TOOL_RESULTS',
  'HISTORICAL_RUN_NOT_FABRICATED',
] as const;

export type CopilotLimitationCode = (typeof ALLOWED_LIMITATION_CODES)[number];

export const LIVE_OPS_COMPLETENESS_NOTICE =
  'ALL_VERIFIED means every submitted claim verified. It does not mean every operational fact was investigated or that the source data is fresh, complete, or atomic.';

export const ROUTE_DISABLED_NOTICE =
  'POST /ai/query remains disabled. This console does not call that route, and production AI is not active.';

export type CopilotTransport =
  | 'LOCAL_DISABLED'
  | 'INJECTED_TEST'
  | 'HOST_CONTRACT';

export type CopilotEvidenceEntityKind = 'AIRCRAFT' | 'HAZARD' | 'ENCOUNTER';

export interface CopilotRouteStanding {
  readonly routeKey: typeof AI_QUERY_ROUTE_KEY;
  readonly routeEnabled: false;
  readonly productionAiActive: false;
  readonly transport: CopilotTransport;
  readonly backendRouteOwner: 'integration_owner';
}

export const DISABLED_COPILOT_STANDING: CopilotRouteStanding = {
  routeKey: AI_QUERY_ROUTE_KEY,
  routeEnabled: false,
  productionAiActive: false,
  transport: 'LOCAL_DISABLED',
  backendRouteOwner: 'integration_owner',
};

export const INJECTED_COPILOT_STANDING: CopilotRouteStanding = {
  routeKey: AI_QUERY_ROUTE_KEY,
  routeEnabled: false,
  productionAiActive: false,
  transport: 'INJECTED_TEST',
  backendRouteOwner: 'integration_owner',
};

export const HOST_CONTRACT_COPILOT_STANDING: CopilotRouteStanding = {
  routeKey: AI_QUERY_ROUTE_KEY,
  routeEnabled: false,
  productionAiActive: false,
  transport: 'HOST_CONTRACT',
  backendRouteOwner: 'integration_owner',
};

export interface CopilotPriorTurn {
  readonly role: 'user' | 'assistant';
  readonly text: string;
}

/**
 * Untrusted selector text copied from `LiveOpsSelectorHints`.
 * Presence is not authorization and does not execute a tool.
 */
export interface CopilotSelectorHints {
  readonly aircraftId?: string;
  readonly callsign?: string;
  readonly hazardId?: string;
  readonly airportId?: string;
}

export interface CopilotQueryRequest {
  readonly schemaVersion: typeof COPILOT_SESSION_QUERY_SCHEMA;
  readonly liveOpsSchemaVersion: typeof LIVE_OPS_PUBLIC_SCHEMA_VERSION;
  readonly masterRequestId: string;
  readonly planningSessionId: string;
  readonly conversationId: string;
  readonly correlationId: string;
  readonly applicationRequestTimeUtc: string;
  readonly userText: string;
  readonly priorTurns: readonly CopilotPriorTurn[];
  readonly selectorHints?: CopilotSelectorHints | null;
}

export interface CopilotEvidenceItem {
  readonly entityKind: CopilotEvidenceEntityKind;
  readonly entityId: string;
  readonly callsign?: string | null;
  readonly aircraftId?: string | null;
  readonly hazardId?: string | null;
  readonly encounterId?: string | null;
  readonly toolName: string;
  readonly toolCallId: string;
  readonly sourceName: string;
  /** Absence stays absence. Never invented. */
  readonly sourceVersion: string | null;
  /** Source observation clock. Distinct from application and evaluation clocks. */
  readonly observationTimeUtc: string | null;
  /** Evaluation clock. Must not be copied from the request clock. */
  readonly evaluationTimeUtc: string | null;
  /** Freshness band from the source, or null when unknown. Never inferred. */
  readonly freshnessBand: string | null;
  readonly limitationCodes: readonly string[];
  readonly truncated: boolean;
  readonly hasMore: boolean;
  readonly pageEmpty: boolean;
  readonly verifiedZeroMinted: false;
}

export interface CopilotQueryResult {
  readonly schemaVersion: typeof COPILOT_SESSION_QUERY_SCHEMA;
  readonly standing: CopilotRouteStanding;
  readonly masterRequestId: string;
  readonly planningSessionId: string;
  readonly conversationId: string;
  readonly correlationId: string;
  readonly applicationRequestTimeUtc: string;
  readonly state: MasterResponsePolicyState;
  readonly statusText: string;
  /** Display text. Must equal `statusText`; not model-generated prose. */
  readonly assistantText: string;
  readonly limitationCodes: readonly CopilotLimitationCode[];
  readonly claimsAdmitted: false;
  readonly productionAuthority: 'DENIED';
  readonly sourceAuthenticity: 'NOT_ESTABLISHED';
  readonly requestCoverage: 'NOT_ESTABLISHED';
  readonly completenessNotice: typeof LIVE_OPS_COMPLETENESS_NOTICE;
  readonly routeDisabledNotice: typeof ROUTE_DISABLED_NOTICE;
  readonly productionAiActive: false;
  readonly evidence: readonly CopilotEvidenceItem[];
  readonly pageEmpty: boolean;
  readonly truncated: boolean;
  readonly hasMore: boolean;
  readonly verifiedZeroMinted: false;
}

export class CopilotContractError extends Error {
  readonly code: string;

  constructor(code: string, message?: string) {
    super(message ?? code);
    this.name = 'CopilotContractError';
    this.code = code;
  }
}

function assertToken(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) {
    throw new CopilotContractError(`invalid_${field}`);
  }

  if (value.length > TOKEN_MAX_LENGTH) {
    throw new CopilotContractError(`invalid_${field}`);
  }

  return value;
}

function assertUtc(value: unknown, field: string): string {
  const text = assertToken(value, field);

  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(text)) {
    throw new CopilotContractError(`invalid_${field}`);
  }

  const parsed = Date.parse(text);

  if (Number.isNaN(parsed)) {
    throw new CopilotContractError(`invalid_${field}`);
  }

  return text;
}

function assertOptionalToken(
  value: unknown,
  field: string,
): string | null | undefined {
  if (value === undefined) {
    return undefined;
  }

  if (value === null) {
    return null;
  }

  return assertToken(value, field);
}

function assertBooleanFalse(value: unknown, field: string): false {
  if (value !== false) {
    throw new CopilotContractError(field);
  }

  return false;
}

export function assertCopilotQueryRequest(
  request: CopilotQueryRequest,
): CopilotQueryRequest {
  if (request.schemaVersion !== COPILOT_SESSION_QUERY_SCHEMA) {
    throw new CopilotContractError('invalid_schema_version');
  }

  if (request.liveOpsSchemaVersion !== LIVE_OPS_PUBLIC_SCHEMA_VERSION) {
    throw new CopilotContractError('invalid_live_ops_schema_version');
  }

  const masterRequestId = assertToken(request.masterRequestId, 'master_request_id');
  const planningSessionId = assertToken(
    request.planningSessionId,
    'planning_session_id',
  );
  const conversationId = assertToken(request.conversationId, 'conversation_id');
  const correlationId = assertToken(request.correlationId, 'correlation_id');

  const identities = [
    masterRequestId,
    planningSessionId,
    conversationId,
    correlationId,
  ];

  if (new Set(identities).size !== identities.length) {
    throw new CopilotContractError('identity_not_pairwise_distinct');
  }

  assertUtc(request.applicationRequestTimeUtc, 'application_request_time_utc');

  if (
    typeof request.userText !== 'string' ||
    request.userText.trim().length === 0 ||
    request.userText !== request.userText.trim() ||
    request.userText.length > USER_TEXT_MAX_LENGTH
  ) {
    throw new CopilotContractError('invalid_user_text');
  }

  if (!Array.isArray(request.priorTurns)) {
    throw new CopilotContractError('invalid_prior_turns');
  }

  if (request.priorTurns.length > PRIOR_TURN_MAX) {
    throw new CopilotContractError('too_many_prior_turns');
  }

  let priorTotal = 0;

  for (const turn of request.priorTurns) {
    if (turn.role !== 'user' && turn.role !== 'assistant') {
      throw new CopilotContractError('invalid_prior_turn_role');
    }

    if (
      typeof turn.text !== 'string' ||
      turn.text.trim().length === 0 ||
      turn.text.length > PRIOR_TURN_TEXT_MAX_LENGTH
    ) {
      throw new CopilotContractError('invalid_prior_turn_text');
    }

    priorTotal += turn.text.length;
  }

  if (priorTotal > PRIOR_TURNS_TOTAL_MAX_LENGTH) {
    throw new CopilotContractError('prior_turns_too_large');
  }

  const hints = request.selectorHints;

  if (hints != null) {
    const values = [
      hints.aircraftId,
      hints.callsign,
      hints.hazardId,
      hints.airportId,
    ];

    if (values.every((item) => item == null)) {
      throw new CopilotContractError('invalid_selector_hints');
    }

    for (const [field, value] of [
      ['aircraft_id', hints.aircraftId],
      ['callsign', hints.callsign],
      ['hazard_id', hints.hazardId],
      ['airport_id', hints.airportId],
    ] as const) {
      if (value !== undefined) {
        assertToken(value, field);
      }
    }
  }

  return request;
}

function assertStanding(standing: CopilotRouteStanding): CopilotRouteStanding {
  if (standing.routeKey !== AI_QUERY_ROUTE_KEY) {
    throw new CopilotContractError('invalid_route_key');
  }

  if (standing.routeEnabled !== false) {
    throw new CopilotContractError('ai_query_route_must_remain_disabled');
  }

  if (standing.productionAiActive !== false) {
    throw new CopilotContractError('production_ai_must_remain_inactive');
  }

  if (
    standing.transport !== 'LOCAL_DISABLED' &&
    standing.transport !== 'INJECTED_TEST' &&
    standing.transport !== 'HOST_CONTRACT'
  ) {
    throw new CopilotContractError('invalid_transport');
  }

  if (standing.backendRouteOwner !== 'integration_owner') {
    throw new CopilotContractError('backend_route_reserved_for_integrator');
  }

  return standing;
}

function assertEvidenceItem(item: CopilotEvidenceItem): void {
  if (
    item.entityKind !== 'AIRCRAFT' &&
    item.entityKind !== 'HAZARD' &&
    item.entityKind !== 'ENCOUNTER'
  ) {
    throw new CopilotContractError('invalid_entity_kind');
  }

  assertToken(item.entityId, 'entity_id');
  assertToken(item.toolName, 'tool_name');
  assertToken(item.toolCallId, 'tool_call_id');
  assertToken(item.sourceName, 'source_name');
  assertOptionalToken(item.sourceVersion, 'source_version');
  assertOptionalToken(item.callsign, 'callsign');
  assertOptionalToken(item.aircraftId, 'aircraft_id');
  assertOptionalToken(item.hazardId, 'hazard_id');
  assertOptionalToken(item.encounterId, 'encounter_id');

  if (item.observationTimeUtc !== null) {
    assertUtc(item.observationTimeUtc, 'observation_time_utc');
  }

  if (item.evaluationTimeUtc !== null) {
    assertUtc(item.evaluationTimeUtc, 'evaluation_time_utc');
  }

  if (
    item.observationTimeUtc !== null &&
    item.evaluationTimeUtc !== null &&
    item.observationTimeUtc === item.evaluationTimeUtc
  ) {
    throw new CopilotContractError('clocks_must_remain_distinct');
  }

  if (item.freshnessBand !== null) {
    assertToken(item.freshnessBand, 'freshness_band');
  }

  if (!Array.isArray(item.limitationCodes)) {
    throw new CopilotContractError('invalid_limitation_codes');
  }

  if (typeof item.truncated !== 'boolean' || typeof item.hasMore !== 'boolean') {
    throw new CopilotContractError('invalid_pagination_flags');
  }

  if (typeof item.pageEmpty !== 'boolean') {
    throw new CopilotContractError('invalid_page_empty');
  }

  assertBooleanFalse(item.verifiedZeroMinted, 'verified_zero_not_inferred');
}

export function assertCopilotQueryResult(
  result: CopilotQueryResult,
): CopilotQueryResult {
  if (result.schemaVersion !== COPILOT_SESSION_QUERY_SCHEMA) {
    throw new CopilotContractError('invalid_schema_version');
  }

  assertStanding(result.standing);
  assertToken(result.masterRequestId, 'master_request_id');
  assertToken(result.planningSessionId, 'planning_session_id');
  assertToken(result.conversationId, 'conversation_id');
  assertToken(result.correlationId, 'correlation_id');
  assertUtc(result.applicationRequestTimeUtc, 'application_request_time_utc');

  if (
    !MASTER_RESPONSE_POLICY_STATES.includes(
      result.state as MasterResponsePolicyState,
    )
  ) {
    throw new CopilotContractError('invalid_response_policy_state');
  }

  if (result.statusText !== MASTER_RESPONSE_STATUS_TEXT[result.state]) {
    throw new CopilotContractError('invalid_status_text');
  }

  if (result.assistantText !== result.statusText) {
    throw new CopilotContractError('assistant_text_must_match_status_text');
  }

  for (const required of REQUIRED_LIMITATION_CODES) {
    if (!result.limitationCodes.includes(required)) {
      throw new CopilotContractError('missing_response_limitation');
    }
  }

  const allowed = new Set<string>(ALLOWED_LIMITATION_CODES);

  for (const code of result.limitationCodes) {
    if (!allowed.has(code)) {
      throw new CopilotContractError('invalid_limitation_codes');
    }
  }

  assertBooleanFalse(result.claimsAdmitted, 'operational_claim_not_admitted');

  if (result.productionAuthority !== 'DENIED') {
    throw new CopilotContractError('production_authority_not_issued');
  }

  if (result.sourceAuthenticity !== 'NOT_ESTABLISHED') {
    throw new CopilotContractError('source_authenticity_not_established');
  }

  if (result.requestCoverage !== 'NOT_ESTABLISHED') {
    throw new CopilotContractError('request_coverage_not_established');
  }

  if (result.completenessNotice !== LIVE_OPS_COMPLETENESS_NOTICE) {
    throw new CopilotContractError('invalid_completeness_notice');
  }

  if (result.routeDisabledNotice !== ROUTE_DISABLED_NOTICE) {
    throw new CopilotContractError('invalid_route_disabled_notice');
  }

  assertBooleanFalse(result.productionAiActive, 'production_ai_must_remain_inactive');
  assertBooleanFalse(result.verifiedZeroMinted, 'verified_zero_not_inferred');

  if (!Array.isArray(result.evidence)) {
    throw new CopilotContractError('invalid_evidence');
  }

  for (const item of result.evidence) {
    assertEvidenceItem(item);
  }

  if (result.evidence.length === 0 && result.pageEmpty !== true) {
    throw new CopilotContractError('empty_evidence_must_be_page_empty');
  }

  if (result.pageEmpty && result.verifiedZeroMinted !== false) {
    throw new CopilotContractError('verified_zero_not_inferred');
  }

  if (typeof result.truncated !== 'boolean' || typeof result.hasMore !== 'boolean') {
    throw new CopilotContractError('invalid_pagination_flags');
  }

  return result;
}

export function createRouteDisabledResult(
  request: CopilotQueryRequest,
): CopilotQueryResult {
  assertCopilotQueryRequest(request);

  return assertCopilotQueryResult({
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    standing: DISABLED_COPILOT_STANDING,
    masterRequestId: request.masterRequestId,
    planningSessionId: request.planningSessionId,
    conversationId: request.conversationId,
    correlationId: request.correlationId,
    applicationRequestTimeUtc: request.applicationRequestTimeUtc,
    state: 'UNAVAILABLE',
    statusText: MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE,
    assistantText: MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE,
    limitationCodes: [...REQUIRED_LIMITATION_CODES],
    claimsAdmitted: false,
    productionAuthority: 'DENIED',
    sourceAuthenticity: 'NOT_ESTABLISHED',
    requestCoverage: 'NOT_ESTABLISHED',
    completenessNotice: LIVE_OPS_COMPLETENESS_NOTICE,
    routeDisabledNotice: ROUTE_DISABLED_NOTICE,
    productionAiActive: false,
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
    verifiedZeroMinted: false,
  });
}

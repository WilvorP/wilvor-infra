import {
  COPILOT_SESSION_QUERY_SCHEMA,
  INJECTED_COPILOT_STANDING,
  LIVE_OPS_COMPLETENESS_NOTICE,
  MASTER_RESPONSE_STATUS_TEXT,
  REQUIRED_LIMITATION_CODES,
  ROUTE_DISABLED_NOTICE,
  type CopilotLimitationCode,
  type CopilotQueryRequest,
  type CopilotQueryResult,
  type CopilotEvidenceItem,
  type MasterResponsePolicyState,
} from '@/api/aiContracts';

export const TEST_SESSION = {
  planningSessionId: 'plan-testsession0001',
  conversationId: 'conv-testsession0001',
} as const;

export const TEST_REQUEST_IDS = {
  masterRequestId: 'mreq-testrequest0001',
  correlationId: 'corr-testrequest0001',
} as const;

export const TEST_APPLICATION_TIME = '2026-11-07T18:00:00Z';

export function testRequest(
  overrides: Partial<CopilotQueryRequest> = {},
): CopilotQueryRequest {
  return {
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    liveOpsSchemaVersion: 'wilvor.ai.live_ops_public.v1',
    masterRequestId: TEST_REQUEST_IDS.masterRequestId,
    planningSessionId: TEST_SESSION.planningSessionId,
    conversationId: TEST_SESSION.conversationId,
    correlationId: TEST_REQUEST_IDS.correlationId,
    applicationRequestTimeUtc: TEST_APPLICATION_TIME,
    userText: 'What is the current state of UAL9?',
    priorTurns: [],
    selectorHints: null,
    ...overrides,
  };
}

function baseResult(
  request: CopilotQueryRequest,
  state: MasterResponsePolicyState,
  extraLimitations: readonly CopilotLimitationCode[] = [],
): Omit<CopilotQueryResult, 'evidence' | 'pageEmpty' | 'truncated' | 'hasMore'> {
  const limitationCodes = [
    ...REQUIRED_LIMITATION_CODES,
    ...extraLimitations,
  ].filter((code, index, all) => all.indexOf(code) === index);

  return {
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    standing: INJECTED_COPILOT_STANDING,
    masterRequestId: request.masterRequestId,
    planningSessionId: request.planningSessionId,
    conversationId: request.conversationId,
    correlationId: request.correlationId,
    applicationRequestTimeUtc: request.applicationRequestTimeUtc,
    state,
    statusText: MASTER_RESPONSE_STATUS_TEXT[state],
    assistantText: MASTER_RESPONSE_STATUS_TEXT[state],
    limitationCodes,
    claimsAdmitted: false,
    productionAuthority: 'DENIED',
    sourceAuthenticity: 'NOT_ESTABLISHED',
    requestCoverage: 'NOT_ESTABLISHED',
    completenessNotice: LIVE_OPS_COMPLETENESS_NOTICE,
    routeDisabledNotice: ROUTE_DISABLED_NOTICE,
    productionAiActive: false,
    verifiedZeroMinted: false,
  };
}

export function unavailableResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNAVAILABLE'),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export function unsupportedResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNSUPPORTED', [
      'REQUEST_COVERAGE_NOT_ESTABLISHED',
    ]),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export function insufficientEvidenceResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'INSUFFICIENT_EVIDENCE', [
      'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
    ]),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export const AIRCRAFT_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'AIRCRAFT',
  entityId: 'aa0001',
  callsign: 'UAL9',
  aircraftId: 'aa0001',
  hazardId: null,
  encounterId: null,
  toolName: 'get_aircraft_current_state',
  toolCallId: 'tool-aircraft-1',
  sourceName: 'wilvor_aircraft_current_state',
  sourceVersion: 'v-aircraft-17',
  observationTimeUtc: '2026-11-07T17:59:40Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: 'FRESH',
  limitationCodes: ['SOURCE_AUTHENTICITY_NOT_ESTABLISHED'],
  truncated: false,
  hasMore: false,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export const HAZARD_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'HAZARD',
  entityId: 'sigmet-a',
  callsign: null,
  aircraftId: null,
  hazardId: 'sigmet-a',
  encounterId: null,
  toolName: 'get_hazard',
  toolCallId: 'tool-hazard-1',
  sourceName: 'wilvor_active_hazards',
  sourceVersion: 'v-hazard-4',
  observationTimeUtc: '2026-11-07T17:50:00Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: 'AVAILABLE',
  limitationCodes: [
    'SOURCE_AUTHENTICITY_NOT_ESTABLISHED',
    'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
  ],
  truncated: false,
  hasMore: false,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export const ENCOUNTER_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'ENCOUNTER',
  entityId: 'enc-aa0001-sigmet-a',
  callsign: 'UAL9',
  aircraftId: 'aa0001',
  hazardId: 'sigmet-a',
  encounterId: 'enc-aa0001-sigmet-a',
  toolName: 'get_encounter',
  toolCallId: 'tool-encounter-1',
  sourceName: 'wilvor_active_encounters',
  sourceVersion: null,
  observationTimeUtc: '2026-11-07T17:58:12Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: null,
  limitationCodes: ['SOURCE_AUTHENTICITY_NOT_ESTABLISHED'],
  truncated: true,
  hasMore: true,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export function evidenceDisplayResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'INSUFFICIENT_EVIDENCE', [
      'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
      'LIVE_OPS_REFS_NOT_TOOL_RESULTS',
    ]),
    evidence: [AIRCRAFT_EVIDENCE, HAZARD_EVIDENCE, ENCOUNTER_EVIDENCE],
    pageEmpty: false,
    truncated: true,
    hasMore: true,
  };
}

export function emptyEvidenceResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNAVAILABLE'),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

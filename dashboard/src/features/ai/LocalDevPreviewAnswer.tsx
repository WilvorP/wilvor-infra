import type { CopilotDeveloperToolResult, CopilotQueryResult } from '@/api/aiContracts';

import styles from './ConversationTranscript.module.css';

/**
 * Time between the stored aircraft observation and the request clock.
 * This is descriptive only, never an independent freshness verdict.
 */
export function describeObservationAge(
  positionTimeUtc: string | null,
  requestTimeUtc: string,
): string | null {
  if (!positionTimeUtc) {
    return null;
  }
  const observedMs = Date.parse(positionTimeUtc);
  const requestMs = Date.parse(requestTimeUtc);
  if (!Number.isFinite(observedMs) || !Number.isFinite(requestMs)) {
    return null;
  }

  const deltaSeconds = Math.floor(Math.abs(requestMs - observedMs) / 1000);
  const hours = Math.floor(deltaSeconds / 3600);
  const minutes = Math.floor((deltaSeconds % 3600) / 60);
  const seconds = deltaSeconds % 60;
  const elapsed = (hours > 0 ? String(hours) + 'h ' : '') +
    String(minutes) + 'm ' + String(seconds) + 's';

  if (observedMs > requestMs) {
    return 'Stored position time is ' + elapsed +
      ' after the request clock; clock alignment needs review.';
  }
  return 'Stored position time was ' + elapsed + ' before the request.';
}

function AircraftObservation({
  tool,
  requestTimeUtc,
}: {
  tool: CopilotDeveloperToolResult;
  requestTimeUtc: string;
}) {
  const age = describeObservationAge(tool.positionTimeUtc, requestTimeUtc);

  return (
    <section className={styles.previewEntity} aria-label="Stored aircraft observation">
      <p className={styles.previewEntityTitle}>
        Stored aircraft {tool.aircraftId ?? 'not reported'}
        {tool.callsign ? ' (' + tool.callsign + ')' : ''}
        {' — '}{tool.recordState.toLowerCase()} record
      </p>
      {tool.positionTimeUtc ? (
        <p className={styles.previewDetail}>Position timestamp: {tool.positionTimeUtc}</p>
      ) : null}
      {age ? <p className={styles.previewDetail}>{age}</p> : null}
      {(tool.observations?.length ?? 0) > 0 ? (
        <dl className={styles.previewFields}>
          {tool.observations?.map((observation) => (
            <div key={observation.field} className={styles.previewField}>
              <dt>{observation.label}</dt>
              <dd>{observation.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={styles.previewDetail}>No observed scalar fields returned.</p>
      )}
      <p className={styles.previewCaution}>
        Stored freshness label: {tool.freshnessStatus ?? 'not reported'}.
        This stored label is not an independent source-freshness check.
      </p>
    </section>
  );
}

/**
 * Human-readable local observations, not a second authoritative answer.
 * All facts shown here come from the validated local preview envelope.
 * Production response fields and the fail-closed contract remain unchanged.
 */
export function LocalDevPreviewAnswer({ result }: { result: CopilotQueryResult }) {
  const preview = result.developerPreview;
  if (!preview) {
    return null;
  }

  const aircraft = preview.toolResults.filter(
    (tool) => tool.toolName === 'get_aircraft_operational_context',
  );
  const encounters = preview.toolResults.filter(
    (tool) => tool.toolName === 'search_current_encounters',
  );
  const other = preview.toolResults.filter(
    (tool) =>
      tool.toolName !== 'get_aircraft_operational_context' &&
      tool.toolName !== 'search_current_encounters',
  );
  const noObservations = preview.toolResults.length === 0;

  return (
    <section className={styles.previewWrap} data-testid="copilot-developer-preview">
      <p className={styles.previewHeading}>Local developer observations</p>
      <p className={styles.previewCaution}>
        NOT PRODUCTION. These are operator-authenticated DEV reads, not admitted
        operational claims. Source authenticity is not established.
      </p>
      <p className={styles.previewDetail}>
        Master route: {preview.routeStatus}
        {preview.routeKinds?.length ? ' (' + preview.routeKinds.join(', ') + ')' : ''}.
        {preview.specialistStatusText
          ? ' Specialist verification text is available below.'
          : ' No completed specialist verification text is available.'}
      </p>
      {noObservations ? (
        <p className={styles.previewDetail}>
          No specialist tool observations were returned. No aircraft or hazard status is inferred.
        </p>
      ) : null}
      {aircraft.map((tool) => (
        <AircraftObservation
          key={tool.toolCallId}
          tool={tool}
          requestTimeUtc={result.applicationRequestTimeUtc}
        />
      ))}
      {encounters.map((tool) => (
        <p key={tool.toolCallId} className={styles.previewDetail}>
          Encounter query for aircraft {tool.aircraftId ?? 'not reported'}:
          {' '}{tool.encounterCandidateCount === null
            ? 'count not reported'
            : String(tool.encounterCandidateCount) + ' returned candidate rows'}.
          {' '}A returned count, including zero, does not establish that an aircraft
          is clear of hazards.
        </p>
      ))}
      {other.map((tool) => (
        <p key={tool.toolCallId} className={styles.previewDetail}>
          Tool {tool.toolName}: {tool.status} ({tool.recordState}).
          No additional operational conclusion is inferred.
        </p>
      ))}
      {preview.blockedSpecialists.map((blocked) => (
        <p key={blocked.specialist} className={styles.previewCaution}>
          {blocked.specialist} not executed: {blocked.reason}
        </p>
      ))}
      {preview.modelErrors.length > 0 ? (
        <p className={styles.previewCaution}>
          Model diagnostics: {preview.modelErrors.join(', ')}
        </p>
      ) : null}
      {preview.toolErrors.length > 0 ? (
        <p className={styles.previewCaution}>
          Tool diagnostics: {preview.toolErrors.join(', ')}
        </p>
      ) : null}

      <details className={styles.previewTechnical}>
        <summary>Specialist verification and technical details</summary>
        <p>{preview.notice}</p>
        {preview.specialistStatusText ? (
          <p>
            Specialist verification output (local DEV only):
            {' '}{preview.specialistStatusText}
          </p>
        ) : null}
        <p>Diagnostic narrative: {preview.answer}</p>
        <p>Model calls recorded: {preview.modelOperations.length}</p>
        {preview.awsOperations.length > 0 ? (
          <ul>
            {preview.awsOperations.map((operation, index) => (
              <li key={index}>
                {operation.provenance} {operation.operation} {operation.table}
              </li>
            ))}
          </ul>
        ) : null}
      </details>
    </section>
  );
}

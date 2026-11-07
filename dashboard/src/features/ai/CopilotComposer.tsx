import { USER_TEXT_MAX_LENGTH } from '@/api/aiContracts';

import styles from './CopilotComposer.module.css';

export interface CopilotComposerProps {
  value: string;
  pending: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}

export function CopilotComposer({
  value,
  pending,
  disabled = false,
  onChange,
  onSubmit,
}: CopilotComposerProps) {
  const remaining = USER_TEXT_MAX_LENGTH - value.length;
  const canSubmit = value.trim().length > 0 && !pending && !disabled;

  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();

        if (canSubmit) {
          onSubmit();
        }
      }}
    >
      <label className={styles.label} htmlFor="copilot-input">
        Operator question
      </label>
      <textarea
        id="copilot-input"
        className={styles.input}
        value={value}
        maxLength={USER_TEXT_MAX_LENGTH}
        rows={3}
        disabled={pending || disabled}
        placeholder="Ask about current aircraft or hazard context"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();

            if (canSubmit) {
              onSubmit();
            }
          }
        }}
      />
      <div className={styles.bar}>
        <p className={styles.hint}>
          Advisory only — not ATC. Shift+Enter adds a line.
        </p>
        <span className={`${styles.count} wv-numeric`} aria-live="polite">
          {remaining}
        </span>
        <button type="submit" className={styles.submit} disabled={!canSubmit}>
          {pending ? 'Sending' : 'Ask'}
        </button>
      </div>
    </form>
  );
}

import type { ReactNode } from 'react';

import { describeApiError, isApiError } from '@/api/errors';

import styles from './QueryState.module.css';

export interface LoadingStateProps {
  label?: string;
}

export function LoadingState({ label = 'Loading' }: LoadingStateProps) {
  return (
    <div className={styles.state} role="status">
      <span className={styles.spinner} aria-hidden="true" />
      <p className={styles.title}>{label}</p>
    </div>
  );
}

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  /** What the operator could not see, e.g. "operations overview". */
  subject?: string;
}

export function ErrorState({ error, onRetry, subject }: ErrorStateProps) {
  const retryable = !isApiError(error) || error.isRetryable;

  return (
    <div className={styles.state} role="alert">
      <p className={styles.errorTitle}>
        <span aria-hidden="true">⚠ </span>
        {subject ? `Cannot load ${subject}` : 'Request failed'}
      </p>
      <p className={styles.detail}>{describeApiError(error)}</p>
      {onRetry && retryable ? (
        <button type="button" className={styles.retry} onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  /**
   * Why the view is empty. An operations console must distinguish "nothing is
   * happening" from "we could not find out", so this should say which.
   */
  detail?: string;
  children?: ReactNode;
}

export function EmptyState({ title, detail, children }: EmptyStateProps) {
  return (
    <div className={styles.state}>
      <p className={styles.title}>{title}</p>
      {detail ? <p className={styles.detail}>{detail}</p> : null}
      {children}
    </div>
  );
}

export interface NoticeProps {
  tone?: 'info' | 'warning';
  children: ReactNode;
}

/** Inline advisory, used for partial data and unimplemented capability. */
export function Notice({ tone = 'info', children }: NoticeProps) {
  return (
    <p className={`${styles.notice} ${styles[tone]}`}>
      <span aria-hidden="true">{tone === 'warning' ? '⚠' : 'ℹ'}</span>
      <span>{children}</span>
    </p>
  );
}

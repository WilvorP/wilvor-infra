import type { ReactNode } from 'react';

const ICON_PATHS: Record<string, ReactNode> = {
  '/': (
    <>
      <path d="M9 4.5 3.5 6.8v12.7L9 17.2l6 2.3 5.5-2.3V4.5L15 6.8 9 4.5Z" />
      <path d="M9 4.5v12.7M15 6.8v12.7" />
    </>
  ),
  '/aircraft': (
    <path d="M12 3c.8 0 1.3.9 1.3 2v4.3l7.2 4.3v1.9l-7.2-2.1v4.1l2.1 1.6v1.6L12 20l-3.4.7v-1.6l2.1-1.6v-4.1l-7.2 2.1v-1.9l7.2-4.3V5c0-1.1.5-2 1.3-2Z" />
  ),
  '/airports': (
    <>
      <path d="M12 20.5s-6.3-5.4-6.3-10.6a6.3 6.3 0 0 1 12.6 0c0 5.2-6.3 10.6-6.3 10.6Z" />
      <circle cx="12" cy="9.9" r="2.2" />
    </>
  ),
  '/encounters': (
    <>
      <circle cx="9.2" cy="12" r="5.4" />
      <circle cx="14.8" cy="12" r="5.4" />
    </>
  ),
  '/recommendations': (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15.4 8.6-2 4.8-4.8 2 2-4.8 4.8-2Z" />
    </>
  ),
  '/alerts': (
    <>
      <path d="M6.2 16.2v-5a5.8 5.8 0 0 1 11.6 0v5l1.6 2H4.6l1.6-2Z" />
      <path d="M10.1 20.4a2 2 0 0 0 3.8 0" />
    </>
  ),
  '/system-health': <path d="M3 12.5h4l2.4-6 5.2 12 2.4-6H21" />,
};

/** Workflow glyph for the sidebar. Decorative: the label carries the name. */
export function NavIcon({ path }: { path: string }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICON_PATHS[path] ?? <circle cx="12" cy="12" r="3" />}
    </svg>
  );
}

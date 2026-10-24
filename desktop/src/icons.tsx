// Menu icons, drawn inline (no icon library: the window loads nothing from the network, and five shapes do
// not justify a dependency). Decorative: the menu text says what each one is.
import type { ReactNode } from "react";

function Icon({ children, size = 20 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">{children}</svg>
  );
}

export const icons = {
  jobs: <Icon><path d="M3 8h18v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M8 8V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3" /></Icon>,
  review: <Icon><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7" /><path d="M12 17h.01" /></Icon>,
  applications: <Icon><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4h6v3H9z" /><path d="m9 14 2 2 4-4" /></Icon>,
  profile: <Icon><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></Icon>,
  preferences: <Icon><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></Icon>,
  setup: <Icon><path d="m5 12 4.5 4.5L19 7" /></Icon>,
  back: <Icon size={18}><path d="m15 6-6 6 6 6" /></Icon>,
  check: <Icon size={15}><path d="m5 12 4.5 4.5L19 7" /></Icon>,
  stretch: <Icon size={15}><path d="M7 17 17 7M9 7h8v8" /></Icon>,
  question: <Icon size={15}><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7" /><path d="M12 17h.01" /></Icon>,
  cross: <Icon size={15}><path d="M6 6l12 12M18 6 6 18" /></Icon>,
  external: <Icon size={17}><path d="M14 4h6v6" /><path d="M20 4 11 13" /><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></Icon>,
  hide: <Icon size={17}><path d="M3 3l18 18" /><path d="M10.6 6.1A9.7 9.7 0 0 1 12 6c5 0 8.5 4.5 9.5 6-.5.8-1.6 2.3-3.2 3.6M6.5 7.6C4.6 8.9 3.2 10.8 2.5 12c1 1.5 4.5 6 9.5 6 1.5 0 2.9-.4 4.1-1" /></Icon>,
  upload: <Icon size={17}><path d="M12 16V4" /><path d="m7 9 5-5 5 5" /><path d="M5 20h14" /></Icon>,
};

/** The JobForge mark in the menu. */
export function Logo() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
      <rect width="30" height="30" rx="8" fill="var(--btn)" />
      <path d="M8 12h14v9H8z M12 12V9.5h6V12" fill="none" stroke="var(--btn-text)" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

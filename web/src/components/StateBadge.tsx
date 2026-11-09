import type { HotState, Session } from '../api/types';
import { sessionLabel, trendLabel } from '../lib/format';

const HOT_TONE: Record<HotState, string> = {
  hot: 'badge-up',
  warming: 'badge-gold',
  crowded: 'badge-warn',
  cooling: 'badge-cyan',
  fading: 'badge-down',
  neutral: 'badge-muted',
  unavailable: 'badge-faint',
};

export function HotStateBadge({
  state,
  label,
}: {
  state: HotState;
  label?: string;
}): React.JSX.Element {
  return (
    <span className={`state-badge ${HOT_TONE[state] ?? 'badge-muted'}`}>
      {label ?? state}
    </span>
  );
}

const TREND_TONE: Record<string, string> = {
  down_left: 'badge-down',
  down_right: 'badge-down',
  up_left: 'badge-up',
  up_right: 'badge-up',
  range: 'badge-muted',
};

export function TrendBadge({ state }: { state: string }): React.JSX.Element {
  return (
    <span className={`state-badge ${TREND_TONE[state] ?? 'badge-muted'}`}>
      {trendLabel(state)}
    </span>
  );
}

export function SessionChip({ session }: { session: Session }): React.JSX.Element {
  const live = session === 'morning' || session === 'afternoon';
  return (
    <span className={`state-badge ${live ? 'badge-up' : 'badge-muted'}`}>
      {sessionLabel(session)}
      {live ? <span className="sr-only">（交易中）</span> : null}
    </span>
  );
}

export function LabelBadge({
  children,
  tone = 'muted',
}: {
  children: React.ReactNode;
  tone?: 'gold' | 'muted' | 'up' | 'down' | 'cyan' | 'warn' | 'faint';
}): React.JSX.Element {
  return <span className={`state-badge state-badge--plain badge-${tone}`}>{children}</span>;
}

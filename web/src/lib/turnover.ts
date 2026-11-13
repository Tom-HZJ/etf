import type { TurnoverView } from '../api/types';

export function turnoverFreshness(data: TurnoverView, now: number, failed = false): { quote: boolean; minute: boolean } {
  const unexpired = (time: string | null): boolean => !time || now <= Date.parse(time);
  return {
    quote: !failed && data.state !== 'stale' && unexpired(data.valid_until),
    minute: !failed && !data.minute_stale && unexpired(data.minute_valid_until),
  };
}

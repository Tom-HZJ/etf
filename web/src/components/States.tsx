import type { ReactNode } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { EmptyState } from '@astryxdesign/core/EmptyState';
import { Icon } from '@astryxdesign/core/Icon';
import { Skeleton } from '@astryxdesign/core/Skeleton';
import { Tooltip } from '@astryxdesign/core/Tooltip';
import { CloudOff, RefreshCw } from 'lucide-react';
import { isMock } from '../api/http';

export function PanelSkeleton({
  rows = 3,
  chart = false,
}: {
  rows?: number;
  chart?: boolean;
}): React.JSX.Element {
  return (
    <div className="panel" role="status" aria-label="正在加载数据">
      <div className="panel__body space-y-3">
        <Skeleton width="32%" height={10} />
        {chart ? <Skeleton width="100%" height={180} radius={2} index={1} /> : null}
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="space-y-2" style={{ opacity: 1 - index * 0.14 }}>
            <Skeleton width={`${88 - index * 9}%`} height={12} index={index + 2} />
            <Skeleton width={`${54 - index * 4}%`} height={9} index={index + 3} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function ErrorPanel({
  error,
  onRetry,
  title = '数据暂时不可用',
  className = '',
}: {
  error: string | null;
  onRetry: () => void;
  title?: string;
  className?: string;
}): React.JSX.Element {
  return (
    <div className={`panel ${className}`}>
      <EmptyState
        isCompact
        title={title}
        description={error ?? '请检查网络或稍后重试。'}
        icon={<Icon icon={CloudOff} size="lg" color="secondary" />}
        actions={
          <Button
            label="重新加载"
            size="sm"
            variant="secondary"
            icon={<Icon icon={RefreshCw} size="sm" />}
            onClick={onRetry}
          />
        }
      />
    </div>
  );
}

export function EmptyPanel({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="panel">
      <EmptyState isCompact title={title} description={description} actions={actions} />
    </div>
  );
}

export function MockChip(): React.JSX.Element | null {
  if (!isMock) return null;
  return (
    <Tooltip
      content="当前为 mock 示例数据（2026-11-06 14:32 快照，非真实行情）。切换 live：VITE_API_MODE=live"
      placement="below"
    >
      <span className="state-badge badge-gold" tabIndex={0}>
        MOCK<span className="hidden sm:inline"> · 示例数据</span>
      </span>
    </Tooltip>
  );
}

export function OfflineStrip(): React.JSX.Element {
  return (
    <div className="notice notice--error" role="status">
      <span className="mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full bg-[var(--etf-up)]" aria-hidden="true" />
      网络已断开；页面保留最近一次成功的数据，恢复后会自动重试。
    </div>
  );
}

export function InlineLoading({ text = '刷新中…' }: { text?: string }): React.JSX.Element {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] faint">
      <span
        className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-[var(--etf-gold)] border-t-transparent"
        aria-hidden="true"
      />
      {text}
    </span>
  );
}

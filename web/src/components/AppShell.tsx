import type { ReactNode } from 'react';
import { Icon } from '@astryxdesign/core/Icon';
import { IconButton } from '@astryxdesign/core/IconButton';
import { StatusDot } from '@astryxdesign/core/StatusDot';
import { LayoutGrid, Landmark, LineChart, LogOut, Moon, Radar, RefreshCw, Sun, Database, Wallet } from 'lucide-react';
import type { Quote, Session, SourceHealth, AuthMode } from '../api/types';
import { deltaClass, fmtPct, fmtNum, fmtDateTime, sessionLabel } from '../lib/format';
import { ROUTES, hrefFor, routeMeta, type RouteKey } from '../lib/router';
import { prefetchRoute } from '../lib/prefetch';
import type { ThemeMode } from '../lib/hooks';
import { MockChip } from './States';

const NAV_ICONS: Record<RouteKey, typeof LayoutGrid> = {
  overview: LayoutGrid,
  team: Landmark,
  hotspots: Radar,
  market: LineChart,
  data: Database,
  research: LineChart,
  stocks: LineChart,
  funds: Wallet,
};

function Brand({ compact = false }: { compact?: boolean }): React.JSX.Element {
  return (
    <div className="flex items-center gap-3">
      <span className="brandmark shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <div className={`font-semibold tracking-[0.05em] text-[var(--etf-ink)] ${compact ? 'text-[13px]' : 'text-[14px]'}`}>
          ETF 观察台
        </div>
        <div className="mono text-[8px] tracking-[0.18em] text-[var(--etf-faint)]">
          OBSERVATORY · PRIVATE
        </div>
      </div>
    </div>
  );
}

function Rail({
  route,
  mode,
  onToggleTheme,
  health,
  user,
  authMode,
  onLogout,
}: {
  route: RouteKey;
  mode: ThemeMode;
  onToggleTheme: () => void;
  health: { status: 'ok' | 'degraded' } | null;
  user: string | null;
  authMode: AuthMode;
  onLogout: () => void;
}): React.JSX.Element {
  const healthy = health ? health.status === 'ok' : null;
  return (
    <aside className="rail" aria-label="主导航">
      <div className="rail__brand">
        <Brand />
      </div>
      <nav className="rail__nav">
        {ROUTES.filter(item => !item.private || user === 'tom').map((item) => (
          <a
            key={item.key}
            className="navlink"
            href={hrefFor(item.key)}
            aria-current={route === item.key ? 'page' : undefined}
            onMouseEnter={() => prefetchRoute(item.key)}
            onFocus={() => prefetchRoute(item.key)}
          >
            <span className="navlink__num">{item.index}</span>
            <span>
              <span className="navlink__label">{item.label}</span>
              <span className="navlink__en">{item.en}</span>
            </span>
          </a>
        ))}
      </nav>
      <div className="rail__foot">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[10.5px] text-[var(--etf-muted)]">
            <StatusDot
              variant={healthy === null ? 'neutral' : healthy ? 'success' : 'warning'}
              label={healthy === null ? '数据状态未知' : healthy ? '数据源正常' : '数据源告警'}
            />
            {healthy === null ? '连接中…' : healthy ? '数据源正常' : '数据源告警'}
          </div>
          <button
            type="button"
            className="nav-action"
            onClick={onToggleTheme}
            aria-label={mode === 'dark' ? '切换到浅色主题' : '切换到暗色主题'}
          >
            <Icon icon={mode === 'dark' ? Sun : Moon} size="sm" />
            {mode === 'dark' ? '浅色' : '暗色'}
          </button>
        </div>
        <div className="mono mt-3 flex items-center justify-between text-[9px] tracking-[0.08em] text-[var(--etf-faint)]">
          <span>v{__ETF_RELEASE_VERSION__} · CONTRACT v2.2</span>
          <span>SHANGHAI</span>
        </div>
        <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-[var(--etf-line)] pt-2.5">
          <span className="mono max-w-[120px] truncate text-[9.5px] tracking-[0.05em] text-[var(--etf-muted)]">
            {user ? `${user} · ${authMode === 'forward' ? 'Authelia' : '本地'}` : '已登录'}
          </span>
          <button type="button" className="nav-action" onClick={onLogout} aria-label="退出登录">
            <LogOut size={12} aria-hidden="true" />
            退出登录
          </button>
        </div>
      </div>
    </aside>
  );
}

function TopBar({
  route,
  mode,
  onToggleTheme,
  session,
  asOf,
  refreshing,
  onRefresh,
  health,
}: {
  route: RouteKey;
  mode: ThemeMode;
  onToggleTheme: () => void;
  session: Session;
  asOf: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  health: { status: 'ok' | 'degraded' } | null;
}): React.JSX.Element {
  const meta = routeMeta(route);
  return (
    <div className="topbar">
      <div className="topbar__inner">
        <div className="lg:hidden">
          <Brand compact />
        </div>
        <div className="hidden items-center gap-2 lg:flex">
          <span className="eyebrow eyebrow--muted">ACTION DESK</span>
          <span className="divider-dot" aria-hidden="true" />
          <span className="text-[11.5px] tracking-[0.08em] text-[var(--etf-muted)]">{meta.label}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-1.5 text-[10.5px] text-[var(--etf-muted)] md:flex">
            <StatusDot
              variant={health === null ? 'neutral' : health.status === 'ok' ? 'success' : 'warning'}
              label={health === null ? '数据状态加载中' : health.status === 'ok' ? '数据健康' : '数据降级'}
            />
            {health === null ? '—' : health.status === 'ok' ? 'HEALTHY' : 'DEGRADED'}
          </span>
          <span className="state-badge badge-muted hidden sm:inline-flex">
            {sessionLabel(session)}
          </span>
          <span className="mono hidden text-[10px] tracking-[0.06em] text-[var(--etf-faint)] sm:inline">
            {asOf ? `行情 ${fmtDateTime(asOf)}` : '—'}
          </span>
          <MockChip />
          <IconButton
            label="刷新全部数据"
            icon={<Icon icon={RefreshCw} size="sm" />}
            variant="ghost"
            size="sm"
            isLoading={refreshing}
            onClick={onRefresh}
          />
          <IconButton
            label={mode === 'dark' ? '切换到浅色主题' : '切换到暗色主题'}
            icon={<Icon icon={mode === 'dark' ? Sun : Moon} size="sm" />}
            variant="ghost"
            size="sm"
            onClick={onToggleTheme}
          />
        </div>
      </div>
    </div>
  );
}

function Tape({ indices }: { indices: Quote[] | undefined }): React.JSX.Element {
  return (
    <div className="tape" aria-label="主要指数">
      <div className="tape__track">
        {(indices ?? []).map((quote) => (
          <a
            key={quote.code}
            className="tape__cell group"
            href={hrefFor('market', { code: quote.code })}
            aria-label={`${quote.name} ${fmtNum(quote.price, 2)} ${fmtPct(quote.change_pct)}，查看行情`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-[10.5px] tracking-[0.04em] text-[var(--etf-muted)]">{quote.name}</span>
              <span className="mono text-[8.5px] tracking-[0.06em] text-[var(--etf-faint)]">
                {quote.code.slice(2)}
              </span>
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="num text-[14.5px] font-semibold text-[var(--etf-ink)]">{fmtNum(quote.price, 2)}</span>
              <span className={`num text-[11px] font-medium ${deltaClass(quote.change_pct)}`}>
                {fmtPct(quote.change_pct)}
              </span>
            </div>
          </a>
        ))}
        {(!indices || indices.length === 0) ? (
          <div className="tape__cell text-[10.5px] text-[var(--etf-faint)]">指数加载中…</div>
        ) : null}
      </div>
    </div>
  );
}

function MobileBar({ route, user }: { route: RouteKey; user: string | null }): React.JSX.Element {
  return (
    <nav className="mobilebar" data-private={user === 'tom' ? 'true' : undefined} aria-label="主导航（移动端）">
      {ROUTES.filter(item => !item.private || user === 'tom').map((item) => {
        const IconComponent = NAV_ICONS[item.key];
        return (
          <a
            key={item.key}
            href={hrefFor(item.key)}
            aria-current={route === item.key ? 'page' : undefined}
            style={{ position: 'relative' }}
            onTouchStart={() => prefetchRoute(item.key)}
          >
            <IconComponent size={18} strokeWidth={1.7} aria-hidden="true" />
            <span>{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}

export function AppShell({
  route,
  mode,
  onToggleTheme,
  indices,
  session,
  asOf,
  refreshing,
  onRefresh,
  health,
  user,
  authMode,
  onLogout,
  children,
}: {
  route: RouteKey;
  mode: ThemeMode;
  onToggleTheme: () => void;
  indices: Quote[] | undefined;
  session: Session;
  asOf: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  health: { status: 'ok' | 'degraded'; sources?: SourceHealth[] } | null;
  user: string | null;
  authMode: AuthMode;
  onLogout: () => void;
  children: ReactNode;
}): React.JSX.Element {
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主内容
      </a>
      <div className="app-shell">
        <Rail
          route={route}
          mode={mode}
          onToggleTheme={onToggleTheme}
          health={health}
          user={user}
          authMode={authMode}
          onLogout={onLogout}
        />
        <div className="app-main">
          <TopBar
            route={route}
            mode={mode}
            onToggleTheme={onToggleTheme}
            session={session}
            asOf={asOf}
            refreshing={refreshing}
            onRefresh={onRefresh}
            health={health}
          />
          <Tape indices={indices} />
          <div className="content">
            <main id="main">{children}</main>
            <footer className="mt-10 border-t border-[var(--etf-line)] pt-4 text-[10.5px] leading-relaxed text-[var(--etf-faint)]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>ETF 观察台 · 私有研究站 · 数据仅供研究，不构成投资建议</span>
                <span className="mono">Asia/Shanghai · v{__ETF_RELEASE_VERSION__}</span>
              </div>
            </footer>
          </div>
        </div>
      </div>
      <MobileBar route={route} user={user} />
    </>
  );
}

import { CoveragePanel } from '../components/CoveragePanel';
import { useEffect, useMemo, useState } from 'react';
import { Badge } from '@astryxdesign/core/Badge';
import { Button } from '@astryxdesign/core/Button';
import { IconButton } from '@astryxdesign/core/IconButton';
import { Table, proportional, pixel } from '@astryxdesign/core/Table';
import type { TableColumn } from '@astryxdesign/core/Table';
import { CheckCircle2, CircleAlert, KeyRound, LogOut, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import type { AuthMode, HealthResponse, MetaResponse, SourceStatus, SourcesResponse } from '../api/types';
import { fetchAdminSessions, fetchHealth, fetchMeta, fetchSources, isMock, revokeAllSessions } from '../api/client';
import { HttpError } from '../api/http';
import { useResource } from '../lib/hooks';
import { fmtCode, fmtDateTime, fmtNum, sessionLabel } from '../lib/format';
import { PageHeader, Panel, SectionHead } from '../components/Page';
import { ErrorPanel, PanelSkeleton } from '../components/States';
import { LabelBadge } from '../components/StateBadge';

const DEF_LABEL: Record<string, string> = {
  net_flow: '净申赎（估算）',
  heat: '热度分',
  trend: '趋势结构',
  breadth: '市场宽度',
  flow: '份额申赎',
};

const SOURCE_LABEL: Record<string, string> = {
  tencent_quote: '腾讯 · 报价',
  tencent_bars: '腾讯 · 日线',
  tencent_intraday: '腾讯 · 分时',
  eastmoney_quote: '东财 · 报价兜底',
  eastmoney_fund: '东财 · 基金档案',
  tencent: '腾讯行情',
  eastmoney: '东方财富',
};

const CHANGELOG = [
  {
    version: 'v2.4.0',
    date: '2026-11-08',
    items: [
      '分钟图固定窗口、滚轮不缩放、去掉悬停数值浮层；60s 可见轮询。',
      '行情日/周/月 K 支持滚轮缩放、区间按钮与首收盘基准线/区间涨跌。',
      '删除近 5 日分时与分钟明细表；总览改为左 9 栏 + 右 3 栏（自选 + 市场宽度）。',
    ],
  },
  {
    version: 'v2.1.0',
    date: '2026-11-08',
    items: [
      '总览同花顺式主内容 + 右侧自选栏；移除核心指数卡与数据健康卡。',
      '行情分钟周期改为折线 + MA5/10/20；新增国家队今日分钟走势（1/5/15/30/60）。',
      '热点雷达删除四路证据研究员与方法/限制段；数据源健康收敛为 10 源。',
    ],
  },
  {
    version: 'v2.0.0',
    date: '2026-11-08',
    items: [
      'Authelia 前向认证 / 本地登录双模式；会话守卫与退出分流。',
      '数据页「安全 / 会话」：管理员注销所有设备（Redis 会话吊销）。',
      '数据源可观测性：10 个上游的延迟、成功/失败/回退、熔断与告警。',
      '路由级拆包与图表懒加载；首屏 gzip < 250KB。',
      '分钟 K（1/5/15/30/60 分钟）与日/周/月统一周期切换。',
      '长列表窗口化（热点 20 行、实体 25 行）；移动端 6 指数横向条。',
    ],
  },
  {
    version: 'v1.0.0',
    date: '2026-11-08',
    items: [
      '五个研究视图：总览 / 国家队 / 热点雷达 / 行情 / 数据。',
      '国家队盘中估算、份额加权合成 K 线、10% 趋势结构与报告期申赎。',
      '59 只方向 ETF 的热度雷达、四路证据研究员与排名变化。',
      '自选增删改、日/周/月 K 线、5 日分时与区间统计。',
      '暗色默认 / 浅色切换、移动端导航、prefers-reduced-motion 支持。',
    ],
  },
];

interface EntityRow extends Record<string, unknown> {
  code: string;
  name: string;
  group: string;
}

function uptimeText(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours <= 0) return `${minutes} 分钟`;
  return `${hours} 小时 ${minutes} 分`;
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}): React.JSX.Element {
  return (
    <div className="bg-[var(--etf-surface)] px-4 py-3">
      <div className="metric__label">{label}</div>
      <div className="mid-num mt-1">{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

function HealthPanel({ health }: { health: HealthResponse }): React.JSX.Element {
  return (
    <Panel
      feature
      title="运行状态"
      sub="etf-monitor · ThreadingHTTPServer"
      actions={
        <Badge
          label={health.status === 'ok' ? '运行正常' : '降级运行'}
          variant={health.status === 'ok' ? 'success' : 'warning'}
        />
      }
      bodyClassName=""
    >
      <div className="grid grid-cols-2 gap-px bg-[var(--etf-line)] sm:grid-cols-3">
        <Stat label="服务版本" value={<span className="mono text-[14px]">{health.version}</span>} hint={health.trade_date} />
        <Stat label="已运行" value={<span className="text-[15px]">{uptimeText(health.uptime_s)}</span>} />
        <Stat label="会话" value={sessionLabel(health.session)} hint={health.trade_date} />
        <Stat
          label="最近盘中采样"
          value={<span className="mono text-[12px]">{health.last_intraday_sample ? fmtDateTime(health.last_intraday_sample).slice(5) : '—'}</span>}
          hint="Asia/Shanghai"
        />
        <Stat
          label="最近日终刷新"
          value={<span className="mono text-[12px]">{health.last_daily_refresh ? fmtDateTime(health.last_daily_refresh).slice(5) : '—'}</span>}
          hint="15:10 后执行"
        />
        <Stat label="数据源" value={<span className="num">{health.sources.filter((source) => source.ok).length} / {health.sources.length}</span>} hint="正常 / 全部" />
      </div>
    </Panel>
  );
}

const SOURCE_KIND_LABEL: Record<SourceStatus['kind'], string> = {
  quote: '行情',
  bars: '日线',
  intraday: '分时',
  fund: '基金',
};

const CIRCUIT_LABEL: Record<SourceStatus['circuit'], string> = {
  closed: '正常',
  open: '熔断',
  half_open: '半开',
};

const SOURCE_STATUS_LABEL: Record<SourceStatus['status'], string> = {
  ok: '正常',
  degraded: '降级',
  down: '故障',
  unknown: '未知',
};

function sourceTone(source: SourceStatus): string {
  if (source.status === 'ok') return 'badge-down';
  if (source.status === 'degraded') return 'badge-warn';
  return 'badge-up';
}

function SourcesPanel({
  sources,
  health,
  onRefresh,
}: {
  sources: SourcesResponse | null;
  health: HealthResponse;
  onRefresh: () => void;
}): React.JSX.Element {
  const rows = sources?.sources ?? [];
  const alerts = sources?.alerts ?? [];
  const critical = alerts.some((alert) => alert.level === 'critical');
  const warn = alerts.some((alert) => alert.level === 'warn');
  const fallbackRows = health.sources.map((source) => ({
    id: source.id,
    name: SOURCE_LABEL[source.id] ?? source.id,
    kind: 'quote' as SourceStatus['kind'],
    status: source.ok ? ('ok' as const) : ('down' as const),
    latency_ms: source.latency_ms,
    success_count: 0,
    failure_count: 0,
    fallback_count: 0,
    circuit: 'closed' as const,
    last_error: source.detail ?? null,
    checked_at: source.checked_at,
    detail: source.detail ?? '',
  }));

  return (
    <Panel
      title="数据源健康"
      sub={`${rows.length || fallbackRows.length} 个上游 · 30s 自动刷新（页面可见时）`}
      actions={
        <IconButton
          label="刷新数据源状态"
          icon={<RefreshCw size={14} />}
          variant="ghost"
          size="sm"
          onClick={onRefresh}
        />
      }
      bodyClassName=""
      footer={<span>采集失败不会导致 API 500：服务端返回缓存数据并附带 warnings；熔断状态用于避免持续打上游。</span>}
    >
      <div className="border-b border-[var(--etf-line)] px-4 py-2.5">
        {alerts.length === 0 ? (
          <div className="flex items-center gap-2 text-[10.5px] text-[var(--etf-muted)]">
            <CheckCircle2 size={13} className="shrink-0 text-[var(--etf-down)]" aria-hidden="true" />
            全部正常，无最近告警。
          </div>
        ) : (
          <div className="space-y-1.5" role="status" aria-live="polite">
            <div className="flex items-center gap-2 text-[10px]">
              <span className={critical ? 'text-[var(--etf-up)]' : warn ? 'text-[var(--etf-amber)]' : 'text-[var(--etf-muted)]'}>
                最近告警 {alerts.length} 条
              </span>
              {critical ? <span className="state-badge badge-up">critical</span> : null}
              {warn ? <span className="state-badge badge-warn">warn</span> : null}
            </div>
            {alerts.slice(0, 3).map((alert) => (
              <div
                key={alert.id}
                className={`truncate text-[10.5px] ${
                  alert.level === 'critical' ? 'text-[var(--etf-up)]' : alert.level === 'warn' ? 'text-[var(--etf-amber)]' : 'text-[var(--etf-muted)]'
                }`}
                title={alert.message}
              >
                [{alert.level}] {alert.message} <span className="mono text-[9px] text-[var(--etf-faint)]">{fmtDateTime(alert.at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="scroll-x hidden md:block">
          <table className="datatable">
            <caption className="sr-only">数据源健康状态</caption>
            <thead>
              <tr>
                <th>数据源</th>
                <th>状态</th>
                <th className="r">延迟</th>
                <th className="r">检查时间</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {fallbackRows.map((source) => (
                <tr key={source.id}>
                  <td className="text-[var(--etf-ink-2)]">
                    {source.name}
                    <span className="datatable__code ml-2">{source.id}</span>
                  </td>
                  <td>
                    <span className={`state-badge ${sourceTone(source as SourceStatus)}`}>
                      {source.status === 'ok' ? '正常' : '异常'}
                    </span>
                  </td>
                  <td className="num r">{source.latency_ms === null ? '—' : `${source.latency_ms} ms`}</td>
                  <td className="mono r text-[10.5px] text-[var(--etf-muted)]">{source.checked_at ? fmtDateTime(source.checked_at) : '—'}</td>
                  <td className="max-w-[260px] truncate text-[11px] text-[var(--etf-muted)]">{source.detail || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <div className="scroll-x hidden md:block">
            <table className="datatable">
              <caption className="sr-only">数据源健康状态（含熔断与回退计数）</caption>
              <thead>
                <tr>
                  <th>数据源</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th className="r">延迟</th>
                  <th className="r">成功 / 失败 / 回退</th>
                  <th>熔断</th>
                  <th>最近错误</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((source) => (
                  <tr key={source.id}>
                    <td className="text-[var(--etf-ink-2)]">
                      {source.name}
                      <span className="datatable__code ml-2">{source.id}</span>
                    </td>
                    <td className="text-[10.5px] text-[var(--etf-muted)]">{SOURCE_KIND_LABEL[source.kind]}</td>
                    <td>
                      <span className={`state-badge ${sourceTone(source)}`}>{SOURCE_STATUS_LABEL[source.status]}</span>
                    </td>
                    <td className="num r">{source.latency_ms === null ? '—' : `${source.latency_ms} ms`}</td>
                    <td className="num r text-[10.5px] text-[var(--etf-muted)]">
                      {source.success_count} / {source.failure_count} / {source.fallback_count}
                    </td>
                    <td>
                      <span className={`state-badge ${source.circuit === 'closed' ? 'badge-muted' : source.circuit === 'open' ? 'badge-up' : 'badge-warn'}`}>
                        {CIRCUIT_LABEL[source.circuit]}
                      </span>
                    </td>
                    <td className="max-w-[220px] truncate text-[10.5px] text-[var(--etf-faint)]" title={source.last_error ?? undefined}>
                      {source.last_error ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="space-y-2 p-3 md:hidden">
            {rows.map((source) => (
              <div key={source.id} className="rounded-lg border border-[var(--etf-line)] bg-[var(--etf-surface-2)] px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[12px] text-[var(--etf-ink)]">{source.name}</div>
                    <div className="mono text-[9px] text-[var(--etf-faint)]">{source.id} · {SOURCE_KIND_LABEL[source.kind]}</div>
                  </div>
                  <span className={`state-badge ${sourceTone(source)}`}>{SOURCE_STATUS_LABEL[source.status]}</span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-[var(--etf-muted)]">
                  <span>延迟 {source.latency_ms === null ? '—' : `${source.latency_ms}ms`}</span>
                  <span>失败 {source.failure_count}</span>
                  <span>回退 {source.fallback_count}</span>
                  <span>熔断 {CIRCUIT_LABEL[source.circuit]}</span>
                  <span className="col-span-2 truncate" title={source.last_error ?? undefined}>错误 {source.last_error ?? '—'}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

function EntitiesPanel({ meta }: { meta: MetaResponse }): React.JSX.Element {
  const [group, setGroup] = useState('全部');
  const [visibleCount, setVisibleCount] = useState(25);
  const groups = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entity of meta.entities) counts.set(entity.group, (counts.get(entity.group) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [meta.entities]);

  const filteredRows: EntityRow[] = useMemo(
    () =>
      meta.entities
        .filter((entity) => group === '全部' || entity.group === group)
        .map((entity) => ({ code: entity.code, name: entity.name, group: entity.group })),
    [meta.entities, group],
  );

  useEffect(() => {
    setVisibleCount(25);
  }, [group]);

  const rows = filteredRows.slice(0, visibleCount);

  const columns: TableColumn<EntityRow>[] = [
    {
      key: 'name',
      header: '名称',
      width: proportional(2, { minWidth: 180 }),
      renderCell: (item) => <span className="text-[var(--etf-ink)]">{item.name}</span>,
    },
    {
      key: 'code',
      header: '代码',
      width: pixel(100),
      renderCell: (item) => <span className="mono text-[11px] text-[var(--etf-muted)]">{fmtCode(item.code)}</span>,
    },
    {
      key: 'group',
      header: '分组',
      width: proportional(1, { minWidth: 88 }),
      renderCell: (item) => <span className="text-[11px] text-[var(--etf-muted)]">{item.group}</span>,
    },
  ];

  return (
    <Panel
      title="实体清单"
      sub={`${meta.entities.length} 个 ETF · 篮子与热点方向`}
      actions={
        <span className="mono text-[9px] text-[var(--etf-faint)]">
          {rows.length} / {filteredRows.length} 行
        </span>
      }
      bodyClassName=""
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>窗口化渲染：首屏只显示 25 行，筛选后保持原排序。</span>
          {visibleCount < filteredRows.length ? (
            <Button
              label={`加载更多（剩余 ${filteredRows.length - visibleCount}）`}
              size="sm"
              variant="secondary"
              onClick={() => setVisibleCount((value) => value + 25)}
            />
          ) : null}
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--etf-line)] px-4 py-2.5">
        <div className="group-pills">
          <button type="button" className="group-pill" aria-pressed={group === '全部'} onClick={() => setGroup('全部')}>
            全部 <span className="group-pill__count">{meta.entities.length}</span>
          </button>
          {groups.map(([name, count]) => (
            <button
              key={name}
              type="button"
              className="group-pill"
              aria-pressed={group === name}
              onClick={() => setGroup(name)}
            >
              {name} <span className="group-pill__count">{count}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="p-2">
        <Table
          data={rows}
          columns={columns}
          idKey="code"
          density="compact"
          dividers="rows"
          hasHover
          textOverflow="truncate"
          rowCount={rows.length}
        />
      </div>
    </Panel>
  );
}

function DefinitionsPanel({ meta }: { meta: MetaResponse }): React.JSX.Element {
  return (
    <Panel title="口径字典" sub="API 返回的确定性定义" bodyClassName="panel__body">
      <div>
        {Object.entries(meta.definitions).map(([key, value]) => (
          <div key={key} className="py-2.5 border-b border-[var(--etf-line)] last:border-b-0">
            <div className="flex items-baseline gap-2">
              <span className="text-[12px] font-medium text-[var(--etf-ink)]">{DEF_LABEL[key] ?? key}</span>
              <span className="mono text-[9px] text-[var(--etf-faint)]">{key}</span>
            </div>
            <p className="mt-1 text-[11px] leading-[1.7] text-[var(--etf-muted)]">{value}</p>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function SecurityPanel({
  authMode,
  user,
  onLogout,
}: {
  authMode: AuthMode;
  user: string | null;
  onLogout: () => void;
}): React.JSX.Element {
  const sessions = useResource((signal) => fetchAdminSessions(signal), []);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const error: unknown = sessions.error;
  const forbidden = error instanceof HttpError && error.status === 403;
  const unavailable = error instanceof HttpError && error.status === 503;
  const admin = sessions.data;
  const forward = authMode === 'forward';
  const canRevoke = admin?.mode === 'redis' && admin.revoke_supported && !forbidden;

  const revoke = async (): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await revokeAllSessions();
      setMessage(`已注销 ${result.revoked} 个会话，正在跳转登录页…`);
      window.setTimeout(() => {
        if (forward) {
          window.location.assign(`/auth/?rd=${encodeURIComponent(window.location.origin + '/')}`);
        } else {
          onLogout();
        }
      }, 900);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '注销失败，请稍后重试。');
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <Panel
      title="安全 / 会话"
      sub={forward ? 'Authelia 前向认证 · 身份由反代注入' : '本地会话 · 开发回退'}
      actions={<Badge label={forward ? 'Authelia' : 'Local'} variant={forward ? 'success' : 'warning'} />}
      bodyClassName="panel__body"
      footer={<span>会话 cookie 由服务端/反代管理，前端不读取、不存入 localStorage。</span>}
    >
      <div className="kv-line">
        <span className="kv-line__label">当前用户</span>
        <span className="kv-line__value">{user ?? '—'}</span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">认证模式</span>
        <span className="kv-line__value">{forward ? 'Authelia 前向认证' : '本地登录（开发回退）'}</span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">会话存储</span>
        <span className="kv-line__value">
          {sessions.status === 'loading' ? '读取中…' : admin ? (admin.mode === 'redis' ? 'Redis' : '本地') : '—'}
        </span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">活跃会话</span>
        <span className="kv-line__value">
          {admin?.active_keys === null || admin?.active_keys === undefined ? '—' : `${admin.active_keys} 个`}
        </span>
      </div>

      {forbidden ? (
        <p className="mt-3 text-[10.5px] leading-relaxed text-[var(--etf-faint)]">
          当前账号无管理员权限。仅 <span className="mono">ETF_ADMIN_USERS</span> 可查看/注销所有设备。
        </p>
      ) : null}
      {unavailable ? (
        <p className="mt-3 text-[10.5px] leading-relaxed text-[var(--etf-amber)]" role="status">
          会话服务暂不可用（Redis 未就绪），暂时无法查看或注销所有设备。
        </p>
      ) : null}

      {message ? (
        <p className="mt-3 text-[11px] leading-relaxed text-[var(--etf-gold)]" role="status" aria-live="polite">
          {message}
        </p>
      ) : null}

      {canRevoke ? (
        <div className="mt-3 border-t border-[var(--etf-line)] pt-3">
          {confirming ? (
            <div className="rounded-lg border border-[color-mix(in_srgb,var(--etf-up)_35%,var(--etf-line))] bg-[color-mix(in_srgb,var(--etf-up)_6%,transparent)] px-3 py-2.5">
              <p className="text-[11px] leading-relaxed text-[var(--etf-ink-2)]">
                将注销所有设备的 Authelia 会话，包括当前设备。确认继续？
              </p>
              <div className="mt-2 flex items-center gap-2">
                <Button
                  label={busy ? '注销中…' : '确认注销'}
                  size="sm"
                  variant="primary"
                  isLoading={busy}
                  onClick={() => void revoke()}
                />
                <Button label="取消" size="sm" variant="ghost" isDisabled={busy} onClick={() => setConfirming(false)} />
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-start gap-2">
                <ShieldCheck size={14} className="mt-0.5 shrink-0 text-[var(--etf-gold)]" aria-hidden="true" />
                <span className="text-[10.5px] leading-relaxed text-[var(--etf-muted)]">
                  管理员可注销所有设备；此操作会使全部会话立即失效。
                </span>
              </div>
              <Button
                label="注销所有设备"
                size="sm"
                variant="secondary"
                icon={<KeyRound size={13} aria-hidden="true" />}
                onClick={() => setConfirming(true)}
              />
            </div>
          )}
        </div>
      ) : null}

      <div className="mt-3 border-t border-[var(--etf-line)] pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[10.5px] leading-relaxed text-[var(--etf-faint)]">
            {forward ? '退出将同时结束 Authelia 门户会话。' : '退出后需重新输入账号密码。'}
          </span>
          <Button
            label={forward ? '退出 Authelia 会话' : '退出登录'}
            variant="secondary"
            size="sm"
            icon={<LogOut size={13} aria-hidden="true" />}
            onClick={onLogout}
          />
        </div>
      </div>
    </Panel>
  );
}

function AboutPanel({ meta }: { meta: MetaResponse }): React.JSX.Element {
  return (
    <Panel title="关于与版本" sub={`${meta.product} · ${meta.version}`} bodyClassName="panel__body">
      <div className="kv-line">
        <span className="kv-line__label">产品</span>
        <span className="kv-line__value">{meta.product}</span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">API 版本</span>
        <span className="kv-line__value">{meta.version}</span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">构建日期</span>
        <span className="kv-line__value">{meta.built_at}</span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">数据模式</span>
        <span className="kv-line__value">
          {isMock ? 'MOCK 示例数据' : 'LIVE /api'}
        </span>
      </div>
      <div className="kv-line">
        <span className="kv-line__label">构建模式</span>
        <span className="kv-line__value">{import.meta.env.MODE}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <LabelBadge tone="gold">React 19</LabelBadge>
        <LabelBadge tone="muted">Astryx 0.7</LabelBadge>
        <LabelBadge tone="muted">Tailwind 4</LabelBadge>
        <LabelBadge tone="muted">lightweight-charts 5</LabelBadge>
      </div>
      <div className="mt-3 space-y-2">
        {CHANGELOG.map((entry) => (
          <div key={entry.version}>
            <div className="flex items-center gap-2">
              <span className="mono text-[10px] text-[var(--etf-gold)]">{entry.version}</span>
              <span className="mono text-[9px] text-[var(--etf-faint)]">{entry.date}</span>
            </div>
            <ul className="mt-1 space-y-1">
              {entry.items.map((item) => (
                <li key={item} className="flex gap-2 text-[10.5px] leading-[1.65] text-[var(--etf-muted)]">
                  <span className="mt-[7px] h-px w-2 shrink-0 bg-[var(--etf-line-strong)]" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function LimitationsPanel({ meta }: { meta: MetaResponse }): React.JSX.Element {
  return (
    <Panel title="已知限制" sub="明确写出不能证明什么" bodyClassName="panel__body">
      <ul className="space-y-2">
        {meta.limitations.map((item) => (
          <li key={item} className="flex gap-2 text-[11px] leading-[1.7] text-[var(--etf-ink-2)]">
            <CircleAlert size={13} className="mt-0.5 shrink-0 text-[var(--etf-amber)]" aria-hidden="true" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center gap-2 text-[11px] text-[var(--etf-muted)]">
          <CheckCircle2 size={13} className="shrink-0 text-[var(--etf-down)]" aria-hidden="true" />
          所有响应带 generated_at；时间统一 Asia/Shanghai。
        </div>
        <div className="flex items-center gap-2 text-[11px] text-[var(--etf-muted)]">
          <XCircle size={13} className="shrink-0 text-[var(--etf-faint)]" aria-hidden="true" />
          不接交易账户、不提供买卖建议、不识别申赎主体。
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {meta.data_sources.map((source) => (
          <span key={source.id} className="state-badge badge-muted">
            {source.name} · {source.usage}
          </span>
        ))}
      </div>
    </Panel>
  );
}

export function DataPage({
  refreshTick,
  user,
  authMode,
  onLogout,
}: {
  refreshTick: number;
  user: string | null;
  authMode: AuthMode;
  onLogout: () => void;
}): React.JSX.Element {
  const health = useResource((signal) => fetchHealth(signal), [refreshTick], { pollMs: 60_000 });
  const meta = useResource((signal) => fetchMeta(signal), [refreshTick]);
  const sources = useResource((signal) => fetchSources(signal), [refreshTick], { pollMs: 30_000 });
  const healthData = health.data;
  const metaData = meta.data;

  return (
    <>
      <PageHeader
        route="data"
        aside={
          healthData ? (
            <>
              <span className={`state-badge ${healthData.status === 'ok' ? 'badge-down' : 'badge-up'}`}>
                {healthData.status === 'ok' ? '服务运行正常' : '服务降级'}
              </span>
              <span className="state-badge badge-muted">{healthData.version}</span>
              <span className="mono text-[9px] text-[var(--etf-faint)]">{fmtNum(healthData.uptime_s / 3600, 1)}h</span>
            </>

          ) : null
        }
      />

      <CoveragePanel refreshTick={refreshTick}/>

      {!healthData && health.status === 'error' ? (
        <ErrorPanel error={health.error} onRetry={health.reload} title="健康检查失败" />
      ) : !healthData ? (
        <PanelSkeleton rows={3} />
      ) : (
        <div className="space-y-5">
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-5">
              <HealthPanel health={healthData} />
            </div>
            <div className="xl:col-span-7">
              <SourcesPanel sources={sources.data} health={healthData} onRefresh={sources.reload} />
            </div>
          </div>

          <SectionHead index="01" title="实体与口径" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">ENTITIES / DEFINITIONS</span>} />
          {!metaData && meta.status === 'error' ? (
            <ErrorPanel error={meta.error} onRetry={meta.reload} title="元数据加载失败" />
          ) : !metaData ? (
            <PanelSkeleton rows={4} />
          ) : (
            <div className="grid gap-4 xl:grid-cols-12">
              <div className="xl:col-span-7">
                <EntitiesPanel meta={metaData} />
              </div>
              <div className="xl:col-span-5">
                <DefinitionsPanel meta={metaData} />
              </div>
            </div>
          )}

          {metaData ? (
            <>
              <SectionHead index="02" title="安全 / 会话" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">AUTH / SESSIONS</span>} />
              <div className="grid gap-4 xl:grid-cols-12">
                <div className="xl:col-span-6">
                  <SecurityPanel authMode={authMode} user={user} onLogout={onLogout} />
                </div>
                <div className="xl:col-span-6">
                  <AboutPanel meta={metaData} />
                </div>
              </div>

              <SectionHead index="03" title="版本与边界" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">VERSION / LIMITS</span>} />
              <div className="grid gap-4 xl:grid-cols-12">
                <div className="xl:col-span-6">
                  <LimitationsPanel meta={metaData} />
                </div>
                <div className="xl:col-span-6">
                  <Panel title="数据字典 · 来源" sub="服务端 meta.data_sources" bodyClassName="panel__body">
                    <div className="space-y-2">
                      {metaData.data_sources.map((source) => (
                        <div key={source.id} className="rounded-lg border border-[var(--etf-line)] bg-[var(--etf-surface-2)] px-3 py-2">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="text-[12px] text-[var(--etf-ink)]">{source.name}</span>
                            <span className="mono text-[9px] text-[var(--etf-faint)]">{source.id}</span>
                          </div>
                          <div className="mt-0.5 text-[10.5px] leading-relaxed text-[var(--etf-muted)]">{source.usage}</div>
                        </div>
                      ))}
                    </div>
                    <p className="mt-3 text-[10px] leading-relaxed text-[var(--etf-faint)]">
                      盘中提醒来自定时报价采样，可能延迟；当日均线尚未收盘定型。
                    </p>
                  </Panel>
                </div>
              </div>
            </>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-[var(--etf-faint)]">
            <span>数据新鲜度规则：交易时段报价超过 300 秒标记 stale；非交易时段以最近交易日收盘为准。</span>
            <span className="mono">generated_at · {fmtDateTime(healthData.generated_at)}</span>
          </div>
        </div>
      )}
    </>
  );
}

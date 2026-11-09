import { useId } from 'react';
import type { ReactNode } from 'react';
import { routeMeta, type RouteKey } from '../lib/router';

export function PageHeader({
  route,
  title,
  subtitle,
  aside,
}: {
  route: RouteKey;
  title?: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
}): React.JSX.Element {
  const meta = routeMeta(route);
  return (
    <header className="pagehead enter">
      <div className="min-w-0">
        <div className="eyebrow">
          <span>{meta.index}</span>
          <span className="divider-dot" aria-hidden="true" />
          <span>{meta.en}</span>
          <span className="eyebrow--muted">· 研究台</span>
        </div>
        <h1 className="page-title">{title ?? meta.label}</h1>
        <p className="page-sub">{subtitle ?? meta.summary}</p>
      </div>
      {aside ? <div className="flex shrink-0 flex-wrap items-center gap-2">{aside}</div> : null}
    </header>
  );
}

export function SectionHead({
  index,
  title,
  actions,
  id,
}: {
  index?: string;
  title: ReactNode;
  actions?: ReactNode;
  id?: string;
}): React.JSX.Element {
  return (
    <div className="section__head">
      {index ? <span className="section__index">{index}</span> : null}
      <h2 className="section__title" id={id}>
        {title}
      </h2>
      <span className="section__rule" aria-hidden="true" />
      {actions}
    </div>
  );
}

export function Panel({
  title,
  sub,
  actions,
  footer,
  children,
  className = '',
  bodyClassName = '',
  feature = false,
  flush = false,
  id,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  feature?: boolean;
  flush?: boolean;
  id?: string;
}): React.JSX.Element {
  const autoId = useId();
  const headingId = `${id ?? 'panel'}-${autoId.replace(/:/g, '')}`;
  const hasHead = Boolean(title || actions || sub);
  return (
    <section
      className={`panel ${feature ? 'panel--feature' : ''} ${flush ? 'panel--flush' : ''} ${className}`}
      aria-labelledby={title ? headingId : undefined}
      id={id}
    >
      {hasHead ? (
        <div className="panel__head">
          <div className="min-w-0">
            {title ? (
              <h2 className="panel__title" id={headingId}>
                {title}
              </h2>
            ) : null}
            {sub ? <div className="panel__sub mt-0.5">{sub}</div> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </div>
      ) : null}
      <div className={bodyClassName}>{children}</div>
      {footer ? <div className="panel__foot">{footer}</div> : null}
    </section>
  );
}

export function PageAsideNote({ children }: { children: ReactNode }): React.JSX.Element {
  return <p className="mt-3 text-[11px] leading-relaxed faint">{children}</p>;
}

export function KeyValue({
  label,
  value,
  hint,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
}): React.JSX.Element {
  return (
    <div className="min-w-0">
      <div className="metric__label">{label}</div>
      <div className="metric__value">{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

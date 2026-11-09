import { useEffect, useRef, useState } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { IconButton } from '@astryxdesign/core/IconButton';
import { TextInput } from '@astryxdesign/core/TextInput';
import { Eye, EyeOff, Lock, Moon, Sun, User } from 'lucide-react';
import type { AuthLoginResponse, AuthSession } from '../api/types';
import { loginRequest } from '../api/client';
import { HttpError } from '../api/http';
import type { ThemeMode } from '../lib/hooks';

export type LoginNotice = 'expired' | 'network' | null;

function errorText(error: unknown): string {
  if (error instanceof HttpError) {
    if (error.status === 401) return '用户名或密码不正确。';
    if (error.status === 429) return '尝试次数过多，请稍后再试。';
    if (error.status === 403) return '请求被拒绝，请刷新页面后重试。';
    return error.message || '登录失败，请稍后重试。';
  }
  return '无法连接服务，请检查网络后重试。';
}

export function SessionSplash(): React.JSX.Element {
  return (
    <div className="login-page login-page--splash" role="status" aria-label="正在验证登录状态">
      <div className="login-splash">
        <span className="brandmark" aria-hidden="true" />
        <div>
          <div className="text-[15px] font-semibold tracking-[0.05em] text-[var(--etf-ink)]">ETF 观察台</div>
          <div className="mono text-[8.5px] tracking-[0.18em] text-[var(--etf-faint)]">OBSERVATORY · PRIVATE</div>
        </div>
      </div>
      <div className="login-progress">
        <span className="login-progress__bar" aria-hidden="true" />
        <span className="text-[11px] text-[var(--etf-muted)]">正在验证会话…</span>
      </div>
    </div>
  );
}

export function LoginScreen({
  mode,
  onToggleTheme,
  onSuccess,
  notice,
}: {
  mode: ThemeMode;
  onToggleTheme: () => void;
  onSuccess: (session: AuthLoginResponse) => void;
  notice: LoginNotice;
}): React.JSX.Element {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  const initialNotice =
    notice === 'expired'
      ? '登录已过期，请重新登录。'
      : notice === 'network'
        ? '无法连接服务，请检查网络后重试。'
        : null;

  const submit = async (): Promise<void> => {
    if (submitting) return;
    if (!username.trim() || !password) {
      setError('请输入用户名和密码。');
      if (!username.trim()) usernameRef.current?.focus();
      else passwordRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const session: AuthSession & { authenticated: true; user: string } = await loginRequest(
        username.trim(),
        password,
      );
      onSuccess({
        authenticated: true,
        user: session.user,
        expires_at: session.expires_at ?? null,
      });
    } catch (caught) {
      setError(errorText(caught));
      setSubmitting(false);
      passwordRef.current?.focus();
    }
  };

  return (
    <div className="login-page">
      <div className="login-theme">
        <IconButton
          label={mode === 'dark' ? '切换到浅色主题' : '切换到暗色主题'}
          icon={mode === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          variant="ghost"
          size="sm"
          onClick={onToggleTheme}
        />
      </div>

      <section className="login-brand" aria-labelledby="login-brand-title">
        <div className="login-brand__mark">
          <span className="brandmark" aria-hidden="true" />
          <div>
            <div className="mono text-[9px] tracking-[0.2em] text-[var(--etf-gold)]">PRIVATE RESEARCH DESK</div>
            <div className="mt-1 text-[11px] tracking-[0.06em] text-[var(--etf-muted)]">ETF OBSERVATORY</div>
          </div>
        </div>

        <div className="login-brand__copy">
          <h1 id="login-brand-title" className="login-title">
            ETF 观察台
          </h1>
          <p className="login-subtitle">私有研究站 · 份额与热点观察</p>
          <p className="login-description">
            把国家队宽基篮子的份额变化、54 个行业方向的相对强度与自选行情放进同一张研究台。
            数字可追溯、口径可解释，不接交易账户，不提供买卖建议。
          </p>
        </div>

        <dl className="login-facts">
          <div>
            <dt>篮子</dt>
            <dd className="num">26</dd>
            <span>只核心宽基 ETF</span>
          </div>
          <div>
            <dt>方向</dt>
            <dd className="num">54</dd>
            <span>个行业 / 主题代表</span>
          </div>
          <div>
            <dt>采样</dt>
            <dd className="num">2′</dd>
            <span>交易时段盘中估算</span>
          </div>
        </dl>

        <p className="login-brand__foot">
          <span>Asia/Shanghai</span>
          <span className="divider-dot" aria-hidden="true" />
          <span>公开行情数据 · 不识别申赎主体</span>
        </p>
      </section>

      <section className="login-panel" aria-labelledby="login-form-title">
        <div className="login-form">
          <div className="eyebrow">
            <span>SIGN IN</span>
            <span className="divider-dot" aria-hidden="true" />
            <span className="text-[var(--etf-faint)]">访问控制</span>
          </div>
          <h2 id="login-form-title" className="login-form__title">
            登录
          </h2>
          <p className="login-form__hint">会话保存在 HttpOnly Cookie 中，默认 30 天。</p>

          {initialNotice ? (
            <div
              className={`login-banner ${notice === 'network' ? 'login-banner--warn' : ''}`}
              role="status"
              aria-live="polite"
            >
              {initialNotice}
            </div>
          ) : null}

          <form
            className="login-form__fields"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            noValidate
          >
            <div className="login-field">
              <TextInput
                ref={usernameRef}
                id="login-username"
                label="用户名"
                type="text"
                value={username}
                onChange={setUsername}
                placeholder="账号"
                autoComplete="username"
                startIcon={<User size={14} aria-hidden="true" />}
                size="md"
                width="100%"
                isDisabled={submitting}
                onEnter={() => passwordRef.current?.focus()}
              />
            </div>

            <div className="login-field login-password">
              <TextInput
                ref={passwordRef}
                id="login-password"
                label="密码"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={setPassword}
                placeholder="密码"
                autoComplete="current-password"
                startIcon={<Lock size={14} aria-hidden="true" />}
                size="md"
                width="100%"
                isDisabled={submitting}
                onEnter={() => void submit()}
              />
              <button
                type="button"
                className="login-reveal"
                aria-pressed={showPassword}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                onClick={() => setShowPassword((value) => !value)}
              >
                {showPassword ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                {showPassword ? '隐藏' : '显示'}
              </button>
            </div>

            <div className="login-error" role="alert" aria-live="assertive">
              {error ?? ''}
            </div>

            <Button
              type="submit"
              label="登录"
              variant="primary"
              width="100%"
              size="lg"
              isLoading={submitting}
              isDisabled={!username.trim() || !password}
            />
          </form>

          <div className="login-form__foot">
            <span>仅限本人研究使用。</span>
            <span className="mono">ETF OBSERVATORY · v2.2</span>
          </div>
        </div>
      </section>
    </div>
  );
}

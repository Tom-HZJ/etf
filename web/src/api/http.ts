// Live HTTP transport. In mock mode this file is never used (client.ts routes
// to mock.ts), but the retry / timeout / error-envelope logic is shared shape.

// Live is the default: an unset VITE_API_MODE must never ship sample data.
// Mock is opt-in only (`VITE_API_MODE=mock`).
export const API_MODE: 'mock' | 'live' =
  import.meta.env.VITE_API_MODE === 'mock' ? 'mock' : 'live';

export const isMock = API_MODE === 'mock';
export const API_BASE = import.meta.env.VITE_API_BASE ?? '';

const TIMEOUT_MS = 12_000;
const RETRY_DELAYS_MS = [400, 900];

export class HttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, message: string, code = 'http_error') {
    super(message);
    this.name = 'HttpError';
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  signal?: AbortSignal;
  /** Auth endpoints handle their own 401s; don't bounce the whole app. */
  suppressUnauthorized?: boolean;
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function notifyUnauthorized(): void {
  try {
    unauthorizedHandler?.();
  } catch (error) {
    console.error('unauthorized handler failed', error);
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

async function fetchOnce<T>(path: string, options: RequestOptions): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), TIMEOUT_MS);
  const onOuterAbort = () => controller.abort();
  options.signal?.addEventListener('abort', onOuterAbort, { once: true });

  try {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (options.method === 'POST') {
      headers['Content-Type'] = 'application/json';
      headers['X-ETF-Client'] = 'web-v1';
    }

    const response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
      signal: controller.signal,
    });

    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text) as unknown;
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      if (response.status === 401 && !options.suppressUnauthorized) notifyUnauthorized();
      const envelope = payload as { error?: { code?: string; message?: string } } | null;
      const message = envelope?.error?.message ?? `请求失败（HTTP ${response.status}）`;
      throw new HttpError(response.status, message, envelope?.error?.code ?? `http_${response.status}`);
    }

    if (payload === null) {
      throw new HttpError(response.status, '响应不是合法 JSON', 'bad_payload');
    }
    return payload as T;
  } finally {
    window.clearTimeout(timer);
    options.signal?.removeEventListener('abort', onOuterAbort);
  }
}

export async function httpRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const attempts = method === 'GET' ? RETRY_DELAYS_MS.length + 1 : 1;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await fetchOnce<T>(path, options);
    } catch (error) {
      const aborted =
        options.signal?.aborted ||
        (error instanceof DOMException && (error.name === 'AbortError' || error.name === 'TimeoutError'));
      if (aborted) throw error;

      const retryable =
        !(error instanceof HttpError) || error.status >= 500 || error.status === 429;
      if (!retryable || attempt === attempts - 1) throw error;
      await sleep(RETRY_DELAYS_MS[attempt] ?? 900, options.signal);
    }
  }

  throw new HttpError(0, '无法连接数据服务', 'unreachable');
}

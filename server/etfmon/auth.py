"""应用内登录 / 会话认证（纯标准库）。

设计：
- **密码**：PBKDF2-HMAC-SHA256（默认 260,000 次迭代）存 ``ETF_DATA/auth.json``（0600），
  文件里只有盐、哈希、迭代次数与 epoch，没有明文密码；改密码时 ``epoch + 1``，旧会话立即失效。
- **会话**：HMAC-SHA256 签名的紧凑 token（``base64url(payload).base64url(sig)``，
  payload 为 ``{"u","epoch","iat","exp"}``，默认 30 天），密钥随机 32 字节存
  ``ETF_DATA/session_secret``（0600，首次使用时原子创建）。
  cookie 名 ``etf_session``，``HttpOnly; SameSite=Lax; Path=/``；当反代声明
  ``X-Forwarded-Proto: https`` 或环境 ``ETF_COOKIE_SECURE=1`` 时追加 ``Secure``。
- **限速**：内存按 IP 记录失败时间戳，5 次/5 分钟 → 429（带 ``Retry-After``），成功登录清零。
  只记录 IP 与时间，不记录用户名/密码之外的信息，也绝不记录密码。
- **CSRF**：POST 请求若带 ``Origin`` 必须与 ``Host``/``X-Forwarded-Host`` 同源，否则 403；
  写接口仍保留 ``X-ETF-Client: web-v1`` 校验。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Mapping

from . import config, store

LOG = logging.getLogger("etfmon.auth")

_DUMMY_SALT = b"etfmon-dummy-sa"  # 用户不存在时也跑一次 PBKDF2，避免时序差异
_SECRET_LOCK = threading.Lock()
_SECRET_CACHE: dict[str, bytes] = {}
_SECRET_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------- 文件路径与私有写


def auth_path() -> Path:
    return store.data_root() / config.AUTH_FILENAME


def secret_path() -> Path:
    return store.data_root() / config.SESSION_SECRET_FILENAME


def _write_private(path: Path, payload: bytes) -> None:
    """原子写 + 0600 权限（Windows 的 chmod 只有只读位，尽力而为）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:  # 清理失败不影响已完成的替换
                LOG.debug("清理临时文件 %s 失败", temporary)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ---------------------------------------------------------------- 密码


def load_credentials() -> dict[str, Any] | None:
    """读取密码文件；不存在/损坏返回 None。"""
    payload = store.read_json(auth_path(), None)
    if not isinstance(payload, dict):
        return None
    if not payload.get("user") or not payload.get("salt_b64") or not payload.get("hash_b64"):
        return None
    return payload


def _credentials_epoch(credentials: Mapping[str, Any] | None) -> int:
    if not credentials:
        return 0
    try:
        return int(credentials.get("epoch") or 0)
    except (TypeError, ValueError):
        return 0


def current_epoch() -> int:
    """当前密码 epoch；未初始化时为 0。"""
    return _credentials_epoch(load_credentials())


def set_password(username: str, password: str) -> dict[str, Any]:
    """设置/重置密码并 ++epoch（旧会话失效）。返回不含哈希的摘要。"""
    if not username or not username.strip():
        raise ValueError("用户名不能为空")
    if not password:
        raise ValueError("密码不能为空")
    if len(password) < config.PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少 {config.PASSWORD_MIN_LENGTH} 位")
    previous = load_credentials()
    epoch = _credentials_epoch(previous) + 1
    iterations = config.PBKDF2_ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    updated_at = store.iso_now()
    payload = {
        "user": username.strip(),
        "salt_b64": _b64encode(salt),
        "hash_b64": _b64encode(digest),
        "iterations": iterations,
        "epoch": epoch,
        "updated_at": updated_at,
    }
    _write_private(auth_path(), json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"user": payload["user"], "epoch": epoch, "iterations": iterations, "updated_at": updated_at}


def verify_password(username: str, password: str) -> bool:
    """校验用户名/密码；恒定时比较，未知用户也会跑一次 PBKDF2。"""
    credentials = load_credentials()
    if credentials is None:
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _DUMMY_SALT, config.PBKDF2_ITERATIONS)
        return False
    try:
        salt = _b64decode(str(credentials["salt_b64"]))
        expected = _b64decode(str(credentials["hash_b64"]))
        iterations = int(credentials.get("iterations") or config.PBKDF2_ITERATIONS)
    except (KeyError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    password_ok = hmac.compare_digest(actual, expected)
    user_ok = hmac.compare_digest(str(username).encode("utf-8"), str(credentials.get("user") or "").encode("utf-8"))
    return password_ok and user_ok


# ---------------------------------------------------------------- 会话密钥


def _session_secret() -> bytes:
    """读取/创建会话签名密钥（随机 32B，0600，原子创建）。"""
    path = secret_path()
    key = str(path)
    with _SECRET_CACHE_LOCK:
        cached = _SECRET_CACHE.get(key)
    try:
        raw = path.read_bytes()
        if len(raw) >= 32:
            if cached != raw:
                with _SECRET_CACHE_LOCK:
                    _SECRET_CACHE[key] = raw
            return raw
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"读取会话密钥失败：{exc}") from exc
    with _SECRET_LOCK:
        try:
            raw = path.read_bytes()
            if len(raw) >= 32:
                with _SECRET_CACHE_LOCK:
                    _SECRET_CACHE[key] = raw
                return raw
        except FileNotFoundError:
            raw = secrets.token_bytes(32)
            _write_private(path, raw)
            with _SECRET_CACHE_LOCK:
                _SECRET_CACHE[key] = raw
            LOG.info("已生成新的会话签名密钥：%s", path)
            return raw
    return _session_secret()


# ---------------------------------------------------------------- token


def create_session(
    username: str,
    *,
    now: datetime | None = None,
    ttl: timedelta | None = None,
) -> tuple[str, datetime]:
    """签发会话 token，返回 ``(token, expires_at)``。"""
    moment = now or store.now()
    expires_at = moment + (ttl or timedelta(days=config.SESSION_DAYS))
    payload = {
        "u": username,
        "epoch": current_epoch(),
        "iat": int(moment.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_session_secret(), raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}", expires_at


def parse_session(token: str | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    """校验 token 的签名、有效期、epoch 与用户；失败返回 None。"""
    if not token or "." not in token:
        return None
    payload_part, signature_part = token.split(".", 1)
    try:
        raw = _b64decode(payload_part)
        signature = _b64decode(signature_part)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(_session_secret(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=config.SHANGHAI_TZ)
        username = str(payload["u"])
        epoch = int(payload["epoch"])
    except (KeyError, TypeError, ValueError, OSError):
        return None
    moment = now or store.now()
    if expires_at <= moment:
        return None
    credentials = load_credentials()
    if credentials is None or epoch != _credentials_epoch(credentials):
        return None
    granted_user = str(credentials.get("user") or "")
    if not username or username != granted_user:
        return None
    return {"user": username, "epoch": epoch, "expires_at": expires_at}


# ---------------------------------------------------------------- cookie


def token_from_cookie(cookie_header: str | None) -> str | None:
    """从 Cookie 请求头里取会话 token。"""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _sep, value = part.strip().partition("=")
        if name == config.SESSION_COOKIE_NAME and value:
            return value.strip()
    return None


def cookie_secure(forwarded_proto: str | None, env: Mapping[str, str] | None = None) -> bool:
    """判断是否给 cookie 加 Secure：优先 X-Forwarded-Proto: https，其次环境变量。"""
    proto = str(forwarded_proto or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    value = str((env or os.environ).get(config.COOKIE_SECURE_ENV, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _http_date(moment: datetime) -> str:
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def session_cookie(token: str, expires_at: datetime, *, secure: bool) -> str:
    """构造 Set-Cookie 值。"""
    parts = [
        f"{config.SESSION_COOKIE_NAME}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={int(config.SESSION_DAYS * 86400)}",
        f"Expires={_http_date(expires_at)}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(*, secure: bool) -> str:
    """构造清除 cookie 的 Set-Cookie 值。"""
    parts = [
        f"{config.SESSION_COOKIE_NAME}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ---------------------------------------------------------------- 登录限速


class LoginLimiter:
    """按 IP 的滑动窗口登录失败限速（进程内内存）。"""

    def __init__(self, max_failures: int = config.LOGIN_MAX_FAILURES, window_s: int = config.LOGIN_WINDOW_S) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        stamps = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window_s]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def retry_after(self, key: str, *, now: float | None = None) -> int | None:
        """返回需要等待的秒数；未触发限速返回 None。"""
        moment = time.monotonic() if now is None else now
        with self._lock:
            stamps = self._prune(key, moment)
            if len(stamps) < self.max_failures:
                return None
            return max(1, int(self.window_s - (moment - stamps[0])))

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            stamps = self._prune(key, moment)
            stamps.append(moment)
            self._failures[key] = stamps

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


limiter = LoginLimiter()


# ---------------------------------------------------------------- 摘要（CLI 用）


def describe() -> dict[str, Any]:
    """返回当前认证配置摘要（不含哈希），供 CLI ``--show`` 使用。"""
    credentials = load_credentials()
    if credentials is None:
        return {"configured": False, "user": None, "epoch": 0}
    return {
        "configured": True,
        "user": credentials.get("user"),
        "epoch": _credentials_epoch(credentials),
        "iterations": credentials.get("iterations"),
        "updated_at": credentials.get("updated_at"),
    }

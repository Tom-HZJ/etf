"""HTTP API：路由、参数校验、错误体、缓存头。

设计要点：
- 所有 GET 只读内存缓存/磁盘，绝不阻塞上游；采集由后台线程负责。
- 统一错误体 ``{"error": {"code": "...", "message": "..."}}``。
- 全部响应带 ``Cache-Control: no-store``；体积大于 1KB 且客户端支持时 gzip。
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from . import auth, collectors, config, redis_client, sources, store, views

LOG = logging.getLogger("etfmon.api")

_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$")
CLIENT_HEADER = "X-ETF-Client"
CLIENT_VALUE = "web-v1"
MAX_BODY_BYTES = 64 * 1024
HEALTH_PATH = "/api/health"
AUTH_PREFIX = "/api/auth/"


class ApiError(Exception):
    """业务错误：带 HTTP 状态码与契约错误码。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class RateLimited(ApiError):
    """登录限速错误：额外带 ``Retry-After`` 秒数。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__(429, "rate_limited", "尝试次数过多，请稍后再试")
        self.retry_after = retry_after


# ---------------------------------------------------------------- 参数校验


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _parse_code(raw: str | None, *, field: str = "code") -> str:
    text = (raw or "").strip().lower()
    if not text:
        raise ApiError(400, "bad_request", f"缺少参数 {field}")
    if not _CODE_RE.match(text):
        raise ApiError(400, "bad_request", f"参数 {field} 格式应为 sh/sz/bj + 6 位数字")
    return text


def _parse_int(raw: str | None, *, default: int, minimum: int, maximum: int, field: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(400, "bad_request", f"参数 {field} 必须是整数") from exc
    if value < minimum or value > maximum:
        raise ApiError(400, "bad_request", f"参数 {field} 超出范围 {minimum}~{maximum}")
    return value


def _parse_span(raw: str | None) -> str:
    span = (raw or "day").strip().lower()
    if span not in {"day", "week", "month"}:
        raise ApiError(400, "bad_request", "参数 span 只能是 day/week/month")
    return span


def _parse_codes(raw: str | None) -> list[str]:
    if not raw:
        watchlist = [str(item.get("code")) for item in store.load_watchlist().get("items") or [] if item.get("code")]
        return list(dict.fromkeys([*config.INDEX_CODES, *watchlist]))
    parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not parts:
        raise ApiError(400, "bad_request", "参数 codes 不能为空")
    if len(parts) > config.QUOTES_MAX_CODES:
        raise ApiError(400, "bad_request", f"参数 codes 最多 {config.QUOTES_MAX_CODES} 个")
    result: list[str] = []
    for part in parts:
        if not _CODE_RE.match(part):
            raise ApiError(400, "bad_request", f"代码格式不合法：{part}")
        result.append(part)
    return list(dict.fromkeys(result))


def _parse_interval(raw: str | None) -> int:
    if raw is None or raw == "":
        return 1
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ApiError(400, "bad_request", "参数 interval 必须是整数") from exc
    if interval not in config.MINUTE_INTERVALS:
        raise ApiError(400, "bad_request", "参数 interval 只能是 1/5/15/30/60")
    return interval


# ---------------------------------------------------------------- 自选变更


def _resolve_add_quote(app: views.App, code: str) -> dict[str, Any] | None:
    """自选新增时校验代码并拿到报价。

    只有「缓存里已有价格」才复用；否则即使名称/实体表认识这个代码，也现场拉一次
    腾讯报价（10s 超时）。成功则写名称缓存 + 合并进报价缓存，失败返回 None。
    """
    cached = getattr(app.cache, "get", lambda _code: None)(code)
    if cached and cached.get("price") is not None:
        return cached
    try:
        batch = sources.fetch_quotes([code], timeout=config.WATCHLIST_ADD_TIMEOUT_S)
    except sources.SourceError as exc:
        LOG.warning("自选校验 %s 报价失败：%s", code, exc)
        return None
    quote = batch.quotes.get(code)
    if not quote or quote.get("price") is None:
        return None
    merge = getattr(app.cache, "merge", None)
    if callable(merge):
        merge({code: quote})
    if quote.get("name"):
        store.set_names({code: str(quote["name"])})
    return quote


def _schedule_prefetch(code: str) -> None:
    """add 成功后后台预取日线/5 日分时；失败只记日志，不影响 add 返回。"""

    def run() -> None:
        try:
            collectors.prefetch_code(code)
        except Exception:  # noqa: BLE001 - 后台任务不能把异常抛给请求线程
            LOG.exception("预取 %s 行情失败", code)

    threading.Thread(target=run, name=f"etfmon-prefetch-{code}", daemon=True).start()


def mutate_watchlist(app: views.App, body: Mapping[str, Any]) -> dict[str, Any]:
    """处理 POST /api/watchlist 的 add/remove/update。"""
    action = str(body.get("action") or "").strip().lower()
    if action not in {"add", "remove", "update"}:
        raise ApiError(400, "bad_request", "action 只能是 add/remove/update")
    code = _parse_code(body.get("code") if isinstance(body.get("code"), str) else None)
    with_ = store.load_watchlist()
    items = [dict(item) for item in with_.get("items") or [] if item.get("code")]

    if action == "add":
        if any(item["code"] == code for item in items):
            raise ApiError(409, "conflict", f"{code} 已在自选里")
        if len(items) >= config.WATCHLIST_MAX:
            raise ApiError(409, "conflict", f"自选上限 {config.WATCHLIST_MAX} 个")
        quote = _resolve_add_quote(app, code)
        if quote is None:
            raise ApiError(422, "unprocessable", f"无法识别代码 {code}（腾讯行情没有返回报价）")
        kind = body.get("kind")
        kind = str(kind) if kind in {"etf", "stock"} else str(quote.get("kind") or config.kind_of(code))
        label = body.get("label")
        if isinstance(label, str) and label.strip():
            label = label.strip()[:24]
        else:
            label = str(quote.get("name") or "")[:24] or None
        items.append({"code": code, "kind": kind, "label": label})
    elif action == "remove":
        if not any(item["code"] == code for item in items):
            raise ApiError(404, "not_found", f"{code} 不在自选里")
        items = [item for item in items if item["code"] != code]
    else:  # update
        found = False
        for item in items:
            if item["code"] != code:
                continue
            found = True
            kind = body.get("kind")
            if kind in {"etf", "stock"}:
                item["kind"] = str(kind)
            label = body.get("label")
            if isinstance(label, str):
                item["label"] = label.strip()[:24] or None
        if not found:
            raise ApiError(404, "not_found", f"{code} 不在自选里")

    payload = {"updated_at": store.iso_now(), "items": items}
    store.save_watchlist(payload)
    store.append_task("watchlist", ok=True, detail=f"{action} {code}")
    if action == "add":
        # 后台预取日线与 5 日分时，前端切到行情页时多数情况已经就绪
        _schedule_prefetch(code)
    return views.build_watchlist(app)


# ---------------------------------------------------------------- 路由


def dispatch_get(app: views.App, path: str, query: Mapping[str, list[str]]) -> dict[str, Any]:
    """GET 路由分派。"""
    if path == "/api/health":
        return views.build_health(app)
    if path == "/api/overview":
        return views.build_overview(app)
    if path == "/api/team":
        return views.build_team(app)
    if path == "/api/hotspots":
        return views.build_hotspots(app)
    if path == "/api/research":
        from . import research
        return research.build_payload()
    if path == "/api/research/replay":
        from . import replay
        code = _parse_code(_first(query, "code"))
        strategy = _first(query, 'path') or 'combined'
        cutoff = _first(query, 'cutoff') or None
        try:
            cost = int(_first(query, 'cost_bps') or '20')
            return replay.build_payload(code,strategy,cutoff,cost)
        except ValueError as exc:
            raise ApiError(400,'bad_request',str(exc)) from exc
    if path == "/api/research/evaluation":
        return store.read_json(store.path_for('research','evaluation-latest.json'),
                               {'state':'pending','rows':[],'notes':['后台会在完整日线到齐后自动生成事件复核']})
    if path == "/api/holdings":
        from . import holdings
        code = _parse_code(_first(query, "code"))
        period = _first(query, "period")
        if period and not __import__('re').fullmatch(r'\d{4}-(03-31|06-30|09-30|12-31)', period):
            raise ApiError(400, "bad_request", "报告期应为季度末日期")
        return holdings.build_payload(code, period)
    if path == "/api/data/coverage":
        from . import coverage
        result = store.read_json(store.path_for("coverage", "latest.json"), {}) or coverage.build_summary()
        result['maintenance'] = store.read_json(store.path_for('maintenance','status.json'),{})
        return result
    if path == "/api/market/share-history":
        from . import share_history
        return share_history.build_payload(_parse_code(_first(query, "code")))
    if path == "/api/market/bars":
        code = _parse_code(_first(query, "code"))
        span = _parse_span(_first(query, "span"))
        limit = _parse_int(
            _first(query, "limit"),
            default=config.BARS_LIMIT_DEFAULT,
            minimum=1,
            maximum=config.BARS_LIMIT_MAX,
            field="limit",
        )
        return views.build_bars(app, code, span, limit)
    if path == "/api/market/minute":
        code = _parse_code(_first(query, "code"))
        interval = _parse_interval(_first(query, "interval"))
        limit = _parse_int(
            _first(query, "limit"),
            default=config.MINUTE_LIMIT_DEFAULT,
            minimum=1,
            maximum=config.MINUTE_LIMIT_MAX,
            field="limit",
        )
        return views.build_minute(app, code, interval, limit)
    if path == "/api/team/minute":
        interval = _parse_interval(_first(query, "interval"))
        limit = _parse_int(
            _first(query, "limit"),
            default=config.MINUTE_LIMIT_DEFAULT,
            minimum=1,
            maximum=config.MINUTE_LIMIT_MAX,
            field="limit",
        )
        return views.build_team_minute(app, interval, limit)
    if path == "/api/sources":
        return views.build_sources(app)
    if path == "/api/market/intraday":
        code = _parse_code(_first(query, "code"))
        days = _parse_int(_first(query, "days"), default=5, minimum=1, maximum=5, field="days")
        return views.build_intraday(app, code, days)
    if path == "/api/market/quotes":
        return views.build_quotes(app, _parse_codes(_first(query, "codes")))
    if path == "/api/watchlist":
        return views.build_watchlist(app)
    if path == "/api/search":
        term = (_first(query, "q") or "").strip()
        if not term:
            raise ApiError(400, "bad_request", "缺少参数 q")
        if len(term) > 30:
            raise ApiError(400, "bad_request", "搜索词最长 30 字符")
        return views.build_search(app, term)
    if path == "/api/meta":
        return views.build_meta(app)
    raise ApiError(404, "not_found", f"没有这个接口：{path}")


def dispatch_post(app: views.App, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """POST 路由分派（目前只有自选变更）。"""
    if path == "/api/watchlist":
        return mutate_watchlist(app, body)
    raise ApiError(404, "not_found", f"没有这个接口：{path}")


# ---------------------------------------------------------------- HTTP Handler


class EtfHTTPRequestHandler(BaseHTTPRequestHandler):
    """单进程线程化 HTTP handler。"""

    server_version = "ETFMonitor/1.0"
    protocol_version = "HTTP/1.1"

    #: 由 :func:`make_server` 注入
    app: views.App

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 覆盖基类
        LOG.debug("%s %s", self.address_string(), fmt % args)

    # -- 基础输出 ------------------------------------------------------
    def _write_json(
        self,
        status: int,
        payload: Any,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        accept_encoding = self.headers.get("Accept-Encoding", "") or ""
        headers: dict[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        }
        if len(body) >= 1024 and "gzip" in accept_encoding.lower():
            body = gzip.compress(body, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("客户端提前断开 %s", self.path)

    def _write_error(self, error: ApiError) -> None:
        extra_headers = None
        if isinstance(error, RateLimited):
            extra_headers = [("Retry-After", str(error.retry_after))]
        self._write_json(
            error.status,
            {"error": {"code": error.code, "message": error.message}},
            extra_headers=extra_headers,
        )

    def _read_body(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "0")
        except ValueError as exc:
            raise ApiError(400, "bad_request", "Content-Length 非法") from exc
        if length <= 0:
            raise ApiError(400, "bad_request", "请求体为空")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "bad_request", "请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "bad_request", "请求体必须是 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "bad_request", "请求体必须是 JSON 对象")
        return payload

    # -- 认证辅助 ------------------------------------------------------
    def _forwarded_proto(self) -> str | None:
        return self.headers.get("X-Forwarded-Proto")

    def _cookie_secure(self) -> bool:
        return auth.cookie_secure(self._forwarded_proto())

    def _session(self) -> dict[str, Any] | None:
        token = auth.token_from_cookie(self.headers.get("Cookie"))
        return auth.parse_session(token)

    def _loopback_peer(self) -> bool:
        if not self.client_address:
            return False
        return str(self.client_address[0]) in {"127.0.0.1", "::1", "localhost"}

    def _remote_user(self) -> str | None:
        """forward 模式身份：仅信任本机回环 nginx 传来的 ``Remote-User``。"""
        if config.auth_mode() != "forward":
            return None
        if not self._loopback_peer():  # 直连外部地址时不信任该头
            return None
        value = (self.headers.get("Remote-User") or "").strip()
        if not value or len(value) > 64 or "\r" in value or "\n" in value:
            return None
        return value

    def _current_user(self) -> str | None:
        """当前用户名：forward 模式取 Remote-User，local 模式取会话 cookie。"""
        if config.auth_mode() == "forward":
            return self._remote_user()
        session = self._session()
        return str(session["user"]) if session else None

    @staticmethod
    def _is_admin(user: str | None) -> bool:
        return bool(user) and str(user) in config.admin_users()

    def _client_ip(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return str(self.client_address[0]) if self.client_address else "unknown"

    def _origin_allowed(self) -> bool:
        """POST 若带 Origin，必须与 Host / X-Forwarded-Host 同源。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = (
            (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip().lower()
        )
        if not host:
            return False
        try:
            return urlparse(origin).netloc.lower() == host
        except ValueError:
            return False

    def _require_session(self, path: str) -> bool:
        """除 /api/health 与 /api/auth/* 外的 /api/* 都需要认证。"""
        if not path.startswith("/api/"):
            return True
        if path == HEALTH_PATH or path == "/api/auth" or path.startswith(AUTH_PREFIX):
            return True
        if self._current_user() is not None:
            return True
        self._write_error(ApiError(401, "unauthorized", "需要登录"))
        return False

    # -- 认证端点 ------------------------------------------------------
    def _auth_session(self) -> None:
        mode = config.auth_mode()
        if mode == "forward":
            user = self._remote_user()
            self._write_json(
                200,
                {
                    "authenticated": user is not None,
                    "user": user,
                    "mode": "forward",
                    "expires_at": None,
                },
            )
            return
        session = self._session()
        self._write_json(
            200,
            {
                "authenticated": session is not None,
                "user": session["user"] if session else None,
                "mode": "local",
                "expires_at": store.iso(session["expires_at"]) if session else None,
            },
        )

    def _require_local_mode(self) -> None:
        if config.auth_mode() == "forward":
            raise ApiError(410, "gone", "认证由 Authelia 管理")

    def _auth_login(self) -> None:
        self._require_local_mode()
        body = self._read_body()
        username = body.get("username")
        password = body.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
            raise ApiError(400, "bad_request", "用户名或密码不能为空")
        username = username.strip()
        if len(username) > 64 or len(password) > 256:
            raise ApiError(400, "bad_request", "用户名或密码过长")
        client_ip = self._client_ip()
        retry_after = auth.limiter.retry_after(client_ip)
        if retry_after is not None:
            raise RateLimited(retry_after)
        if not auth.verify_password(username, password):
            auth.limiter.record_failure(client_ip)
            configured = auth.load_credentials() is not None
            message = "用户名或密码不正确" if configured else "尚未初始化登录密码，请先运行 set-password"
            raise ApiError(401, "unauthorized", message)
        auth.limiter.reset(client_ip)
        token, expires_at = auth.create_session(username)
        LOG.info("登录成功：user=%s ip=%s", username, client_ip)
        self._write_json(
            200,
            {"authenticated": True, "user": username, "expires_at": store.iso(expires_at)},
            extra_headers=[("Set-Cookie", auth.session_cookie(token, expires_at, secure=self._cookie_secure()))],
        )

    def _auth_logout(self) -> None:
        self._require_local_mode()
        self._write_json(
            200,
            {"authenticated": False},
            extra_headers=[("Set-Cookie", auth.clear_cookie(secure=self._cookie_secure()))],
        )

    # -- 会话吊销（Authelia Redis） ------------------------------------
    def _admin_sessions(self) -> None:
        user = self._current_user()
        if user is None:
            raise ApiError(401, "unauthorized", "需要登录")
        if not self._is_admin(user):
            raise ApiError(403, "forbidden", "需要管理员权限")
        if config.auth_mode() != "forward":
            self._write_json(
                200,
                {"mode": "local", "active_keys": None, "revoke_supported": False, "current_user": user},
            )
            return
        try:
            active, total = redis_client.session_stats()
        except redis_client.RedisError as exc:
            raise ApiError(503, "service_unavailable", f"Redis 会话存储不可用：{exc}") from exc
        self._write_json(
            200,
            {
                "mode": "redis",
                "active_keys": active,
                "redis_keys": total,
                "revoke_supported": True,
                "current_user": user,
            },
        )

    def _admin_revoke_all(self) -> None:
        user = self._current_user()
        if user is None:
            raise ApiError(401, "unauthorized", "需要登录")
        if config.auth_mode() != "forward":
            raise ApiError(404, "not_found", "local 模式不支持会话吊销")
        if not self._is_admin(user):
            raise ApiError(403, "forbidden", "需要管理员权限")
        try:
            revoked = redis_client.revoke_all_sessions()
        except redis_client.RedisError as exc:
            raise ApiError(503, "service_unavailable", f"Redis 会话存储不可用：{exc}") from exc
        LOG.warning("管理员 %s 吊销了全部会话（%d）", user, revoked)
        self._write_json(200, {"ok": True, "revoked": revoked})

    # -- 路由 ----------------------------------------------------------
    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if method == "GET":
                if path == "/api/auth/session":
                    self._auth_session()
                    return
                if path in {"/api/auth/login", "/api/auth/logout"}:
                    self._method_not_allowed()
                    return
                if path == "/api/admin/sessions":
                    if not self._require_session(path):
                        return
                    self._admin_sessions()
                    return
                if not self._require_session(path):
                    return
                payload = dispatch_get(self.app, path, query)
            else:
                if not self._origin_allowed():
                    raise ApiError(403, "forbidden", "跨站请求被拒绝")
                if path == "/api/auth/login":
                    self._auth_login()
                    return
                if path == "/api/auth/logout":
                    self._auth_logout()
                    return
                if path == "/api/auth/session":
                    self._method_not_allowed()
                    return
                if not self._require_session(path):
                    return
                if path == "/api/admin/sessions/revoke-all":
                    self._require_client_header()
                    self._admin_revoke_all()
                    return
                if path == "/api/watchlist":
                    self._require_client_header()
                else:
                    self._method_not_allowed()
                    return
                body = self._read_body()
                payload = dispatch_post(self.app, path, body)
        except ApiError as error:
            if error.status >= 500:
                LOG.error("%s %s -> %s: %s", method, path, error.code, error.message)
            self._write_error(error)
            return
        except LookupError as error:
            self._write_error(ApiError(404, "not_found", str(error) or "数据不存在"))
            return
        except ValueError as error:
            self._write_error(ApiError(400, "bad_request", str(error) or "参数不合法"))
            return
        except Exception:  # noqa: BLE001 - 兜底：不把异常细节暴露给客户端，但记录堆栈
            LOG.exception("%s %s 处理失败", method, path)
            self._write_error(ApiError(500, "internal", "服务内部错误"))
            return
        self._write_json(200, payload)

    def _require_client_header(self) -> None:
        """写接口 CSRF：要求 ``X-ETF-Client: web-v1``。"""
        client = (self.headers.get(CLIENT_HEADER) or "").strip()
        if client != CLIENT_VALUE:
            raise ApiError(403, "forbidden", f"缺少 {CLIENT_HEADER}: {CLIENT_VALUE} 请求头")

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        body = json.dumps(
            {"error": {"code": "method_not_allowed", "message": "只支持 GET/POST"}}, ensure_ascii=False
        ).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()


class EtfHTTPServer(ThreadingHTTPServer):
    """允许测试时绑定端口 0，线程 daemon 便于退出。"""

    allow_reuse_address = True
    daemon_threads = True


def make_server(app: views.App, host: str = "127.0.0.1", port: int = 8765) -> EtfHTTPServer:
    """创建（未启动的）HTTP server；测试里用 ``port=0`` 取随机端口。"""
    handler = type("BoundEtfHandler", (EtfHTTPRequestHandler,), {"app": app})
    server = EtfHTTPServer((host, port), handler)
    server.app = app  # type: ignore[attr-defined]
    return server


def serve_forever(app: views.App, host: str = "127.0.0.1", port: int = 8765) -> None:
    """阻塞运行 HTTP 服务（调用方负责启动/停止采集线程）。"""
    server = make_server(app, host, port)
    LOG.info("ETF 观察台 API 已启动：http://%s:%d", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        LOG.info("HTTP 服务已关闭")

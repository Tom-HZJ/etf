"""认证单测：登录/会话/cookie/限速/epoch/Origin，以及 set-password CLI。"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest import mock

from etfmon import api, auth, config, store, views
from etfmon.collectors import QuoteCache

USER = "tom"
PASSWORD = "correct-horse-battery-staple"


class AuthHttpTest(unittest.TestCase):
    """每个用例独立数据目录 + 独立服务，避免限速/epoch 互相干扰。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        auth.set_password(USER, PASSWORD)
        auth.limiter.clear()
        self.app = views.App(cache=QuoteCache(), started_at=store.now())
        self.server = api.make_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        auth.limiter.clear()
        self._env.stop()
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        cookie: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        merged = {"Content-Type": "application/json", **(headers or {})}
        if cookie:
            merged["Cookie"] = cookie
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=merged)
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read()
                    return response.status, dict(response.headers), json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as error:
                raw = error.read()
                return error.code, dict(error.headers), json.loads(raw.decode("utf-8"))
            except (ConnectionAbortedError, ConnectionResetError) as error:
                # Windows 下本地 server 偶发连接中止，重试一次（仅测试辅助）
                last_error = error
        raise last_error  # type: ignore[misc]

    def login(self, password: str = PASSWORD, *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], dict]:
        return self.request("POST", "/api/auth/login", payload={"username": USER, "password": password}, headers=headers)

    def login_cookie(self, password: str = PASSWORD) -> str:
        status, headers, _payload = self.login(password)
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";")[0]

    # -- 公开/受保护 ----------------------------------------------------
    def test_health_is_public(self) -> None:
        status, _headers, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn("status", payload)

    def test_protected_api_requires_login(self) -> None:
        for path in ("/api/overview", "/api/team", "/api/market/quotes", "/api/watchlist"):
            status, _headers, payload = self.request("GET", path)
            self.assertEqual(status, 401, path)
            self.assertEqual(payload["error"]["code"], "unauthorized")
            self.assertEqual(payload["error"]["message"], "需要登录")

    def test_session_endpoint_anonymous(self) -> None:
        status, _headers, payload = self.request("GET", "/api/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"authenticated": False, "user": None, "mode": "local", "expires_at": None})

    # -- 登录 ----------------------------------------------------------
    def test_login_success_and_cookie(self) -> None:
        status, headers, payload = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"], USER)
        self.assertTrue(payload["expires_at"])
        cookie = headers["Set-Cookie"]
        self.assertIn(f"{config.SESSION_COOKIE_NAME}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn(f"Max-Age={config.SESSION_DAYS * 86400}", cookie)
        self.assertNotIn("Secure", cookie)  # 本地 http 不加 Secure
        # 带 cookie 访问受保护接口
        status, _headers, overview = self.request("GET", "/api/overview", cookie=cookie.split(";")[0])
        self.assertEqual(status, 200)
        self.assertIn("brief", overview)
        status, _headers, session = self.request("GET", "/api/auth/session", cookie=cookie.split(";")[0])
        self.assertEqual(status, 200)
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["user"], USER)
        self.assertTrue(session["expires_at"])

    def test_cookie_secure_via_forwarded_proto(self) -> None:
        status, headers, _payload = self.login(headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        self.assertIn("Secure", headers["Set-Cookie"])

    def test_cookie_secure_via_env(self) -> None:
        with mock.patch.dict(os.environ, {config.COOKIE_SECURE_ENV: "1"}):
            status, headers, _payload = self.login()
        self.assertEqual(status, 200)
        self.assertIn("Secure", headers["Set-Cookie"])

    def test_login_wrong_password(self) -> None:
        status, _headers, payload = self.login("wrong-password")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")
        self.assertEqual(payload["error"]["message"], "用户名或密码不正确")

    def test_login_unknown_user(self) -> None:
        status, _headers, payload = self.request(
            "POST", "/api/auth/login", payload={"username": "nobody", "password": PASSWORD}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_login_before_password_set(self) -> None:
        auth.auth_path().unlink()
        status, _headers, payload = self.login()
        self.assertEqual(status, 401)
        self.assertIn("尚未初始化", payload["error"]["message"])

    def test_login_missing_fields(self) -> None:
        status, _headers, payload = self.request("POST", "/api/auth/login", payload={"username": USER})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    # -- 退出 ----------------------------------------------------------
    def test_logout_clears_cookie(self) -> None:
        cookie = self.login_cookie()
        status, headers, payload = self.request("POST", "/api/auth/logout", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"authenticated": False})
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        # 浏览器清掉 cookie 后访问受保护接口 → 401
        status, _headers, _payload = self.request("GET", "/api/overview")
        self.assertEqual(status, 401)

    # -- 限速 ----------------------------------------------------------
    def test_rate_limit_after_five_failures(self) -> None:
        headers = {"X-Forwarded-For": "203.0.113.11"}
        for _ in range(config.LOGIN_MAX_FAILURES):
            status, _headers, payload = self.login("bad-password", headers=headers)
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"]["code"], "unauthorized")
        status, response_headers, payload = self.login("bad-password", headers=headers)
        self.assertEqual(status, 429)
        self.assertEqual(payload["error"]["code"], "rate_limited")
        self.assertGreater(int(response_headers.get("Retry-After", "0")), 0)
        # 限速期间即使密码正确也 429
        status, _headers, _payload = self.login(PASSWORD, headers=headers)
        self.assertEqual(status, 429)

    def test_rate_limit_isolated_per_ip(self) -> None:
        headers = {"X-Forwarded-For": "203.0.113.12"}
        for _ in range(config.LOGIN_MAX_FAILURES):
            self.login("bad-password", headers=headers)
        status, _headers, _payload = self.login(PASSWORD, headers={"X-Forwarded-For": "203.0.113.13"})
        self.assertEqual(status, 200)

    def test_rate_limit_success_resets(self) -> None:
        headers = {"X-Forwarded-For": "203.0.113.14"}
        for _ in range(4):
            self.login("bad-password", headers=headers)
        status, _headers, _payload = self.login(PASSWORD, headers=headers)
        self.assertEqual(status, 200)
        status, _headers, payload = self.login("bad-password", headers=headers)
        self.assertEqual(status, 401)  # 计数已清零

    # -- epoch / token --------------------------------------------------
    def test_epoch_invalidates_old_session(self) -> None:
        cookie = self.login_cookie()
        status, _headers, _payload = self.request("GET", "/api/overview", cookie=cookie)
        self.assertEqual(status, 200)
        auth.set_password(USER, "new-password-456")
        status, _headers, payload = self.request("GET", "/api/overview", cookie=cookie)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")
        # 新密码可以登录
        status, _headers, _payload = self.login("new-password-456")
        self.assertEqual(status, 200)

    def test_tampered_token_rejected(self) -> None:
        cookie = self.login_cookie()
        name, _sep, token = cookie.partition("=")
        tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        status, _headers, _payload = self.request("GET", "/api/overview", cookie=f"{name}={tampered}")
        self.assertEqual(status, 401)

    def test_expired_token_rejected(self) -> None:
        token, _expires = auth.create_session(USER, now=store.now() - timedelta(days=config.SESSION_DAYS + 1))
        status, _headers, _payload = self.request(
            "GET", "/api/overview", cookie=f"{config.SESSION_COOKIE_NAME}={token}"
        )
        self.assertEqual(status, 401)

    # -- Origin --------------------------------------------------------
    def test_cross_origin_post_forbidden(self) -> None:
        status, _headers, payload = self.request(
            "POST",
            "/api/auth/login",
            payload={"username": USER, "password": PASSWORD},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_same_origin_post_allowed(self) -> None:
        origin = f"http://127.0.0.1:{self.port}"
        status, _headers, _payload = self.request(
            "POST",
            "/api/auth/login",
            payload={"username": USER, "password": PASSWORD},
            headers={"Origin": origin},
        )
        self.assertEqual(status, 200)

    def test_post_requires_client_header_after_login(self) -> None:
        cookie = self.login_cookie()
        status, _headers, payload = self.request(
            "POST", "/api/watchlist", payload={"action": "remove", "code": "sh560050"}, cookie=cookie
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")
        # 带 X-ETF-Client 后逻辑正常（remove 不在自选里的代码 → 404）
        status, _headers, _payload = self.request(
            "POST",
            "/api/watchlist",
            payload={"action": "remove", "code": "sh560050"},
            cookie=cookie,
            headers={"X-ETF-Client": "web-v1"},
        )
        self.assertEqual(status, 404)

    def test_unauthenticated_post_returns_401(self) -> None:
        status, _headers, payload = self.request(
            "POST", "/api/watchlist", payload={"action": "remove", "code": "sh560050"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")


class AuthStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_password_file_format_and_epoch(self) -> None:
        first = auth.set_password(USER, PASSWORD)
        self.assertEqual(first["epoch"], 1)
        self.assertEqual(first["iterations"], config.PBKDF2_ITERATIONS)
        raw = json.loads(auth.auth_path().read_text(encoding="utf-8"))
        self.assertEqual(set(raw), {"user", "salt_b64", "hash_b64", "iterations", "epoch", "updated_at"})
        self.assertEqual(raw["iterations"], config.PBKDF2_ITERATIONS)
        self.assertNotEqual(raw["hash_b64"], PASSWORD)
        self.assertNotIn(PASSWORD, json.dumps(raw))
        second = auth.set_password(USER, "another-pass-789")
        self.assertEqual(second["epoch"], 2)
        self.assertEqual(auth.current_epoch(), 2)
        self.assertFalse(auth.verify_password(USER, PASSWORD))  # 旧密码失效
        self.assertTrue(auth.verify_password(USER, "another-pass-789"))
        self.assertFalse(auth.verify_password("nobody", "another-pass-789"))

    def test_password_file_private(self) -> None:
        auth.set_password(USER, PASSWORD)
        mode = auth.auth_path().stat().st_mode & 0o777
        if os.name == "posix":
            self.assertEqual(mode, 0o600)
        else:  # Windows 只有只读位，至少确认文件存在且可读
            self.assertTrue(auth.auth_path().is_file())

    def test_short_password_rejected(self) -> None:
        with self.assertRaises(ValueError):
            auth.set_password(USER, "short")

    def test_describe_hides_hash(self) -> None:
        self.assertFalse(auth.describe()["configured"])
        auth.set_password(USER, PASSWORD)
        summary = auth.describe()
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["user"], USER)
        self.assertNotIn("hash", json.dumps(summary))

    def test_cookie_parser(self) -> None:
        self.assertIsNone(auth.token_from_cookie(None))
        self.assertIsNone(auth.token_from_cookie("other=1"))
        self.assertEqual(auth.token_from_cookie("a=1; etf_session=abc.def; b=2"), "abc.def")

    def test_login_limiter_unit(self) -> None:
        limiter = auth.LoginLimiter(max_failures=2, window_s=100)
        self.assertIsNone(limiter.retry_after("ip"))
        limiter.record_failure("ip", now=0)
        limiter.record_failure("ip", now=1)
        retry = limiter.retry_after("ip", now=2)
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertGreater(retry, 0)
        self.assertIsNone(limiter.retry_after("ip", now=101))  # 窗口滑过后恢复
        limiter.record_failure("ip")
        limiter.reset("ip")
        self.assertIsNone(limiter.retry_after("ip"))


class SetPasswordCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        from etfmon import __main__ as cli

        self.cli = cli

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _run(self, argv: list[str], stdin: str = "") -> tuple[int, str]:
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(sys, "stdout", output):
            code = self.cli.main(argv)
        return code, output.getvalue()

    def test_set_password_from_stdin(self) -> None:
        code, output = self._run(["set-password", "--user", "tom", "--password-stdin"], stdin=PASSWORD + "\n")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"], "tom")
        self.assertEqual(payload["epoch"], 1)
        self.assertTrue(auth.verify_password("tom", PASSWORD))

    def test_set_password_twice_bumps_epoch(self) -> None:
        self._run(["set-password", "--user", "tom", "--password-stdin"], stdin="first-pass\n")
        code, output = self._run(["set-password", "--user", "tom", "--password-stdin"], stdin="second-pass\n")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["epoch"], 2)
        self.assertTrue(auth.verify_password("tom", "second-pass"))
        self.assertFalse(auth.verify_password("tom", "first-pass"))

    def test_show_does_not_print_hash(self) -> None:
        code, output = self._run(["set-password", "--show"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["configured"])
        self._run(["set-password", "--user", "tom", "--password-stdin"], stdin="show-pass\n")
        code, output = self._run(["set-password", "--show"])
        self.assertEqual(code, 0)
        summary = json.loads(output)
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["user"], "tom")
        self.assertNotIn("hash", output)

    def test_short_password_cli_error(self) -> None:
        code, _output = self._run(["set-password", "--user", "tom", "--password-stdin"], stdin="short\n")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

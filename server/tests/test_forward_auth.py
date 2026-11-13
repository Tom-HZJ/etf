"""Authelia 前向认证单测：Remote-User、session mode、login/logout 410、管理员会话端点。"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from unittest import mock

from etfmon import api, auth, collectors, config, observability, store, views

from tests.support_redis import FakeRedisServer

CLIENT_HEADER = {"X-ETF-Client": "web-v1"}


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def request(
    base: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    merged = {"Content-Type": "application/json", **(headers or {})}
    last_error: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(base + path, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), json.loads(error.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError) as error:
            # Windows 上偶发连接重置/中止：重试一次，避免测试 flake
            last_error = error
            time.sleep(0.05 * (attempt + 1))
    assert last_error is not None
    raise last_error


class _ServerCase(unittest.TestCase):
    env: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._env = mock.patch.dict(
            os.environ,
            {"ETF_DATA": cls._tmp.name, **cls.env},
        )
        cls._env.start()
        store.ensure_layout()
        cls.app = views.App(cache=collectors.QuoteCache(), started_at=store.now())
        cls.server = api.make_server(cls.app, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._env.stop()
        cls._tmp.cleanup()


class ForwardAuthTest(_ServerCase):
    env = {
        "ETF_AUTH_MODE": "forward",
        "ETF_ADMIN_USERS": "tom",
        "ETF_REDIS_PORT": str(free_port()),  # 没有 Redis → admin sessions 503
    }

    def test_health_public(self) -> None:
        status, _headers, payload = request(self.base, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn("status", payload)

    def test_protected_requires_remote_user(self) -> None:
        status, _headers, payload = request(self.base, "GET", "/api/overview")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_session_forward_modes(self) -> None:
        status, _headers, payload = request(self.base, "GET", "/api/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {"authenticated": False, "user": None, "mode": "forward", "expires_at": None},
        )
        status, _headers, payload = request(
            self.base, "GET", "/api/auth/session", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {"authenticated": True, "user": "tom", "mode": "forward", "expires_at": None},
        )

    def test_authenticated_requests(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/overview", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 200)
        self.assertIn("brief", payload)
        status, _headers, payload = request(
            self.base, "GET", "/api/sources", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["sources"]), len(observability.SOURCES))
        self.assertIn("quote", payload["groups"])
        self.assertIsInstance(payload["alerts"], list)

    def test_blank_remote_user_rejected(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/overview", headers={"Remote-User": "   "}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_test_account_can_read_but_cannot_change_watchlist(self) -> None:
        headers = {"Remote-User": "test", **CLIENT_HEADER}
        status, _, before = request(self.base, "GET", "/api/watchlist", headers=headers)
        self.assertEqual(status, 200)
        status, _, _ = request(self.base, "POST", "/api/watchlist", headers=headers,
                               payload={"action": "remove", "code": before["items"][0]["code"]})
        self.assertEqual(status, 403)
        _, _, after = request(self.base, "GET", "/api/watchlist", headers=headers)
        self.assertEqual(after["items"], before["items"])

    def test_private_routes_check_authenticated_owner(self) -> None:
        for path in ("/api/private/funds?user=tom", "/api/private/stock?code=sh600000&user=tom"):
            self.assertEqual(request(self.base, "GET", path)[0], 401)
            for user in ("test", "bob"):
                self.assertEqual(request(self.base, "GET", path, headers={"Remote-User": user})[0], 403)
        snapshot = {"snapshot_date": None, "items": [{"id": "fixture"}]}
        store.write_json(store.path_for("private", "tom", "funds.json"), snapshot)
        status, headers, payload = request(self.base, "GET", "/api/private/funds", headers={"Remote-User": "tom"})
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(payload, snapshot)

    def test_stock_forecast_expires_when_its_first_session_completes(self) -> None:
        code = "sh600000"
        base = store.path_for("private", "tom")
        store.write_json(base / "stock-bars" / f"{code}.json", {
            "code": code, "name": "Fixture", "source": "Fixture", "adjustment": "qfq",
            "bars": [{"date": "2026-11-10", "open": 10, "high": 11, "low": 9, "close": 10}],
        })
        store.write_json(base / "forecasts" / f"{code}.json", {
            "code": code, "as_of": "2026-11-10", "adjustment": "qfq",
            "sessions": [{"date": "2026-11-11", "close": 10.1}, {"date": "2026-11-12", "close": 10.2}],
        })
        for moment, ready in (("2026-11-11T14:00:00+08:00", True), ("2026-11-11T16:00:00+08:00", False)):
            with mock.patch.object(store, "now", return_value=datetime.fromisoformat(moment)):
                status, _, result = request(self.base, "GET", f"/api/private/stock?code={code}", headers={"Remote-User": "tom"})
            self.assertEqual(status, 200)
            self.assertEqual(result["forecast"] is not None, ready)

    def test_login_logout_gone(self) -> None:
        for path in ("/api/auth/login", "/api/auth/logout"):
            payload_body = {"username": "tom", "password": "x"} if path.endswith("login") else None
            status, _headers, payload = request(
                self.base, "POST", path, payload=payload_body, headers={"Remote-User": "tom"}
            )
            self.assertEqual(status, 410, path)
            self.assertEqual(payload["error"]["code"], "gone")
            self.assertEqual(payload["error"]["message"], "认证由 Authelia 管理")

    def test_admin_sessions_503_without_redis(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/admin/sessions", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "service_unavailable")

    def test_admin_sessions_non_admin(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/admin/sessions", headers={"Remote-User": "bob"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")


class ForwardAdminRedisTest(_ServerCase):
    env: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.redis = FakeRedisServer(keys=4)  # type: ignore[attr-defined]
        cls.redis.__enter__()
        cls.env = {  # type: ignore[misc]
            "ETF_AUTH_MODE": "forward",
            "ETF_ADMIN_USERS": "tom",
            "ETF_REDIS_PORT": str(cls.redis.port),
            "ETF_REDIS_PASSWORD": "",
        }
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.redis.__exit__(None, None, None)

    def test_admin_sessions_redis(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/admin/sessions", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "redis")
        self.assertEqual(payload["active_keys"], 4)
        self.assertEqual(payload["redis_keys"], 4)
        self.assertTrue(payload["revoke_supported"])
        self.assertEqual(payload["current_user"], "tom")

    def test_revoke_requires_admin_and_client_header(self) -> None:
        status, _headers, payload = request(
            self.base, "POST", "/api/admin/sessions/revoke-all", headers={"Remote-User": "tom"}
        )
        self.assertEqual(status, 403)  # 缺 X-ETF-Client
        status, _headers, payload = request(
            self.base,
            "POST",
            "/api/admin/sessions/revoke-all",
            headers={"Remote-User": "bob", **CLIENT_HEADER},
        )
        self.assertEqual(status, 403)  # 非管理员
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_revoke_all(self) -> None:
        self.redis.state["keys"] = [f"authelia:session:{index}" for index in range(6)]
        status, _headers, payload = request(
            self.base,
            "POST",
            "/api/admin/sessions/revoke-all",
            headers={"Remote-User": "tom", **CLIENT_HEADER},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "revoked": 6})
        status, _headers, payload = request(
            self.base, "GET", "/api/admin/sessions", headers={"Remote-User": "tom"}
        )
        self.assertEqual(payload["active_keys"], 0)
        commands = [command[0].upper() for command in self.redis.commands]
        self.assertIn("FLUSHDB", commands)


class LocalAdminTest(_ServerCase):
    env = {"ETF_AUTH_MODE": "local", "ETF_ADMIN_USERS": "tom", "ETF_REDIS_PORT": str(free_port())}
    cookie = ""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        auth.set_password("tom", "local-pass-123")
        status, headers, _payload = request(
            cls.base,
            "POST",
            "/api/auth/login",
            payload={"username": "tom", "password": "local-pass-123"},
        )
        assert status == 200, status
        cls.cookie = headers["Set-Cookie"].split(";")[0]

    def test_admin_sessions_local_mode(self) -> None:
        status, _headers, payload = request(
            self.base, "GET", "/api/admin/sessions", headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {"mode": "local", "active_keys": None, "revoke_supported": False, "current_user": "tom"},
        )

    def test_revoke_all_local_404(self) -> None:
        status, _headers, payload = request(
            self.base,
            "POST",
            "/api/admin/sessions/revoke-all",
            headers={"Cookie": self.cookie, **CLIENT_HEADER},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()

"""API 冒烟测试：本地起 HTTP 服务，验证契约结构、错误体、POST 校验与缓存头。"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from etfmon import api, auth, collectors, config, sources, store, views

CLIENT_HEADER = {"X-ETF-Client": "web-v1"}
TEST_USER = "tester"
TEST_PASSWORD = "test-pass-123"


def weekdays_ending(end: date, count: int) -> list[str]:
    result: list[str] = []
    cursor = end
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(result))


def make_bars(dates: list[str], start_price: float, step: float) -> list[dict]:
    bars = []
    price = start_price
    for date_text in dates:
        bars.append(
            {"date": date_text, "open": price, "high": price * 1.01, "low": price * 0.99, "close": price, "volume": 1000.0}
        )
        price += step
    return bars


def quote_of(code: str, name: str, price: float, shares: float) -> dict:
    return {
        "code": code,
        "name": name,
        "kind": "etf",
        "price": price,
        "pre_close": price - 0.05,
        "open": price - 0.02,
        "high": price + 0.03,
        "low": price - 0.04,
        "change": 0.05,
        "change_pct": 0.01,
        "volume": 1000.0,
        "amount": 4000.0,
        "turnover": 0.01,
        "shares": shares,
        "market_cap": price * shares,
        "quote_time": "2026-11-06T15:00:00+08:00",
        "source": "test",
        "stale": False,
    }


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._env = mock.patch.dict(os.environ, {"ETF_DATA": cls._tmp.name})
        cls._env.start()
        root = Path(cls._tmp.name)
        store.ensure_layout()
        dates = weekdays_ending(date(2026, 11, 6), 80)
        store.save_bars("sh510300", make_bars(dates, 3.5, 0.01), name="沪深300ETF", source="test")
        store.save_bars("sh512480", make_bars(dates, 0.7, 0.001), name="半导体ETF", source="test")
        store.save_daily(
            {
                "days": {
                    "2026-11-05": {
                        "sh510300": {"shares": 1000.0, "price": 3.9},
                        "sh512480": {"shares": 2000.0, "price": 0.78},
                    },
                    "2026-11-06": {
                        "sh510300": {"shares": 1100.0, "price": 4.1},
                        "sh512480": {"shares": 1900.0, "price": 0.79},
                    },
                },
                "intraday": {},
            }
        )
        # Team and overview use complete exchange-cleared shares and unadjusted closes.
        settled_days = ("2026-11-05", "2026-11-06")
        for day, share_count in zip(settled_days, (1000.0, 1100.0)):
            for exchange, prefix in (("sse", "sh"), ("szse", "sz")):
                store.write_json(
                    store.path_for("share_history", exchange, f"{day}.json"),
                    {
                        "date": day,
                        "source": f"{exchange}_settled_etf_shares",
                        "unit": "份",
                        "rows": {
                            code: {"shares": share_count}
                            for code in config.TEAM_CODES
                            if code.startswith(prefix)
                        },
                    },
                )
        for code in config.TEAM_CODES:
            store.write_json(
                store.path_for("share_history", "prices", f"{code}.json"),
                {
                    "source": "tencent_unadjusted_daily",
                    "adjustment": "none",
                    "closes": dict.fromkeys(settled_days, 1.0),
                },
            )
        store.save_flows(
            "sh510300",
            {
                "code": "sh510300",
                "name": "沪深300ETF",
                "periods": [
                    {
                        "date": "2026-09-30",
                        "subscription": 10.0,
                        "redemption": 4.0,
                        "net": 6.0,
                        "net_pct": 0.02,
                        "start_shares": 300.0,
                        "total_shares": 306.0,
                        "large_redemption": False,
                    }
                ],
                "holder_history": [],
            },
        )
        store.write_json(
            root / "intraday" / "sh510300.json",
            {
                "code": "sh510300",
                "name": "沪深300ETF",
                "pre_close": 4.0,
                "days": [
                    {
                        "date": "2026-11-06",
                        "points": [
                            {"time": "09:30", "label": "2026-11-06 09:30", "price": 4.05, "avg": 4.05, "volume": 100.0}
                        ],
                        "change": 0.0125,
                    }
                ],
            },
        )
        cls.cache = collectors.QuoteCache()
        batch = sources.QuoteBatch(
            quotes={
                "sh510300": quote_of("sh510300", "沪深300ETF", 4.124, 1100.0),
                "sh512480": quote_of("sh512480", "半导体ETF", 0.79, 1900.0),
            },
            fetched_at=store.iso_now(),
            source="test",
        )
        cls.cache.update(batch)
        payload = views.compute_hotspot_payload(batch.quotes)
        if payload.get("as_of"):
            store.save_hotspots_latest(payload)
            store.save_hotspots_history(views.build_hotspot_history(payload["rows"], payload["as_of"]))
        cls.app = views.App(cache=cls.cache, started_at=store.now())
        # 自选 add 会触发后台预取；测试里禁掉真实线程，避免访问网络
        cls._prefetch_patch = mock.patch("etfmon.api._schedule_prefetch")
        cls._prefetch_mock = cls._prefetch_patch.start()
        cls.server = api.make_server(cls.app, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        cls.thread.start()
        # 认证：创建测试密码并登录一次，后续请求默认带上会话 cookie
        auth.set_password(TEST_USER, TEST_PASSWORD)
        cls.cookie = cls._login(TEST_USER, TEST_PASSWORD)

    @classmethod
    def _login(cls, username: str, password: str) -> str:
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/api/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            set_cookie = response.headers.get("Set-Cookie") or ""
        return set_cookie.split(";")[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._prefetch_patch.stop()
        cls._env.stop()
        cls._tmp.cleanup()

    # -- HTTP helpers --------------------------------------------------
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(self.url(path), headers={"Cookie": self.cookie, **(headers or {})})
        return self._open(request)

    def _open(self, request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
        """本地 socket 偶发 ConnectionAborted，重试一次（仅测试辅助，不影响被测代码）。"""
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as error:
                return error.code, dict(error.headers), error.read()
            except (ConnectionAbortedError, ConnectionResetError) as error:
                last_error = error
        raise last_error  # type: ignore[misc]

    def json_get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        status, response_headers, body = self.get(path, headers)
        if response_headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return status, json.loads(body.decode("utf-8"))

    def post(self, path: str, payload: dict | bytes, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url(path),
            data=body,
            method="POST",
            headers={"Cookie": self.cookie, **(headers or {})},
        )
        status, _headers, raw = self._open(request)
        return status, json.loads(raw.decode("utf-8"))

    # -- tests ---------------------------------------------------------
    def test_health(self) -> None:
        status, payload = self.json_get("/api/health")
        self.assertEqual(status, 200)
        for key in ("generated_at", "status", "version", "uptime_s", "session", "trade_date", "sources"):
            self.assertIn(key, payload)
        self.assertIn(payload["status"], {"ok", "degraded"})
        self.assertIn(payload["session"], {"pre", "morning", "lunch", "afternoon", "post", "closed", "weekend"})

    def test_overview_structure(self) -> None:
        status, payload = self.json_get("/api/overview")
        self.assertEqual(status, 200)
        for key in (
            "generated_at",
            "as_of",
            "trade_date",
            "session",
            "is_trading",
            "indices",
            "national",
            "hotspots",
            "breadth",
            "brief",
            "watchlist",
            "data_health",
        ):
            self.assertIn(key, payload)
        national = payload["national"]
        for key in (
            "today_flow",
            "today_flow_partial",
            "flow_date",
            "today_share_delta",
            "five_day_flow",
            "five_day_days",
            "month_flow",
            "total_value",
            "total_shares",
            "trend_state",
            "trend_text",
            "history_days",
            "daily_series",
        ):
            self.assertIn(key, national)
        self.assertIsNotNone(national["today_flow"])
        self.assertEqual(national["flow_date"], "2026-11-06")
        self.assertGreaterEqual(national["five_day_days"], 1)
        self.assertEqual(national["month_flow"], None)  # 相邻日不足 5 天
        brief = payload["brief"]
        self.assertIn(brief["tone"], {"risk", "neutral", "positive"})
        self.assertLessEqual(len(brief["headline"]), 30)
        self.assertLessEqual(len(brief["bullets"]), 3)
        self.assertTrue(all(len(bullet) <= 40 for bullet in brief["bullets"]))
        for key in ("above_ma20", "above_ma60", "total", "advancers", "decliners", "median_ret_20d"):
            self.assertIn(key, payload["breadth"])
        self.assertEqual(len(payload["watchlist"]), len(store.load_watchlist()["items"]))

    def test_team_structure_and_gap_days(self) -> None:
        status, payload = self.json_get("/api/team")
        self.assertEqual(status, 200)
        self.assertEqual(payload["basket"]["count"], 26)
        self.assertEqual(len(payload["rows"]), 26)
        for day in payload["history"]:
            self.assertIn("gap_days", day)
            self.assertGreaterEqual(day["gap_days"], 1)
        self.assertIn("excluded_days", payload["cumulative"])
        self.assertEqual(payload["latest_close"]["date"], "2026-11-06")
        self.assertEqual(payload["latest_close"]["gap_days"], 1)
        # 只有 11-05/11-06 相邻，11-04 不存在，累计只算 1 天
        self.assertEqual(payload["cumulative"]["days"], 1)
        for span in ("day", "week", "month"):
            self.assertIn(span, payload["chart"]["spans"])
            self.assertIn("bars", payload["chart"]["spans"][span])
        self.assertIsInstance(payload["flow_periods"], list)
        self.assertIsInstance(payload["intraday"], list)
        self.assertTrue(payload["notes"]["method"])
        if payload["live_flow"] is not None:
            self.assertEqual(set(payload["live_flow"]), {"date", "previous_day", "amount", "share_delta", "partial", "time"})

    def test_hotspots_structure(self) -> None:
        status, payload = self.json_get("/api/hotspots")
        self.assertEqual(status, 200)
        for key in ("version", "rule", "state", "headline", "counts", "summary", "rows", "agents", "flow", "warnings", "method", "limitations"):
            self.assertIn(key, payload)
        self.assertEqual(set(payload["counts"]), {"hot", "warming", "crowded", "cooling", "fading", "neutral", "unavailable"})
        self.assertEqual(len(payload["rows"]), len(config.HOTSPOT_CODES))
        row = payload["rows"][0]
        for key in (
            "rank",
            "code",
            "name",
            "direction",
            "group",
            "price",
            "change_pct",
            "data_date",
            "ret_1d",
            "ret_5d",
            "ret_20d",
            "relative_20d",
            "ma20",
            "ma60",
            "vs_ma20",
            "close_above_ma20",
            "ma20_above_ma60",
            "volume_ratio_5d",
            "breakout_20d",
            "year_loc",
            "heat",
            "state",
            "state_label",
            "rank_change_5d",
            "evidence",
            "spark",
        ):
            self.assertIn(key, row)
        self.assertIn(payload["rows"][0]["state"], analytics_states())
        self.assertEqual(len(payload["agents"]), 4)
        self.assertIn("largest_csi300", payload["flow"])

    def test_sources_endpoint(self) -> None:
        status, payload = self.json_get("/api/sources")
        self.assertEqual(status, 200)
        self.assertIn("sources", payload)
        self.assertIn("groups", payload)
        self.assertIn("alerts", payload)
        self.assertEqual(len(payload["sources"]), 10)
        row = payload["sources"][0]
        for key in (
            "id",
            "name",
            "kind",
            "priority",
            "status",
            "ok",
            "latency_ms",
            "success_count",
            "failure_count",
            "fallback_count",
            "consecutive_failures",
            "circuit",
            "last_ok_at",
            "last_error_at",
            "last_error",
            "checked_at",
            "detail",
        ):
            self.assertIn(key, row)
        self.assertIn("quote", payload["groups"])
        self.assertIn("bars", payload["groups"])
        self.assertIn("intraday", payload["groups"])
        self.assertIn("fund", payload["groups"])

    def test_minute_endpoint(self) -> None:
        payload_data = {
            "code": "sh510300",
            "interval": 5,
            "name": "沪深300ETF",
            "count": 1,
            "as_of": "2026-11-06 15:00",
            "source": "eastmoney",
            "warning": None,
            "bars": [
                {
                    "time": "2026-11-06 15:00",
                    "date": "2026-11-06",
                    "open": 4.1,
                    "high": 4.2,
                    "low": 4.0,
                    "close": 4.15,
                    "volume": 100,
                }
            ],
        }
        with mock.patch("etfmon.minute.get_minute", return_value=payload_data):
            status, payload = self.json_get("/api/market/minute?code=sh510300&interval=5&limit=240")
        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], "sh510300")
        self.assertEqual(payload["interval"], 5)
        self.assertEqual(payload["count"], 1)
        self.assertIsNone(payload["warning"])
        self.assertEqual(len(payload["bars"]), 1)
        status, payload = self.json_get("/api/market/minute?code=sh510300&interval=7")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")
        status, payload = self.json_get("/api/market/minute?code=sh510300&limit=0")
        self.assertEqual(status, 400)

    def test_minute_unknown_code_still_200(self) -> None:
        with mock.patch(
            "etfmon.sources.fetch_eastmoney_minute", side_effect=sources.NoDataError("无数据")
        ):
            with mock.patch(
                "etfmon.sources.fetch_sina_minute", side_effect=sources.NoDataError("无数据")
            ):
                status, payload = self.json_get("/api/market/minute?code=sh999999&interval=5")
        self.assertEqual(status, 200)
        self.assertEqual(payload["bars"], [])
        self.assertIn("暂无该标的数据", payload["warning"])

    def test_team_minute_endpoint(self) -> None:
        payload_data = {
            "interval": 5,
            "count": 1,
            "as_of": "2026-11-06 15:00",
            "source": "eastmoney",
            "warning": None,
            "method": {"unit": "亿元", "weighting": "latest shares × minute close", "codes": 26, "missing": []},
            "series": [
                {
                    "time": "2026-11-06 15:00",
                    "date": "2026-11-06",
                    "value": 10123.45,
                    "change_pct": -0.0012,
                    "volume": 123456,
                }
            ],
        }
        with mock.patch("etfmon.team_minute.get_team_minute", return_value=payload_data) as fetch:
            status, payload = self.json_get("/api/team/minute?interval=5&limit=120")
            self.assertEqual(fetch.call_args.args, (5, 120))
        self.assertEqual(status, 200)
        self.assertEqual(payload["interval"], 5)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["method"]["unit"], "亿元")
        self.assertEqual(payload["series"][0]["value"], 10123.45)
        self.assertIn("generated_at", payload)
        status, payload = self.json_get("/api/team/minute?interval=7")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")
        status, payload = self.json_get("/api/team/minute?limit=0")
        self.assertEqual(status, 400)

    def test_team_minute_empty_still_200(self) -> None:
        empty = {
            "interval": 1,
            "count": 0,
            "as_of": None,
            "source": "unknown",
            "warning": "上游暂时不可用，稍后重试",
            "method": {"unit": "亿元", "weighting": "latest shares × minute close", "codes": 26, "missing": []},
            "series": [],
        }
        with mock.patch("etfmon.team_minute.get_team_minute", return_value=empty):
            status, payload = self.json_get("/api/team/minute?interval=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["series"], [])
        self.assertTrue(payload["warning"])

    def test_sources_has_no_removed_source(self) -> None:
        status, payload = self.json_get("/api/sources")
        self.assertEqual(status, 200)
        ids = [row["id"] for row in payload["sources"]]
        self.assertNotIn("netease_bars", ids)
        self.assertEqual(len(ids), 10)

    def test_health_sources_fields(self) -> None:
        status, payload = self.json_get("/api/health")
        self.assertEqual(status, 200)
        for row in payload["sources"]:
            self.assertIn("fallback_count", row)
            self.assertIn("circuit", row)

    def test_overview_data_health_alerts(self) -> None:
        status, payload = self.json_get("/api/overview")
        self.assertEqual(status, 200)
        self.assertIn("alerts", payload["data_health"])
        self.assertIsInstance(payload["data_health"]["alerts"], list)

    def test_bars_spans(self) -> None:
        for span in ("day", "week", "month"):
            status, payload = self.json_get(f"/api/market/bars?code=sh510300&span={span}&limit=300")
            self.assertEqual(status, 200)
            self.assertEqual(payload["code"], "sh510300")
            self.assertEqual(payload["span"], span)
            self.assertEqual(payload["adjustment"], "qfq")
            self.assertTrue(payload["bars"])
            self.assertIn("ma20", payload["ma"])
            self.assertIn("change", payload["stats"])
            self.assertIn("warning", payload)
            self.assertIsNone(payload["warning"])
            self.assertEqual(set(payload["bars"][0]), {"date", "open", "high", "low", "close", "volume"})
        status, payload = self.json_get("/api/market/bars?code=sh510300&span=day&limit=5")
        self.assertEqual(len(payload["bars"]), 5)

    def test_bars_validation(self) -> None:
        status, payload = self.json_get("/api/market/bars?code=sh510300&span=year")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")
        status, payload = self.json_get("/api/market/bars?code=bad")
        self.assertEqual(status, 400)
        # 未知代码：按需拉取也失败 → 404（本地目录不认识它）
        with mock.patch("etfmon.views._fetch_bars_ondemand", side_effect=sources.SourceError("offline")):
            status, payload = self.json_get("/api/market/bars?code=sh519999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_bars_ondemand_success(self) -> None:
        """合法但本地无缓存的代码：请求路径按需拉取并落盘 200。"""
        dates = weekdays_ending(date(2026, 11, 6), 40)
        fresh = make_bars(dates, 7.0, 0.01)
        with mock.patch("etfmon.views._fetch_bars_ondemand", return_value=(fresh, "tencent")):
            status, payload = self.json_get("/api/market/bars?code=sh518880&span=day&limit=300")
        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], "sh518880")
        self.assertEqual(len(payload["bars"]), 40)
        self.assertIsNone(payload["warning"])
        self.assertTrue(store.load_bars("sh518880"))  # 已落盘

    def test_bars_ondemand_failure_returns_200_with_warning(self) -> None:
        """已知标的按需拉取失败：200 + bars:[] + warning，且 60s 内不重复打上游。"""
        with mock.patch(
            "etfmon.views._fetch_bars_ondemand", side_effect=sources.SourceError("offline")
        ) as fetch:
            status, payload = self.json_get("/api/market/bars?code=sh563300")
            self.assertEqual(status, 200)
            self.assertEqual(payload["bars"], [])
            self.assertEqual(payload["warning"], views.UPSTREAM_WARNING)
            self.assertEqual(fetch.call_count, 1)
            # 失败负缓存：第二次同一标的不会再调上游
            status, payload = self.json_get("/api/market/bars?code=sh563300")
            self.assertEqual(status, 200)
            self.assertEqual(fetch.call_count, 1)

    def test_intraday(self) -> None:
        status, payload = self.json_get("/api/market/intraday?code=sh510300&days=5")
        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], "sh510300")
        self.assertEqual(len(payload["days"]), 1)
        self.assertEqual(payload["days"][0]["date"], "2026-11-06")
        self.assertEqual(payload["days"][0]["points"][0]["time"], "09:30")
        self.assertIn("warning", payload)
        self.assertIsNone(payload["warning"])
        with mock.patch("etfmon.views.sources.fetch_intraday", side_effect=sources.SourceError("offline")):
            status, payload = self.json_get("/api/market/intraday?code=sh519999")
        self.assertEqual(status, 404)

    def test_intraday_ondemand_success(self) -> None:
        fetched = {
            "code": "sh518880",
            "name": "黄金ETF",
            "pre_close": 7.3,
            "days": [
                {
                    "date": "2026-11-06",
                    "points": [{"time": "09:30", "label": "2026-11-06 09:30", "price": 7.35, "avg": 7.35, "volume": 10.0}],
                    "change": 0.0068,
                }
            ],
        }
        with mock.patch("etfmon.views.sources.fetch_intraday", return_value=fetched) as fetch:
            status, payload = self.json_get("/api/market/intraday?code=sh518880&days=5")
        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], "sh518880")
        self.assertEqual(len(payload["days"]), 1)
        self.assertIsNone(payload["warning"])
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(store.path_for("intraday", "sh518880.json").is_file())  # 已落盘

    def test_intraday_ondemand_failure_returns_200_with_warning(self) -> None:
        with mock.patch(
            "etfmon.views.sources.fetch_intraday", side_effect=sources.SourceError("offline")
        ) as fetch:
            status, payload = self.json_get("/api/market/intraday?code=sh510880")
            self.assertEqual(status, 200)
            self.assertEqual(payload["days"], [])
            self.assertEqual(payload["warning"], views.UPSTREAM_WARNING)
            self.assertEqual(fetch.call_count, 1)
            status, payload = self.json_get("/api/market/intraday?code=sh510880")
            self.assertEqual(fetch.call_count, 1)

    def test_quotes(self) -> None:
        status, payload = self.json_get("/api/market/quotes?codes=sh510300,sh000300")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["quotes"]), 2)
        self.assertEqual(payload["quotes"][0]["code"], "sh510300")
        self.assertAlmostEqual(payload["quotes"][0]["price"], 4.124)
        # 缺失报价返回占位 Quote（字段 null），不是 500
        stub = payload["quotes"][1]
        self.assertIsNone(stub["price"])
        self.assertTrue(stub["stale"])
        status, _payload = self.json_get("/api/market/quotes?codes=bad")
        self.assertEqual(status, 400)

    def test_watchlist_flow(self) -> None:
        status, payload = self.json_get("/api/watchlist")
        self.assertEqual(status, 200)
        self.assertTrue(payload["items"])
        self.assertIn("updated_at", payload)

        status, payload = self.post("/api/watchlist", {"action": "add", "code": "sh563300", "label": "中证2000"})
        self.assertEqual(status, 403)  # 缺少 X-ETF-Client

        status, payload = self.post(
            "/api/watchlist", {"action": "add", "code": "sh563300", "label": "中证2000"}, CLIENT_HEADER
        )
        self.assertEqual(status, 200)
        codes = [item["code"] for item in payload["items"]]
        self.assertIn("sh563300", codes)

        status, payload = self.post("/api/watchlist", {"action": "add", "code": "sh563300"}, CLIENT_HEADER)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "conflict")

        # 本地未知代码：上游也查不到 → 422（用 mock 避免测试访问网络）
        with mock.patch("etfmon.api.sources.fetch_quotes", side_effect=sources.SourceError("offline")):
            status, payload = self.post("/api/watchlist", {"action": "add", "code": "sh999999"}, CLIENT_HEADER)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "unprocessable")

        # 本地未知、但腾讯能查到 → 允许加入，并带现场报价。
        # 回归：即使 names 缓存里已有名字（quote 缺失），也必须现场拉一次报价
        store.set_names({"sh518880": "黄金ETF"})
        live_quote = quote_of("sh518880", "黄金ETF", 7.35, 1.5e9)
        batch = sources.QuoteBatch(quotes={"sh518880": live_quote}, fetched_at=store.iso_now(), source="test")
        self._prefetch_mock.reset_mock()
        with mock.patch("etfmon.api.sources.fetch_quotes", return_value=batch) as fetch:
            status, payload = self.post(
                "/api/watchlist", {"action": "add", "code": "sh518880"}, CLIENT_HEADER
            )
        self.assertEqual(fetch.call_count, 1)  # names 存在也要现场拉价
        self.assertEqual(status, 200)
        added = next(item for item in payload["items"] if item["code"] == "sh518880")
        self.assertAlmostEqual(added["quote"]["price"], 7.35)
        self.assertEqual(added["kind"], "etf")
        self.assertEqual(added["label"], "黄金ETF")
        self._prefetch_mock.assert_called_once_with("sh518880")
        # add 返回后立即 GET 自选也应有价格（quote 已合并进缓存）
        status, listing = self.json_get("/api/watchlist")
        item = next(entry for entry in listing["items"] if entry["code"] == "sh518880")
        self.assertAlmostEqual(item["quote"]["price"], 7.35)
        status, payload = self.post("/api/watchlist", {"action": "remove", "code": "sh518880"}, CLIENT_HEADER)
        self.assertEqual(status, 200)

        status, payload = self.post("/api/watchlist", {"action": "remove", "code": "sh560050"}, CLIENT_HEADER)
        self.assertEqual(status, 404)

        status, payload = self.post("/api/watchlist", {"action": "remove", "code": "sh563300"}, CLIENT_HEADER)
        self.assertEqual(status, 200)
        self.assertNotIn("sh563300", [item["code"] for item in payload["items"]])

        status, payload = self.post("/api/watchlist", {"action": "update", "code": "sh510300", "label": "沪深300"}, CLIENT_HEADER)
        self.assertEqual(status, 200)

        status, payload = self.post("/api/watchlist", {"action": "explode", "code": "sh510300"}, CLIENT_HEADER)
        self.assertEqual(status, 400)

        status, payload = self.post("/api/watchlist", b"not-json", CLIENT_HEADER)
        self.assertEqual(status, 400)

    def test_watchlist_limit(self) -> None:
        items = [{"code": code, "kind": "etf", "label": None} for code in list(store_codes())[: 12]]
        store.save_watchlist({"updated_at": store.iso_now(), "items": items})
        try:
            status, payload = self.post(
                "/api/watchlist", {"action": "add", "code": "sh510880", "label": "红利"}, CLIENT_HEADER
            )
            self.assertIn(status, (409, 422))
            if status == 409:
                self.assertEqual(payload["error"]["code"], "conflict")
        finally:
            store.save_watchlist({"updated_at": None, "items": list_default_watchlist()})

    def test_search(self) -> None:
        with mock.patch("etfmon.views.sources.fetch_tencent_search", side_effect=sources.SourceError("offline")):
            status, payload = self.json_get("/api/search?q=" + urllib.parse.quote("半导体"))
        self.assertEqual(status, 200)
        codes = [item["code"] for item in payload["results"]]
        self.assertIn("sh512480", codes)
        self.assertEqual(set(payload["results"][0]), {"code", "name", "kind", "market"})
        self.assertIn("warning", payload)
        self.assertIsNotNone(payload["warning"])
        upstream = [{"code": "sh518880", "name": "黄金ETF", "kind": "etf", "market": "sh"}]
        with mock.patch("etfmon.views.sources.fetch_tencent_search", return_value=upstream):
            status, payload = self.json_get("/api/search?q=" + urllib.parse.quote("黄金"))
        self.assertEqual(status, 200)
        self.assertEqual([item["code"] for item in payload["results"]], ["sh518880"])
        self.assertIsNone(payload["warning"])
        status, payload = self.json_get("/api/search")
        self.assertEqual(status, 400)

    def test_meta(self) -> None:
        status, payload = self.json_get("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(payload["product"], "ETF 观察台")
        self.assertIn("net_flow", payload["definitions"])
        self.assertTrue(payload["entities"])
        self.assertTrue(payload["data_sources"])
        self.assertTrue(payload["limitations"])

    def test_404_and_405(self) -> None:
        status, payload = self.json_get("/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        status, _headers, _body = self.get("/api/nope")
        self.assertEqual(status, 404)
        request = urllib.request.Request(self.url("/api/overview"), method="DELETE")
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(context.exception.code, 405)

    def test_headers_and_gzip(self) -> None:
        status, headers, body = self.get("/api/team", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn("application/json", headers.get("Content-Type", ""))
        if headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("rows", payload)


    def test_replay_model_selection_preserves_versions(self) -> None:
        for model,version in [('current','self-early-turn-v4/protected-bite-v2'),('expanded','self-cross-window-v5/protected-bite-v2'),('legacy','self-trend-two-paths-v3/close-exit-v1')]:
            status,payload=self.json_get('/api/research/replay?code=sh512480&model='+model)
            self.assertEqual(status,200);self.assertEqual(payload['model'],model)
            self.assertEqual(payload['rule_version'],version);self.assertFalse(payload['external_reference_required'])

    def test_unknown_replay_model_is_rejected(self) -> None:
        status,_=self.json_get('/api/research/replay?code=sh512480&model=unknown')
        self.assertEqual(status,400)

    def test_action_desk_is_read_only_and_validates_model(self) -> None:
        from etfmon import actions
        with mock.patch.object(actions,'process',side_effect=AssertionError('GET must not write')):
            status,payload=self.json_get('/api/research/actions?model=expanded')
            self.assertEqual(status,200);self.assertEqual(payload['position_basis'],'simulated_only')
            self.assertEqual(payload['rows'],[])
        status,_=self.json_get('/api/research/actions?model=current&protection=trailing7')
        self.assertEqual(status,400)

    def test_position_drawdown_protection_contract(self) -> None:
        status,payload=self.json_get('/api/research/replay?code=sh512480&model=expanded&protection=trailing7')
        self.assertEqual(status,200);self.assertEqual(payload['protection'],'trailing7')
        self.assertTrue(payload['rule_version'].endswith('/prior-high-7pct-v1'))
        status,payload=self.json_get('/api/research?model=expanded&protection=trailing7')
        self.assertEqual(status,200);self.assertEqual(payload['protection'],'trailing7');self.assertTrue(payload['rule_version'].endswith('-drawdown7'))
        status,_=self.json_get('/api/research/replay?code=sh512480&model=legacy&protection=trailing7');self.assertEqual(status,400)


def analytics_states() -> set[str]:
    from etfmon import analytics

    return set(analytics.STATE_ORDER)


def store_codes() -> list[str]:
    from etfmon import config

    return list(config.TEAM_CODES)


def list_default_watchlist() -> list[dict]:
    from etfmon import config

    return [dict(item) for item in config.watchlist_default()]


if __name__ == "__main__":
    unittest.main()

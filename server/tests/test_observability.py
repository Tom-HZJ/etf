"""可观测性单测：回退顺序、熔断、计数、告警冷却、webhook、持久化。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from typing import Any
from unittest import mock

from etfmon import config, observability, sources, store, views


class RegistryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        observability.registry.reset()
        sources._quote_no_data.clear()

    def tearDown(self) -> None:
        observability.registry.reset()
        sources._quote_no_data.clear()
        self._env.stop()
        self._tmp.cleanup()


class NoDataHealthTest(RegistryTestBase):
    """P1-V2-2：标的无数据不能计入源失败/熔断。"""

    def _snapshot_metrics(self, source_ids: tuple[str, ...]) -> dict[str, tuple[int, int, str]]:
        result = {}
        for source_id in source_ids:
            metric = observability.registry.metric(source_id)
            assert metric is not None
            result[source_id] = (metric.failure_count, metric.consecutive_failures, metric.circuit)
        return result

    def test_unknown_minute_codes_do_not_poison_sources(self) -> None:
        minute_ids = ("eastmoney_minute", "sina_minute")
        before = self._snapshot_metrics(minute_ids)
        for index in range(5):
            code = f"sh99999{index}"
            with self.assertRaises(sources.NoDataError):
                with mock.patch(
                    "etfmon.sources.fetch_eastmoney_minute", side_effect=sources.NoDataError("无数据")
                ):
                    with mock.patch(
                        "etfmon.sources.fetch_sina_minute", side_effect=sources.NoDataError("无数据")
                    ):
                        sources.fetch_minute(code, 5, 10)
        after = self._snapshot_metrics(minute_ids)
        self.assertEqual(before, after)
        for source_id in minute_ids:
            self.assertEqual(after[source_id], (0, 0, "closed"))
        self.assertFalse(any(alert["level"] == "critical" for alert in observability.registry.alerts()))
        # 正常代码随后仍可成功
        bars = [{"time": "2026-11-06 15:00"}]
        with mock.patch("etfmon.sources.fetch_eastmoney_minute", return_value={"bars": bars}):
            result, source = sources.fetch_minute("sh510300", 5, 10)
        self.assertEqual(source, "eastmoney")
        self.assertEqual(result, bars)
        metric = observability.registry.metric("eastmoney_minute")
        assert metric is not None
        self.assertEqual(metric.success_count, 1)
        self.assertEqual(metric.failure_count, 0)

    def test_unknown_quote_codes_do_not_poison_sources(self) -> None:
        quote_ids = ("tencent_quote", "sina_quote", "eastmoney_quote")
        before = self._snapshot_metrics(quote_ids)
        with mock.patch("etfmon.sources.fetch_tencent_quotes", return_value={}):
            with mock.patch("etfmon.sources.fetch_sina_quotes", return_value={}):
                with mock.patch("etfmon.sources.fetch_eastmoney_quotes", return_value={}):
                    with self.assertRaises(sources.NoDataError):
                        sources.fetch_quotes(["sh999991", "sh999992"])
        after = self._snapshot_metrics(quote_ids)
        self.assertEqual(before, after)
        self.assertFalse(any(alert["level"] == "critical" for alert in observability.registry.alerts()))

    def test_unknown_bars_do_not_poison_sources(self) -> None:
        with mock.patch("etfmon.sources.fetch_tencent_bars", side_effect=sources.NoDataError("无")):
            with mock.patch("etfmon.sources.fetch_eastmoney_bars", side_effect=sources.NoDataError("无")):
                with mock.patch("etfmon.sources.fetch_sina_bars", side_effect=sources.NoDataError("无")):
                    with self.assertRaises(sources.NoDataError):
                        sources.fetch_bars("sh999999")
        for source_id in ("tencent_bars", "eastmoney_bars", "sina_bars"):
            metric = observability.registry.metric(source_id)
            assert metric is not None
            self.assertEqual(metric.failure_count, 0)
            self.assertEqual(metric.circuit, "closed")


class DisabledAlertCooldownTest(RegistryTestBase):
    def test_disabled_alert_dedup_one_hour(self) -> None:
        observability.registry.set_disabled("sina_bars", "探测失败：HTTP 502")
        observability.registry.set_disabled("sina_bars", "探测失败：HTTP 502")
        alerts = observability.registry.alerts()
        self.assertEqual(len(alerts), 1)
        # 恢复后再次故障可以立刻提醒
        observability.registry.enable("sina_bars")
        observability.registry.set_disabled("sina_bars", "探测失败：HTTP 502")
        self.assertEqual(len(observability.registry.alerts()), 2)


class PublicDetailTest(RegistryTestBase):
    def test_health_detail_redacts_url(self) -> None:
        url = "https://quotes.money.163.com/service/chddata.html?code=0510300&start=20250101"
        observability.registry.set_disabled("sina_bars", f"请求失败 {url}: HTTP Error 502")
        entries = {row["id"]: row for row in observability.registry.health_entries()}
        self.assertNotIn("https://", entries["sina_bars"]["detail"])
        self.assertIn("上游接口", entries["sina_bars"]["detail"])
        # /api/sources 保留完整错误
        rows = {row["id"]: row for row in observability.registry.snapshot()}
        self.assertIn("https://", rows["sina_bars"]["detail"])


class ViewsHealthTest(RegistryTestBase):
    def _app(self) -> Any:
        from etfmon.collectors import QuoteCache

        return views.App(cache=QuoteCache(), started_at=store.now())

    def _patch_core(self) -> Any:
        return mock.patch.multiple(
            "etfmon.views.store",
            load_daily=mock.Mock(return_value={"days": {"2026-11-06": {}}}),
            load_hotspots_latest=mock.Mock(return_value={"rows": [{"code": "sh510300"}]}),
        )

    def _record_all_primaries(self) -> None:
        for source_id in ("tencent_quote", "tencent_bars", "tencent_intraday", "eastmoney_fund"):
            observability.registry.record_success(source_id, 10)

    def test_optional_source_down_keeps_ok(self) -> None:
        self._record_all_primaries()
        observability.registry.record_failure("sina_bars", "HTTP 502")
        observability.registry.set_disabled("sina_bars", "探测失败：HTTP 502")
        quotes = {"sh510300": {"stale": False}}
        with mock.patch("etfmon.views._cached_quotes", return_value=(quotes, store.now(), False)):
            with self._patch_core():
                health = views.build_health(self._app())
                data_health = views._data_health(self._app(), quotes)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(data_health["status"], "ok")
        self.assertTrue(data_health["alerts"])  # 可选源告警仍保留展示
        self.assertTrue(any("不可用" in warning for warning in data_health["warnings"]))

    def test_primary_source_down_degrades(self) -> None:
        self._record_all_primaries()
        # 行情类所有源都失败才算健康降级（主源被 WAF 拦截但有兜底时仍可用）。
        for source_id in ("tencent_quote", "sina_quote", "eastmoney_quote"):
            for _ in range(config.CIRCUIT_FAILURE_THRESHOLD):
                observability.registry.record_failure(source_id, "network down")
        quotes = {"sh510300": {"stale": False}}
        with mock.patch("etfmon.views._cached_quotes", return_value=(quotes, store.now(), False)):
            with self._patch_core():
                health = views.build_health(self._app())
                data_health = views._data_health(self._app(), quotes)
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(data_health["status"], "degraded")

    def test_primary_down_with_fallback_keeps_ok(self) -> None:
        self._record_all_primaries()
        observability.registry.record_success("eastmoney_bars", 10, fallback=True)
        for _ in range(config.CIRCUIT_FAILURE_THRESHOLD):
            observability.registry.record_failure("tencent_bars", "HTTP 501")
        quotes = {"sh510300": {"stale": False}}
        with mock.patch("etfmon.views._cached_quotes", return_value=(quotes, store.now(), False)):
            with self._patch_core():
                health = views.build_health(self._app())
                data_health = views._data_health(self._app(), quotes)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(data_health["status"], "ok")


class FallbackOrderTest(RegistryTestBase):
    def test_bars_fallback_counts(self) -> None:
        bars = [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        with mock.patch("etfmon.sources.fetch_tencent_bars", side_effect=sources.SourceError("tencent down")):
            with mock.patch("etfmon.sources.fetch_eastmoney_bars", return_value=bars):
                result, source = sources.fetch_bars("sh510300")
        self.assertEqual(source, "eastmoney")
        self.assertEqual(result, bars)
        tencent = observability.registry.metric("tencent_bars")
        em = observability.registry.metric("eastmoney_bars")
        assert tencent is not None and em is not None
        self.assertEqual(tencent.failure_count, 1)
        self.assertEqual(tencent.consecutive_failures, 1)
        self.assertEqual(em.success_count, 1)
        self.assertEqual(em.fallback_count, 1)

    def test_bars_all_fail_raises(self) -> None:
        with mock.patch("etfmon.sources.fetch_tencent_bars", side_effect=sources.SourceError("a")):
            with mock.patch("etfmon.sources.fetch_eastmoney_bars", side_effect=sources.SourceError("b")):
                with mock.patch("etfmon.sources.fetch_sina_bars", side_effect=sources.SourceError("c")):
                    with self.assertRaises(sources.SourceError):
                        sources.fetch_bars("sh510300")
        for source_id in ("tencent_bars", "eastmoney_bars", "sina_bars"):
            metric = observability.registry.metric(source_id)
            assert metric is not None
            self.assertEqual(metric.failure_count, 1)

    def test_quote_fallback_sina_then_eastmoney_shares(self) -> None:
        sina_quote = {
            "code": "sh510300",
            "name": "沪深300ETF",
            "kind": "etf",
            "price": 4.124,
            "shares": None,
            "source": "sina",
        }
        em_quote = {"code": "sh510300", "shares": 100.0, "market_cap": 412.4, "source": "eastmoney"}
        with mock.patch("etfmon.sources.fetch_tencent_quotes", side_effect=sources.SourceError("tencent down")):
            with mock.patch("etfmon.sources.fetch_sina_quotes", return_value={"sh510300": sina_quote}):
                with mock.patch("etfmon.sources.fetch_eastmoney_quotes", return_value={"sh510300": em_quote}) as em:
                    batch = sources.fetch_quotes(["sh510300"])
        self.assertEqual(batch.source, "sina+eastmoney" if em.called else "sina")
        self.assertEqual(batch.quotes["sh510300"]["price"], 4.124)
        self.assertEqual(batch.quotes["sh510300"]["shares"], 100.0)  # 东财补齐份额
        sina = observability.registry.metric("sina_quote")
        em_metric = observability.registry.metric("eastmoney_quote")
        assert sina is not None and em_metric is not None
        self.assertEqual(sina.success_count, 1)
        self.assertEqual(sina.fallback_count, 1)
        self.assertEqual(em_metric.fallback_count, 1)


class CircuitTest(RegistryTestBase):
    def test_open_after_threshold_and_half_open_probe(self) -> None:
        registry = observability.registry
        for _ in range(config.CIRCUIT_FAILURE_THRESHOLD):
            self.assertTrue(registry.allow("tencent_quote"))
            registry.record_failure("tencent_quote", "boom")
        self.assertFalse(registry.allow("tencent_quote"))  # open
        metric = registry.metric("tencent_quote")
        assert metric is not None
        self.assertEqual(metric.circuit, "open")
        # 快进过熔断窗口 → half_open 放一个探针
        with mock.patch("etfmon.observability.time.time", return_value=time.time() + config.CIRCUIT_OPEN_S + 1):
            self.assertTrue(registry.allow("tencent_quote"))
            self.assertFalse(registry.allow("tencent_quote"))  # 探针在途
            metric.circuit = "half_open"
            registry.record_failure("tencent_quote", "probe failed")
            self.assertEqual(metric.circuit, "open")
        # 成功恢复
        registry.record_success("tencent_quote", 10)
        self.assertTrue(registry.allow("tencent_quote"))
        self.assertEqual(metric.circuit, "closed")
        self.assertEqual(metric.consecutive_failures, 0)

    def test_disabled_source_skipped(self) -> None:
        registry = observability.registry
        registry.set_disabled("sina_bars", "探测失败：HTTP 502")
        self.assertFalse(registry.allow("sina_bars"))
        rows = {row["id"]: row for row in registry.snapshot()}
        self.assertEqual(rows["sina_bars"]["status"], "down")
        self.assertIn("HTTP 502", rows["sina_bars"]["detail"])
        registry.enable("sina_bars")
        self.assertTrue(registry.allow("sina_bars"))


class AlertTest(RegistryTestBase):
    def test_alert_cooldown(self) -> None:
        registry = observability.registry
        first = registry.add_alert("warn", "同样的告警", "tencent_quote")
        self.assertIsNotNone(first)
        second = registry.add_alert("warn", "同样的告警", "tencent_quote")
        self.assertIsNone(second)  # 10 分钟内去重
        other = registry.add_alert("critical", "另一条告警", "tencent_bars")
        self.assertIsNotNone(other)
        alerts = registry.alerts()
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["id"], other["id"])

    def test_alert_kept_and_persisted(self) -> None:
        observability.registry.add_alert("warn", "持久化测试", "tencent_quote")
        saved = store.load_state().get("source_health") or {}
        self.assertTrue(saved.get("alerts"))

    def test_webhook_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def read(self, _n: int) -> bytes:
                return b"ok"

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        def fake_urlopen(request, timeout=0):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = observability._post_webhook(
                "https://example.test/hook",
                {"message": "磁盘告警", "source_id": "tencent_quote", "level": "warn", "at": "2026-11-08T12:00:00+08:00"},
            )
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "https://example.test/hook")
        self.assertEqual(captured["body"]["text"], "磁盘告警")
        self.assertEqual(captured["body"]["source"], "tencent_quote")
        self.assertEqual(captured["timeout"], 5.0)

    def test_webhook_failure_only_logs(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertFalse(
                observability._post_webhook("https://example.test/hook", {"message": "x", "level": "warn"})
            )


class PersistenceTest(RegistryTestBase):
    def test_reload_metrics(self) -> None:
        observability.registry.record_success("tencent_quote", 42)
        observability.registry.record_failure("sina_quote", "boom")
        observability.registry.set_disabled("sina_bars", "HTTP 502")
        fresh = observability.SourceRegistry()
        metrics = fresh.snapshot()
        rows = {row["id"]: row for row in metrics}
        self.assertEqual(rows["tencent_quote"]["success_count"], 1)
        self.assertEqual(rows["tencent_quote"]["latency_ms"], 42)
        self.assertEqual(rows["sina_quote"]["failure_count"], 1)
        self.assertTrue(rows["sina_bars"]["status"] == "down")

    def test_health_entries_include_fallback_and_circuit(self) -> None:
        observability.registry.record_success("tencent_quote", 12, fallback=True)
        entries = {row["id"]: row for row in observability.registry.health_entries()}
        self.assertEqual(entries["tencent_quote"]["fallback_count"], 1)
        self.assertEqual(entries["tencent_quote"]["circuit"], "closed")


class StalenessTest(RegistryTestBase):
    def test_stale_alert_only_during_trading(self) -> None:
        from datetime import datetime

        friday = datetime(2026, 11, 6, 10, 0, tzinfo=config.SHANGHAI_TZ)
        store.update_state(
            lambda state: {
                **state,
                "last_quotes_at": (friday.replace(hour=9, minute=30)).isoformat(timespec="seconds"),
                "last_intraday_sample": (friday.replace(hour=9, minute=30)).isoformat(timespec="seconds"),
            }
        )
        alerts = observability.check_data_staleness(now=friday.replace(hour=10, minute=0))
        self.assertTrue(alerts)
        self.assertIn("未更新", alerts[0]["message"])
        # 非交易时段不告警
        observability.registry.reset()
        saturday = datetime(2026, 11, 7, 10, 0, tzinfo=config.SHANGHAI_TZ)
        self.assertEqual(observability.check_data_staleness(now=saturday), [])


if __name__ == "__main__":
    unittest.main()

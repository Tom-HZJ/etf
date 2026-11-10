"""上游解析单测：用构造的腾讯/东财响应验证字段映射，不访问网络。"""

from __future__ import annotations

import unittest

from etfmon import sources


def tencent_body(fields: dict[int, str], length: int = 88) -> str:
    parts = [""] * length
    for index, value in fields.items():
        parts[index] = value
    return "~".join(parts)


class TencentQuoteParseTest(unittest.TestCase):
    def setUp(self) -> None:
        fields = {
            1: "沪深300ETF华泰柏瑞",
            2: "510300",
            3: "4.124",
            4: "4.195",
            5: "4.113",
            6: "12629370",
            30: "20261106161452",
            31: "-0.071",
            32: "-1.69",
            33: "4.169",
            34: "4.095",
            37: "521167",
            38: "2.71",
            45: "1923.45",
            72: "46640287700",
        }
        self.text = f'v_sh510300="{tencent_body(fields)}";'

    def test_fields(self) -> None:
        quotes = sources.parse_tencent_quotes(self.text)
        quote = quotes["sh510300"]
        self.assertEqual(quote["name"], "沪深300ETF华泰柏瑞")
        self.assertEqual(quote["kind"], "etf")
        self.assertAlmostEqual(quote["price"], 4.124)
        self.assertAlmostEqual(quote["pre_close"], 4.195)
        self.assertAlmostEqual(quote["change"], -0.071)
        self.assertAlmostEqual(quote["change_pct"], -0.0169)
        self.assertAlmostEqual(quote["volume"], 12629370.0)  # 手
        self.assertAlmostEqual(quote["amount"], 5211670000.0)  # 万元 → 元
        self.assertAlmostEqual(quote["turnover"], 0.0271)  # 换手率小数
        self.assertAlmostEqual(quote["shares"], 46640287700.0)
        self.assertAlmostEqual(quote["market_cap"], 4.124 * 46640287700.0, places=0)
        self.assertEqual(quote["quote_time"], "2026-11-06T16:14:52+08:00")
        self.assertEqual(quote["source"], "tencent")
        self.assertFalse(quote["stale"])

    def test_index_has_no_shares(self) -> None:
        fields = {
            1: "沪深300",
            2: "000300",
            3: "4054.28",
            4: "4113.41",
            30: "20261106161413",
            32: "-1.44",
            37: "47884040",
            38: "0",
            45: "510902.95",
            72: "3359660625033",
        }
        text = f'v_sh000300="{tencent_body(fields)}";'
        quote = sources.parse_tencent_quotes(text)["sh000300"]
        self.assertEqual(quote["kind"], "stock")
        self.assertIsNone(quote["shares"])
        self.assertIsNone(quote["market_cap"])
        self.assertIsNone(quote["turnover"])

    def test_short_row_ignored(self) -> None:
        fields = {1: "短", 3: "1.0"}
        text = f'v_sh510300="{tencent_body(fields, length=20)}";'
        self.assertEqual(sources.parse_tencent_quotes(text), {})

    def test_missing_change_derived_from_prices(self) -> None:
        fields = {1: "A", 2: "510300", 3: "4.2", 4: "4.0", 30: "20261106150000", 72: "100"}
        text = f'v_sh510300="{tencent_body(fields)}";'
        quote = sources.parse_tencent_quotes(text)["sh510300"]
        self.assertAlmostEqual(quote["change"], 0.2)
        self.assertAlmostEqual(quote["change_pct"], 0.05)


class TencentBarsParseTest(unittest.TestCase):
    def test_qfqday(self) -> None:
        payload = {
            "data": {
                "sh510300": {
                    "qfqday": [
                        ["2026-11-05", "4.10", "4.20", "4.25", "4.05", "1000.000"],
                        ["2026-11-06", "4.20", "4.30", "4.35", "4.15", "2000.000"],
                    ]
                }
            }
        }
        bars = sources.parse_tencent_bars(payload, "sh510300")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["date"], "2026-11-05")
        self.assertAlmostEqual(bars[-1]["open"], 4.20)
        self.assertAlmostEqual(bars[-1]["high"], 4.35)
        self.assertAlmostEqual(bars[-1]["low"], 4.15)
        self.assertAlmostEqual(bars[-1]["close"], 4.30)
        self.assertAlmostEqual(bars[-1]["volume"], 2000.0)

    def test_index_uses_day_key(self) -> None:
        payload = {"data": {"sh000300": {"day": [["2026-11-06", "4000", "4050", "4060", "3990", "123"]]}}}
        bars = sources.parse_tencent_bars(payload, "sh000300")
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]["close"], 4050.0)

    def test_empty(self) -> None:
        self.assertEqual(sources.parse_tencent_bars({"data": {}}, "sh510300"), [])


class IntradayParseTest(unittest.TestCase):
    def test_points_and_avg(self) -> None:
        payload = {
            "data": {
                "sh510300": {
                    "data": [
                        {
                            "date": "20261106",
                            "data": [
                                "0930 4.10 100 410000",
                                "0931 4.20 300 1230000",
                                "1200 4.30 400 1600000",  # 午休，应被过滤
                                "1500 4.40 500 2150000",
                            ],
                        }
                    ],
                    "qt": {"sh510300": ["1", "沪深300ETF", "510300", "4.124", "4.195"]},
                }
            }
        }
        result = sources.parse_tencent_intraday(payload, "sh510300")
        self.assertEqual(result["name"], "沪深300ETF")
        self.assertAlmostEqual(result["pre_close"], 4.195)
        self.assertEqual(len(result["days"]), 1)
        day = result["days"][0]
        self.assertEqual(day["date"], "2026-11-06")
        self.assertEqual([point["time"] for point in day["points"]], ["09:30", "09:31", "15:00"])
        first, second, last = day["points"]
        self.assertAlmostEqual(first["volume"], 100.0)
        self.assertAlmostEqual(second["volume"], 200.0)  # 300-100，按分钟增量
        self.assertAlmostEqual(last["volume"], 200.0)  # 500-300
        self.assertAlmostEqual(second["avg"], 1230000 / (300 * 100))
        self.assertAlmostEqual(day["change"], 4.40 / 4.195 - 1)


class EastmoneyQuoteParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        payload = {
            "data": {
                "diff": [
                    {"f12": "510300", "f13": 1, "f14": "沪深300ETF", "f2": 4.124, "f3": -1.69, "f5": 12629370, "f6": 5211672667.0, "f20": 191732132788.0},
                    {"f12": "159915", "f13": 0, "f14": "创业板ETF", "f2": 2.48, "f3": -4.02, "f5": 100, "f6": 200.0, "f20": 111387848499.0, "f18": 2.584},
                ]
            }
        }
        quotes = sources.parse_eastmoney_quotes(payload)
        self.assertEqual(set(quotes), {"sh510300", "sz159915"})
        quote = quotes["sh510300"]
        self.assertAlmostEqual(quote["change_pct"], -0.0169)
        self.assertAlmostEqual(quote["amount"], 5211672667.0)
        self.assertAlmostEqual(quote["shares"], 191732132788.0 / 4.124)
        self.assertEqual(quote["source"], "eastmoney")
        self.assertAlmostEqual(quotes["sz159915"]["change"], 2.48 - 2.584)


class EastmoneyBarsParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        payload = {"data": {"klines": ["2026-11-06,4.1,4.2,4.3,4.0,12345", "bad,row"]}}
        bars = sources.parse_eastmoney_bars(payload)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]["close"], 4.2)
        self.assertAlmostEqual(bars[0]["volume"], 12345.0)


class TencentSearchParseTest(unittest.TestCase):
    def test_parse_with_unicode_escape(self) -> None:
        text = (
            'v_hint="sh~518880~\\u9ec4\\u91d1ETF\\u534e\\u5b89~hjetfha~ETF'
            "^sz~159934~\\u9ec4\\u91d1ETF\\u6613\\u65b9\\u8fbe~hjetfyfd~ETF"
            "^of~f_000005~\\u57fa\\u91d1~jj~基金^sz~399001~\\u6df1\\u8bc1\\u6210\\u6307~szcz~指数\""
        )
        results = sources.parse_tencent_search(text)
        codes = [item["code"] for item in results]
        self.assertEqual(codes, ["sh518880", "sz159934", "sz399001"])
        self.assertEqual(results[0]["name"], "黄金ETF华安")
        self.assertEqual(results[0]["kind"], "etf")
        self.assertEqual(results[0]["market"], "sh")
        self.assertEqual(results[2]["kind"], "stock")

    def test_empty_and_malformed(self) -> None:
        self.assertEqual(sources.parse_tencent_search("v_hint=\"N\""), [])
        self.assertEqual(sources.parse_tencent_search("no hint"), [])
        self.assertEqual(sources.parse_tencent_search('v_hint="bad~1~x~y~z"'), [])

    def test_dedupe_and_limit(self) -> None:
        chunk = "sh~518880~A~a~ETF"
        extra = [f"sh~{518880 + index:06d}~B{index}~b~ETF" for index in range(1, 31)]
        text = 'v_hint="' + "^".join([chunk, chunk, *extra]) + '"'
        results = sources.parse_tencent_search(text, limit=20)
        self.assertEqual(len(results), 20)
        self.assertEqual(len({item["code"] for item in results}), 20)


class FundHistoryParseTest(unittest.TestCase):
    TEXT = (
        'var fS_name = "测试基金";'
        'var Data_buySedemption = {"categories":["2026-06-30","2026-09-30"],"series":['
        '{"name":"期间申购","data":[10.0,20.0]},'
        '{"name":"期间赎回","data":[4.0,5.0]},'
        '{"name":"总份额","data":[106.0,121.0]}]};'
        'var Data_holderStructure = {"categories":["2025-12-31"],"series":['
        '{"name":"机构持有比例","data":[80.0]},{"name":"个人持有比例","data":[20.0]}]};'
    )

    def test_parse(self) -> None:
        result = sources.parse_fund_history(self.TEXT)
        self.assertEqual(result["name"], "测试基金")
        self.assertEqual(len(result["periods"]), 2)
        first = result["periods"][0]
        self.assertAlmostEqual(first["net"], 6.0)
        self.assertAlmostEqual(first["start_shares"], 100.0)  # 106 - 6
        self.assertAlmostEqual(first["total_shares"], 106.0)
        self.assertAlmostEqual(first["net_pct"], 0.06)
        self.assertFalse(first["large_redemption"])
        self.assertEqual(result["holder_history"], [{"date": "2025-12-31", "institution": 80.0, "individual": 20.0}])
        self.assertIn("报告期", result["frequency"])

    def test_missing_buysedemption_raises(self) -> None:
        with self.assertRaises(sources.SourceError):
            sources.parse_fund_history("var fS_name='x';")


if __name__ == "__main__":
    unittest.main()

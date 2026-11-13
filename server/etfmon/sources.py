"""上游数据源：腾讯行情（报价/日线/分时）与东方财富（报价兜底、k 线兜底、基金档案）。

只负责「取数与解析」，不做缓存与业务判断；异常统一抛 :class:`SourceError`，
调用方（采集线程 / probe）负责记录健康度与告警。

单位约定（与 docs/API.md 一致）：
- 金额：元（腾讯字段 37 是万元，这里已 ×10000）
- 份额：份（腾讯字段 72）
- 成交量：手（腾讯原始单位，``volume`` 与 ``bar.volume`` 都保留为手，便于与上游对数）
- 涨跌幅/换手率：小数（``-0.0169`` = -1.69%）
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import config, observability, store


class SourceError(RuntimeError):
    """上游不可用或返回无法解析的数据。"""


class NoDataError(SourceError):
    """上游可达，但该标的没有数据（例如不存在的代码）。

    与「源故障」区分：不计失败、不触发熔断，只影响该次请求。
    """


@dataclass(slots=True)
class QuoteBatch:
    """一次批量报价的结果与来源信息。"""

    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_at: str = ""
    source: str = "tencent"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- HTTP


def _http_get(
    url: str,
    *,
    referer: str,
    timeout: int = config.HTTP_TIMEOUT_S,
    encoding: str = "utf-8",
    retries: int = config.HTTP_RETRIES,
) -> str:
    """带重试的 GET，返回解码后的文本。"""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": config.USER_AGENT,
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode(encoding, errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
    raise SourceError(f"请求失败 {url}: {last_error}") from last_error


def _to_float(value: Any) -> float | None:
    """宽松转 float：空串、``-``、inf/nan 都返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text or text in {"-", "--", "null", "None"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def _quote_kind(code: str) -> tuple[str, bool]:
    """返回 (kind, is_index)。指数归到 stock，但没有总份额/换手率。"""
    digits = code[2:] if code[:2] in {"sh", "sz", "bj"} else code
    is_index = digits.startswith(("000", "399"))
    return "stock" if is_index else config.kind_of(code), is_index


def _iso_from_stamp(stamp: str) -> str | None:
    """腾讯时间戳 ``20261106161452`` → ISO（Asia/Shanghai）。"""
    text = (stamp or "").strip()
    if len(text) < 14 or not text[:14].isdigit():
        return None
    try:
        moment = datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=config.SHANGHAI_TZ)
    except ValueError:
        return None
    return moment.isoformat(timespec="seconds")


# ---------------------------------------------------------------- 腾讯报价


_TENCENT_ROW = re.compile(r'v_([a-z]{2}\d+)="([^"]*)"')


def parse_tencent_quotes(text: str) -> dict[str, dict[str, Any]]:
    """解析腾讯批量报价文本（GBK 已解码）。字段含义见 docs/API.md。"""
    quotes: dict[str, dict[str, Any]] = {}
    for match in _TENCENT_ROW.finditer(text):
        code, body = match.group(1), match.group(2)
        parts = body.split("~")
        if len(parts) < 73:
            continue
        price = _to_float(parts[3])
        pre_close = _to_float(parts[4])
        open_price = _to_float(parts[5])
        volume = _to_float(parts[6])
        high = _to_float(parts[33])
        low = _to_float(parts[34])
        amount_wan = _to_float(parts[37])
        turnover_pct = _to_float(parts[38])
        market_cap_yi = _to_float(parts[45])
        shares = _to_float(parts[72])
        kind, is_index = _quote_kind(code)
        change = _to_float(parts[31])
        change_pct = _to_float(parts[32])
        if change is None and price is not None and pre_close is not None:
            change = price - pre_close
        if change_pct is not None:
            change_pct /= 100.0
        elif change is not None and pre_close:
            change_pct = change / pre_close
        market_cap: float | None = None
        if not is_index:
            if price is not None and shares:
                market_cap = price * shares
            elif market_cap_yi is not None:
                market_cap = market_cap_yi * 1e8
        quotes[code] = {
            "code": code,
            "name": parts[1].strip(),
            "kind": kind,
            "price": price,
            "pre_close": pre_close,
            "open": open_price,
            "high": high,
            "low": low,
            "change": change,
            "change_pct": change_pct,
            "volume": volume,
            "amount": amount_wan * 1e4 if amount_wan is not None else None,
            "turnover": turnover_pct / 100.0 if turnover_pct is not None and not is_index else None,
            "shares": None if is_index else shares,
            "market_cap": market_cap,
            "quote_time": _iso_from_stamp(parts[30]),
            "source": "tencent",
            "stale": False,
        }
    return quotes


def fetch_tencent_quotes(
    codes: Iterable[str],
    *,
    timeout: int = config.HTTP_TIMEOUT_S,
    retries: int = config.HTTP_RETRIES,
) -> dict[str, dict[str, Any]]:
    """腾讯批量报价（自动分批）。"""
    unique = list(dict.fromkeys(codes))
    if not unique:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique), config.QUOTE_BATCH_SIZE):
        batch = unique[start : start + config.QUOTE_BATCH_SIZE]
        url = config.TENCENT_QUOTE_URL + ",".join(batch)
        text = _http_get(url, referer=config.TENCENT_REFERER, encoding="gbk", timeout=timeout, retries=retries)
        result.update(parse_tencent_quotes(text))
    return result


# ---------------------------------------------------------------- 东方财富报价兜底


def _em_secid(code: str) -> str:
    digits = code[2:] if code[:2] in {"sh", "sz", "bj"} else code
    market = "1" if code.startswith("sh") or (not code.startswith("sz") and digits.startswith(("5", "6", "9"))) else "0"
    return f"{market}.{digits}"


def parse_eastmoney_quotes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """解析东方财富 ulist 报价（字段含义见 ``push2.eastmoney.com``）。"""
    data = payload.get("data") or {}
    diff = data.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    quotes: dict[str, dict[str, Any]] = {}
    for row in diff:
        if not isinstance(row, dict):
            continue
        raw_code = str(row.get("f12") or "").strip()
        if not raw_code:
            continue
        if raw_code[:2].lower() in {"sh", "sz", "bj"}:
            code = raw_code.lower()
        else:
            market = str(row.get("f13") or "0")
            code = ("sh" if market == "1" else "sz") + raw_code
        price = _to_float(row.get("f2"))
        pre_close = _to_float(row.get("f18"))
        if price is None:
            continue
        change = price - pre_close if pre_close is not None else None
        change_pct = _to_float(row.get("f3"))
        if change_pct is not None:
            change_pct /= 100.0
        elif change is not None and pre_close:
            change_pct = change / pre_close
        market_cap = _to_float(row.get("f20"))
        kind, is_index = _quote_kind(code)
        shares = None
        if not is_index and market_cap is not None and price:
            shares = market_cap / price
        quotes[code] = {
            "code": code,
            "name": str(row.get("f14") or "").strip(),
            "kind": kind,
            "price": price,
            "pre_close": pre_close,
            "open": _to_float(row.get("f17")),
            "high": _to_float(row.get("f15")),
            "low": _to_float(row.get("f16")),
            "change": change,
            "change_pct": change_pct,
            "volume": _to_float(row.get("f5")),
            "amount": _to_float(row.get("f6")),
            "turnover": None,
            "shares": shares,
            "market_cap": None if is_index else market_cap,
            "quote_time": None,
            "source": "eastmoney",
            "stale": False,
        }
    return quotes


def fetch_eastmoney_quotes(codes: Iterable[str], *, timeout: int = config.HTTP_TIMEOUT_S) -> dict[str, dict[str, Any]]:
    """东方财富批量报价（仅在腾讯缺代码/失败时使用）。"""
    unique = list(dict.fromkeys(codes))
    if not unique:
        return {}
    fields = "f12,f13,f14,f2,f3,f5,f6,f15,f16,f17,f18,f20"
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique), config.QUOTE_BATCH_SIZE):
        batch = unique[start : start + config.QUOTE_BATCH_SIZE]
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": fields,
            "secids": ",".join(_em_secid(code) for code in batch),
        }
        url = config.EASTMONEY_QUOTE_URL + "?" + urllib.parse.urlencode(params)
        text = _http_get(url, referer=config.EASTMONEY_REFERER, timeout=timeout)
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise SourceError(f"东方财富报价返回非 JSON：{exc}") from exc
        result.update(parse_eastmoney_quotes(payload))
    return result


# ---------------------------------------------------------------- 新浪报价


_SINA_QUOTE_ROW = re.compile(r'hq_str_([a-z]{2}\d+)="([^"]*)"')


def parse_sina_quotes(text: str) -> dict[str, dict[str, Any]]:
    """解析新浪 ``hq.sinajs.cn`` 报价。

    字段：0 名称、1 开、2 昨收、3 现价、4 高、5 低、6 买一、7 卖一、
    8 成交量（股）、9 成交额（元）……30 日期、31 时间。新浪没有总份额字段。
    """
    quotes: dict[str, dict[str, Any]] = {}
    for match in _SINA_QUOTE_ROW.finditer(text):
        code, body = match.group(1), match.group(2)
        parts = body.split(",")
        if len(parts) < 32 or not parts[0].strip():
            continue
        price = _to_float(parts[3])
        pre_close = _to_float(parts[2])
        if price is None or price <= 0:
            continue
        volume_shares = _to_float(parts[8])
        amount = _to_float(parts[9])
        change = price - pre_close if pre_close is not None else None
        change_pct = change / pre_close if change is not None and pre_close else None
        kind, is_index = _quote_kind(code)
        quote_time = None
        date_text = parts[30].strip() if len(parts) > 30 else ""
        time_text = parts[31].strip() if len(parts) > 31 else ""
        if date_text and time_text:
            try:
                moment = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=config.SHANGHAI_TZ
                )
                quote_time = moment.isoformat(timespec="seconds")
            except ValueError:
                quote_time = None
        quotes[code] = {
            "code": code,
            "name": parts[0].strip(),
            "kind": kind,
            "price": price,
            "pre_close": pre_close,
            "open": _to_float(parts[1]),
            "high": _to_float(parts[4]),
            "low": _to_float(parts[5]),
            "change": change,
            "change_pct": change_pct,
            # 新浪成交量单位是「股」，契约 volume 用「手」
            "volume": volume_shares / 100 if volume_shares is not None else None,
            "amount": amount,
            "turnover": None,
            "shares": None,
            "market_cap": None,
            "quote_time": quote_time,
            "source": "sina",
            "stale": False,
        }
    return quotes


def fetch_sina_quotes(codes: Iterable[str], *, timeout: int = config.HTTP_TIMEOUT_S) -> dict[str, dict[str, Any]]:
    """新浪批量报价（GBK，需 Referer）。"""
    unique = list(dict.fromkeys(codes))
    if not unique:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique), config.QUOTE_BATCH_SIZE):
        batch = unique[start : start + config.QUOTE_BATCH_SIZE]
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        text = _http_get(url, referer=config.SINA_REFERER, encoding="gbk", timeout=timeout)
        result.update(parse_sina_quotes(text))
    return result


# ---------------------------------------------------------------- 多源回退框架


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) == 0
    return False


def _legacy_source_name(source_id: str) -> str:
    return {
        "tencent_quote": "tencent",
        "sina_quote": "sina",
        "eastmoney_quote": "eastmoney",
        "tencent_bars": "tencent",
        "sina_bars": "sina",
        "eastmoney_bars": "eastmoney",
        "tencent_intraday": "tencent",
        "tencent_minute": "tencent",
        "eastmoney_minute": "eastmoney",
        "sina_minute": "sina",
        "eastmoney_fund": "eastmoney",
    }.get(source_id, source_id)


def _run_fallback(
    kind: str,
    attempts: Sequence[tuple[str, Callable[[], Any]]],
) -> tuple[Any, str]:
    """按优先级依次尝试源，记录指标/熔断；全部失败抛异常。

    - ``NoDataError``：上游成功但该标的无数据 → 不计失败、不熔断，继续下一个源；
    - 其他异常：源故障 → 记录指标，连续失败触发熔断。
    全部源都无数据时抛 ``NoDataError``，否则抛 ``SourceError``。
    """
    errors: list[str] = []
    no_data_sources = 0
    failed_sources = 0
    for index, (source_id, fetch) in enumerate(attempts):
        if not observability.registry.allow(source_id):
            errors.append(f"{source_id}: 熔断/禁用")
            continue
        started = time.perf_counter()
        try:
            value = fetch()
            if _is_empty_value(value):
                raise NoDataError("返回空")
            latency = int((time.perf_counter() - started) * 1000)
            observability.registry.record_success(source_id, latency, fallback=index > 0)
            return value, source_id
        except NoDataError as exc:
            no_data_sources += 1
            errors.append(f"{source_id}: 无数据（{exc}）")
        except Exception as exc:  # noqa: BLE001 - 源故障才记录并熔断
            failed_sources += 1
            observability.registry.record_failure(source_id, str(exc))
            errors.append(f"{source_id}: {exc}")
    if no_data_sources and not failed_sources:
        raise NoDataError("暂无该标的数据")
    raise SourceError(f"所有 {kind} 源都失败：{'；'.join(errors)}")


# ---------------------------------------------------------------- 批量报价（多源）


#: 未知代码的报价负缓存（code → 过期 monotonic），避免反复打上游/污染源健康
_quote_no_data: dict[str, float] = {}
_quote_no_data_lock = threading.Lock()


def _quote_no_data_ttl() -> float:
    return float(getattr(config, "QUOTE_NO_DATA_TTL_S", 60))


def _filter_quote_no_data(codes: Iterable[str]) -> list[str]:
    now = time.monotonic()
    with _quote_no_data_lock:
        for code in [code for code, until in _quote_no_data.items() if until <= now]:
            _quote_no_data.pop(code, None)
        return [code for code in codes if code not in _quote_no_data]


def _mark_quote_no_data(codes: Iterable[str]) -> None:
    until = time.monotonic() + _quote_no_data_ttl()
    with _quote_no_data_lock:
        for code in codes:
            _quote_no_data[code] = until
        if len(_quote_no_data) > 2048:
            now = time.monotonic()
            for code in [code for code, expiry in _quote_no_data.items() if expiry <= now]:
                _quote_no_data.pop(code, None)


def _clear_quote_no_data(codes: Iterable[str]) -> None:
    with _quote_no_data_lock:
        for code in codes:
            _quote_no_data.pop(code, None)


def fetch_quotes(codes: Iterable[str], *, timeout: int = config.HTTP_TIMEOUT_S) -> QuoteBatch:
    """批量报价：腾讯 → 新浪 → 东财；新浪缺总份额时再用东财补齐份额。

    未知代码（上游正常但无此标的）抛 ``NoDataError``，不记源失败、不熔断；
    并用短 TTL 负缓存避免重复请求。
    """
    unique = list(dict.fromkeys(codes))
    fetched_at = store.iso_now()
    result = QuoteBatch(fetched_at=fetched_at)
    if not unique:
        return result
    active = _filter_quote_no_data(unique)
    if not active:
        raise NoDataError("暂无该标的的报价")
    remaining = list(active)
    attempts: list[tuple[str, Callable[[], Any]]] = [
        ("tencent_quote", lambda: fetch_tencent_quotes(remaining, timeout=timeout)),
        ("sina_quote", lambda: fetch_sina_quotes(remaining, timeout=timeout)),
        ("eastmoney_quote", lambda: fetch_eastmoney_quotes(remaining, timeout=timeout)),
    ]
    used: list[str] = []
    attempted: set[str] = set()
    for index, (source_id, fetch) in enumerate(attempts):
        if not remaining:
            break
        if not observability.registry.allow(source_id):
            attempted.add(source_id)
            result.warnings.append(f"{source_id} 熔断/禁用，跳过")
            continue
        attempted.add(source_id)
        started = time.perf_counter()
        try:
            batch = fetch()
            if not batch:
                raise NoDataError("返回空")
            latency = int((time.perf_counter() - started) * 1000)
            observability.registry.record_success(source_id, latency, fallback=index > 0)
            for code, quote in batch.items():
                if code in remaining:
                    result.quotes[code] = quote
            remaining = [code for code in remaining if code not in batch]
            used.append(_legacy_source_name(source_id))
        except NoDataError:
            # 上游正常但该批代码没有报价：不记失败、不熔断
            result.warnings.append(f"{_legacy_source_name(source_id)} 无该标的数据")
        except Exception as exc:  # noqa: BLE001 - 源故障才记录
            observability.registry.record_failure(source_id, str(exc))
            result.warnings.append(f"{_legacy_source_name(source_id)} 报价失败：{exc}")
    # 份额补齐：新浪报价没有总份额字段，用东财补齐非指数代码（团队份额计算依赖它）
    if result.quotes and "eastmoney_quote" not in attempted:
        missing_shares = [
            code
            for code, quote in result.quotes.items()
            if quote.get("shares") is None and not _quote_kind(code)[1]
        ]
        if missing_shares and observability.registry.allow("eastmoney_quote"):
            started = time.perf_counter()
            enriched = 0
            try:
                extra = fetch_eastmoney_quotes(missing_shares, timeout=timeout)
                latency = int((time.perf_counter() - started) * 1000)
                observability.registry.record_success("eastmoney_quote", latency, fallback=True)
                for code, quote in extra.items():
                    target = result.quotes.get(code)
                    if target is None:
                        continue
                    if target.get("shares") is None and quote.get("shares") is not None:
                        target["shares"] = quote["shares"]
                        enriched += 1
                    if target.get("market_cap") is None and quote.get("market_cap") is not None:
                        target["market_cap"] = quote["market_cap"]
            except NoDataError:
                pass
            except SourceError as exc:
                observability.registry.record_failure("eastmoney_quote", str(exc))
            if enriched:
                used.append("eastmoney")
    if result.quotes:
        _clear_quote_no_data(result.quotes)
    still_missing = [code for code in remaining if code not in result.quotes]
    if still_missing:
        _mark_quote_no_data(still_missing)
        result.warnings.append(f"{len(still_missing)} 个代码未取到报价：{','.join(still_missing[:6])}")
    if not result.quotes:
        raise NoDataError("暂无该标的的报价")
    result.source = "+".join(dict.fromkeys(used)) or "unknown"
    return result


# ---------------------------------------------------------------- 腾讯日线


def parse_tencent_bars(payload: dict[str, Any], code: str, *, day_key: str | None = None) -> list[dict[str, Any]]:
    """解析腾讯 fqkline 日线：行格式 ``[date, open, close, high, low, volume]``。"""
    node = ((payload.get("data") or {}).get(code)) or {}
    rows: list[Any] = []
    for key in (day_key,) if day_key else ("qfqday", "day", "hfqday"):
        candidate = node.get(key)
        if isinstance(candidate, list) and candidate:
            rows = candidate
            break
    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        date = str(row[0])[:10]
        try:
            open_price = float(row[1])
            close = float(row[2])
            high = float(row[3])
            low = float(row[4])
            volume = float(row[5]) if row[5] not in (None, "") else 0.0
        except (TypeError, ValueError):
            continue
        if close <= 0 or open_price <= 0 or high <= 0 or low <= 0:
            continue
        bars.append(
            {"date": date, "open": open_price, "high": high, "low": low, "close": close, "volume": volume}
        )
    bars.sort(key=lambda item: item["date"])
    return bars


def fetch_tencent_bars(
    code: str,
    count: int = 500,
    *,
    timeout: int = config.HTTP_TIMEOUT_S,
    retries: int = config.HTTP_RETRIES,
    require_qfq: bool = False,
) -> list[dict[str, Any]]:
    """腾讯前复权日线。"""
    param = f"{code},day,,,{count},qfq"
    url = config.TENCENT_BARS_URL + "?" + urllib.parse.urlencode({"param": param})
    text = _http_get(url, referer=config.TENCENT_REFERER, timeout=timeout, retries=retries)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"腾讯日线返回非 JSON：{exc}") from exc
    bars = parse_tencent_bars(payload, code, day_key="qfqday" if require_qfq else None)
    if not bars:
        raise NoDataError(f"{code} 腾讯无日线数据")
    return bars


# ---------------------------------------------------------------- 日线兜底（东方财富）


def parse_eastmoney_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """解析东方财富 k 线：``date,open,close,high,low,volume`` 逗号分隔。"""
    lines = ((payload.get("data") or {}).get("klines")) or []
    bars: list[dict[str, Any]] = []
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        try:
            open_price = float(parts[1])
            close = float(parts[2])
            high = float(parts[3])
            low = float(parts[4])
            volume = float(parts[5])
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        bars.append(
            {
                "date": parts[0][:10],
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    bars.sort(key=lambda item: item["date"])
    return bars


def fetch_eastmoney_bars(
    code: str,
    count: int = 500,
    *,
    timeout: int = config.HTTP_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """东方财富前复权日线（腾讯失败时兜底）。"""
    params = {
        "secid": _em_secid(code),
        "klt": "101",
        "fqt": "1",
        "lmt": str(count),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    url = config.EASTMONEY_KLINE_URL + "?" + urllib.parse.urlencode(params)
    text = _http_get(url, referer=config.EASTMONEY_REFERER, timeout=timeout, retries=0)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"东方财富日线返回非 JSON：{exc}") from exc
    bars = parse_eastmoney_bars(payload)
    if not bars:
        raise NoDataError(f"{code} 东方财富无日线数据")
    return bars


# ---------------------------------------------------------------- 新浪日线


def parse_sina_bars(payload: Any) -> list[dict[str, Any]]:
    """解析新浪日线 JSON：``[{day,open,high,low,close,volume}]``，成交量单位股。"""
    rows = payload if isinstance(payload, list) else []
    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("day") or "")[:10]
        try:
            open_price = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
            volume_shares = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if len(date) != 10 or close <= 0:
            continue
        bars.append(
            {
                "date": date,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_shares / 100.0,  # 股 → 手
            }
        )
    bars.sort(key=lambda item: item["date"])
    return bars


def fetch_sina_bars(
    code: str,
    count: int = 500,
    *,
    timeout: int = config.HTTP_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """新浪日线（scale=240，datalen=count）。"""
    params = {"symbol": code, "scale": "240", "ma": "no", "datalen": str(count)}
    url = config.SINA_KLINE_URL + "?" + urllib.parse.urlencode(params)
    text = _http_get(url, referer=config.SINA_REFERER, timeout=timeout, retries=0)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"新浪日线返回非 JSON：{exc}") from exc
    bars = parse_sina_bars(payload)
    if not bars:
        raise NoDataError(f"{code} 新浪无日线数据")
    return bars


# ---------------------------------------------------------------- 日线统一入口


def fetch_bars(
    code: str,
    count: int = 500,
    *,
    timeout: int = config.HTTP_TIMEOUT_S,
    retries: int = config.HTTP_RETRIES,
) -> tuple[list[dict[str, Any]], str]:
    """取日线：腾讯 → 东财 → 新浪。返回 ``(bars, source)``。"""
    bars, source_id = _run_fallback(
        "bars",
        [
            ("tencent_bars", lambda: fetch_tencent_bars(code, count, timeout=timeout, retries=retries)),
            ("eastmoney_bars", lambda: fetch_eastmoney_bars(code, count, timeout=timeout)),
            ("sina_bars", lambda: fetch_sina_bars(code, count, timeout=timeout)),
        ],
    )
    return bars, _legacy_source_name(source_id)


# ---------------------------------------------------------------- 腾讯分时


def parse_tencent_intraday(payload: dict[str, Any], code: str) -> dict[str, Any]:
    """解析腾讯分时。

    原始行：``HHMM price cumulative_volume(手) cumulative_amount(元)``，
    其中成交量/成交额是当日累计值。这里换算为：
    - ``volume``：该分钟成交量（手，前后相减）
    - ``avg``：累计均价 = 累计成交额 / (累计成交量 × 100)
    """
    data = payload.get("data") or {}
    section = data.get(code) or {}
    raw_days = section.get("data") or []
    quote = ((section.get("qt") or {}).get(code)) or []
    name = str(quote[1]).strip() if len(quote) > 1 else ""
    pre_close = _to_float(quote[4]) if len(quote) > 4 else None
    days: list[dict[str, Any]] = []
    for day in raw_days:
        if not isinstance(day, dict):
            continue
        day_text = str(day.get("date") or "")
        if len(day_text) != 8 or not day_text.isdigit():
            continue
        date = f"{day_text[:4]}-{day_text[4:6]}-{day_text[6:8]}"
        points: list[dict[str, Any]] = []
        previous_volume = 0.0
        last_cum_volume = 0.0
        last_cum_amount = 0.0
        for line in day.get("data") or []:
            parts = str(line).split()
            if len(parts) < 4:
                continue
            clock = parts[0]
            if len(clock) < 4 or not clock[:4].isdigit():
                continue
            hour, minute = int(clock[:2]), int(clock[2:4])
            if store.session_minute(hour, minute) is None:
                continue
            price = _to_float(parts[1])
            cum_volume = _to_float(parts[2])
            cum_amount = _to_float(parts[3])
            if price is None or price <= 0 or cum_volume is None:
                continue
            delta = max(0.0, cum_volume - previous_volume)
            previous_volume = cum_volume
            last_cum_volume = cum_volume
            if cum_amount is not None:
                last_cum_amount = cum_amount
            avg = None
            if last_cum_volume > 0 and last_cum_amount:
                avg = last_cum_amount / (last_cum_volume * 100.0)
            points.append(
                {
                    "time": f"{hour:02d}:{minute:02d}",
                    "label": f"{date} {hour:02d}:{minute:02d}",
                    "price": price,
                    "avg": avg,
                    "volume": delta,
                    "cumulative_amount": cum_amount,
                }
            )
        if not points:
            continue
        change = None
        if pre_close:
            change = points[-1]["price"] / pre_close - 1
        days.append({"date": date, "points": points, "change": change})
    days.sort(key=lambda item: item["date"])
    return {"code": code, "name": name, "pre_close": pre_close, "days": days}


def _fetch_tencent_intraday(code: str, *, timeout: int = config.HTTP_TIMEOUT_S) -> dict[str, Any]:
    url = config.TENCENT_INTRADAY_URL + "?" + urllib.parse.urlencode({"code": code})
    text = _http_get(url, referer=config.TENCENT_REFERER, timeout=timeout)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"腾讯分时返回非 JSON：{exc}") from exc
    result = parse_tencent_intraday(payload, code)
    if not result.get("days"):
        raise NoDataError(f"{code} 腾讯分时无数据")
    return result


# ---------------------------------------------------------------- 分钟 K（东财 / 新浪）


def parse_eastmoney_minute(payload: dict[str, Any], code: str = "") -> dict[str, Any]:
    """解析东财分钟 K：``time,open,close,high,low,volume(手),amount(元)``。"""
    data = payload.get("data") or {}
    rows = data.get("klines") or []
    pre_close = _to_float(data.get("preKPrice"))
    bars: list[dict[str, Any]] = []
    for line in rows:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        time_text = parts[0].strip()
        if len(time_text) < 16:
            continue
        try:
            open_price = float(parts[1])
            close = float(parts[2])
            high = float(parts[3])
            low = float(parts[4])
            volume = float(parts[5])
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        amount = _to_float(parts[6]) if len(parts) > 6 else None
        bars.append(
            {
                "time": time_text[:16],
                "date": time_text[:10],
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )
    bars.sort(key=lambda bar: bar["time"])
    return {"code": code, "name": str(data.get("name") or ""), "pre_close": pre_close, "bars": bars}


def fetch_eastmoney_minute(
    code: str,
    interval: int,
    limit: int,
    *,
    timeout: int = config.MINUTE_FETCH_TIMEOUT_S,
) -> dict[str, Any]:
    """东财分钟 K（interval=1/5/15/30/60）。"""
    params = {
        "secid": _em_secid(code),
        "klt": str(interval),
        "fqt": "1",
        "lmt": str(limit),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    url = config.EASTMONEY_MINUTE_URL + "?" + urllib.parse.urlencode(params)
    text = _http_get(url, referer=config.EASTMONEY_REFERER, timeout=timeout, retries=0)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"东财分钟K返回非 JSON：{exc}") from exc
    result = parse_eastmoney_minute(payload, code)
    if not result["bars"]:
        raise NoDataError(f"{code} 东财分钟K无数据")
    return result


def parse_tencent_minute(payload: dict[str, Any], code: str) -> dict[str, Any]:
    """Real provider OHLC, not prices sampled from the intraday line.

    Tencent m1 rows are [YYYYMMDDHHMM, open, close, high, low, volume(lots), ...].
    Reject conflicting duplicate timestamps rather than choosing one silently.
    """
    node = (payload.get("data") or {}).get(code) or {}
    rows: dict[str, dict[str, Any] | None] = {}
    for row in node.get("m1") or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        text = str(row[0])
        try:
            stamp = datetime.strptime(text, "%Y%m%d%H%M")
        except ValueError:
            continue
        if stamp.strftime("%Y%m%d%H%M") != text:
            continue
        clock = stamp.hour * 60 + stamp.minute
        if not (571 <= clock <= 690 or 781 <= clock <= 900):
            continue
        values = [_to_float(v) for v in row[1:6]]
        if any(v is None for v in values):
            continue
        opening, close, high, low, volume = values
        if low <= 0 or high < max(opening, close) or low > min(opening, close) or volume < 0:
            continue
        key = stamp.strftime("%Y-%m-%d %H:%M")
        bar = dict(time=key, date=key[:10], open=opening, close=close, high=high, low=low,
                   volume=volume, amount=None)
        if key in rows and rows[key] != bar:
            rows[key] = None
        else:
            rows[key] = bar
    return {"code": code, "bars": [rows[k] for k in sorted(rows) if rows[k] is not None]}


def fetch_tencent_minute(code: str, limit: int, *, timeout: int = config.MINUTE_FALLBACK_TIMEOUT_S) -> dict[str, Any]:
    params = urllib.parse.urlencode({"param": f"{code},m1,,{min(max(limit, 1), 1000)}"})
    text = _http_get(config.TENCENT_MINUTE_URL + "?" + params, referer=config.TENCENT_REFERER,
                     timeout=timeout, retries=0)
    try:
        result = parse_tencent_minute(json.loads(text), code)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceError(f"腾讯分钟K返回无效数据：{exc}") from exc
    if not result["bars"]:
        raise NoDataError(f"{code} 腾讯分钟K无数据")
    return result


#: 新浪分钟 K 支持的周期（1 分钟不支持，实测返回 null）
SINA_MINUTE_INTERVALS = frozenset({5, 15, 30, 60})


def parse_sina_minute(payload: Any, code: str = "") -> dict[str, Any]:
    """解析新浪分钟 K：``[{day,open,high,low,close,volume(股)}]``。"""
    rows = payload if isinstance(payload, list) else []
    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")
        if len(day) < 16:
            continue
        try:
            open_price = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
            volume_shares = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        bars.append(
            {
                "time": day[:16],
                "date": day[:10],
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_shares / 100.0,  # 股 → 手
                "amount": None,
            }
        )
    bars.sort(key=lambda bar: bar["time"])
    return {"code": code, "name": "", "pre_close": None, "bars": bars}


def fetch_sina_minute(
    code: str,
    interval: int,
    limit: int,
    *,
    timeout: int = config.MINUTE_FALLBACK_TIMEOUT_S,
) -> dict[str, Any]:
    """新浪分钟 K（仅支持 5/15/30/60 分钟）。"""
    if interval not in SINA_MINUTE_INTERVALS:
        raise SourceError(f"新浪分钟K不支持 {interval} 分钟")
    params = {"symbol": code, "scale": str(interval), "ma": "no", "datalen": str(limit)}
    url = config.SINA_KLINE_URL + "?" + urllib.parse.urlencode(params)
    text = _http_get(url, referer=config.SINA_REFERER, timeout=timeout, retries=0)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SourceError(f"新浪分钟K返回非 JSON：{exc}") from exc
    result = parse_sina_minute(payload, code)
    if not result["bars"]:
        raise NoDataError(f"{code} 新浪分钟K无数据")
    return result


def _intraday_from_minute(data: Mapping[str, Any], code: str) -> dict[str, Any]:
    """把分钟 K 折成 ``/api/market/intraday`` 需要的按日分时结构。"""
    bars = data.get("bars") or []
    pre_close = data.get("pre_close")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        grouped.setdefault(str(bar["date"]), []).append(bar)
    days: list[dict[str, Any]] = []
    for date in sorted(grouped):
        points: list[dict[str, Any]] = []
        cum_volume = 0.0
        cum_amount = 0.0
        for bar in sorted(grouped[date], key=lambda item: item["time"]):
            volume = float(bar.get("volume") or 0.0)
            amount = _to_float(bar.get("amount"))
            cum_volume += volume
            if amount:
                cum_amount += amount
            avg = cum_amount / (cum_volume * 100.0) if cum_amount and cum_volume else None
            points.append(
                {
                    "time": str(bar["time"])[11:16],
                    "label": str(bar["time"]),
                    "price": float(bar["close"]),
                    "avg": avg,
                    "volume": volume,
                }
            )
        if not points:
            continue
        change = None
        if pre_close:
            change = points[-1]["price"] / float(pre_close) - 1
        days.append({"date": date, "points": points, "change": change})
    if not days:
        raise NoDataError(f"{code} 分钟数据无法折成分时")
    return {
        "code": code,
        "name": str(data.get("name") or ""),
        "pre_close": pre_close,
        "days": days,
    }


def fetch_intraday(code: str, *, timeout: int = config.HTTP_TIMEOUT_S) -> dict[str, Any]:
    """5 日分时：腾讯 → 东财 1 分钟 K → 新浪 5 分钟 K。"""
    result, _source_id = _run_fallback(
        "intraday",
        [
            ("tencent_intraday", lambda: _fetch_tencent_intraday(code, timeout=timeout)),
            (
                "eastmoney_minute",
                lambda: _intraday_from_minute(
                    fetch_eastmoney_minute(code, 1, 1200, timeout=timeout), code
                ),
            ),
            (
                "sina_minute",
                lambda: _intraday_from_minute(
                    fetch_sina_minute(code, 5, 600, timeout=timeout), code
                ),
            ),
        ],
    )
    return result


# ---------------------------------------------------------------- 分钟 K 统一入口


def fetch_minute(
    code: str,
    interval: int,
    limit: int = config.MINUTE_LIMIT_DEFAULT,
    *,
    timeout: int = config.MINUTE_FETCH_TIMEOUT_S,
    fallback_timeout: int = config.MINUTE_FALLBACK_TIMEOUT_S,
) -> tuple[list[dict[str, Any]], str]:
    """东财主源；1m 回退腾讯真实 OHLC，其余周期回退新浪。"""
    attempts: list[tuple[str, Callable[[], Any]]] = [
        ("eastmoney_minute", lambda: fetch_eastmoney_minute(code, interval, limit, timeout=timeout))
    ]
    if interval == 1:
        attempts.append(("tencent_minute", lambda: fetch_tencent_minute(code, limit, timeout=fallback_timeout)))
    if interval in SINA_MINUTE_INTERVALS:
        attempts.append(
            ("sina_minute", lambda: fetch_sina_minute(code, interval, limit, timeout=fallback_timeout))
        )
    data, source_id = _run_fallback("minute", attempts)
    bars = data.get("bars") or []
    if limit and len(bars) > limit:
        bars = bars[-limit:]
    return bars, _legacy_source_name(source_id)


# ---------------------------------------------------------------- 腾讯搜索建议


def _decode_js_string(raw: str) -> str:
    """解码腾讯 smartbox 的 JS 字符串（``\\u9ec4\\u91d1`` → 黄金）。"""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def parse_tencent_search(text: str, limit: int = config.SEARCH_LIMIT) -> list[dict[str, str]]:
    """解析腾讯 smartbox 搜索建议。

    返回形如 ``{"code": "sh518880", "name": "黄金ETF", "kind": "etf", "market": "sh"}``
    的列表：字段以 ``~`` 分隔、以 ``^`` 分段；只接受 sh/sz + 6 位数字。
    """
    match = re.search(r'v_hint="([^"]*)"', text)
    if not match:
        return []
    raw = match.group(1)
    if not raw or raw == "N":
        return []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in raw.split("^"):
        parts = chunk.split("~")
        if len(parts) < 5:
            continue
        market, digits, name_raw, _pinyin, type_raw = parts[0].strip().lower(), parts[1].strip(), parts[2], parts[3], parts[4]
        if market not in {"sh", "sz"} or len(digits) != 6 or not digits.isdigit():
            continue
        code = market + digits
        if code in seen:
            continue
        seen.add(code)
        kind = "etf" if type_raw.strip().upper() in {"ETF", "LOF"} else "stock"
        results.append(
            {
                "code": code,
                "name": _decode_js_string(name_raw).strip(),
                "kind": kind,
                "market": market,
            }
        )
        if len(results) >= limit:
            break
    return results


def fetch_tencent_search(
    query: str,
    *,
    timeout: float = config.SEARCH_UPSTREAM_TIMEOUT_S,
    limit: int = config.SEARCH_LIMIT,
) -> list[dict[str, str]]:
    """腾讯搜索建议（请求路径用，超时即失败，由调用方回退本地目录）。"""
    query = query.strip()
    if not query:
        return []
    url = config.TENCENT_SEARCH_URL.format(query=urllib.parse.quote(query))
    text = _http_get(url, referer=config.TENCENT_REFERER, timeout=timeout, retries=0)
    return parse_tencent_search(text, limit)


# ---------------------------------------------------------------- 东方财富基金档案


_JS_OBJECT = {
    "Data_buySedemption": re.compile(r"var\s+Data_buySedemption\s*=\s*(\{.*?\})\s*;", re.S),
    "Data_holderStructure": re.compile(r"var\s+Data_holderStructure\s*=\s*(\{.*?\})\s*;", re.S),
    "fS_name": re.compile(r'var\s+fS_name\s*=\s*"([^"]*)"'),
}


def _extract_js_object(text: str, name: str) -> dict[str, Any] | None:
    pattern = _JS_OBJECT.get(name)
    if pattern is None:
        raise KeyError(name)
    match = pattern.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_fund_history(text: str) -> dict[str, Any]:
    """解析东方财富 ``pingzhongdata/<code>.js`` 中的报告期申赎与持有人结构。

    份额单位：亿份（上游原始单位）。``net = 期间申购 - 期间赎回``，
    ``net_pct = net / 期初份额``。
    """
    buy = _extract_js_object(text, "Data_buySedemption")
    if buy is None:
        raise SourceError("基金档案缺少 Data_buySedemption")
    categories = [str(item) for item in buy.get("categories") or []]
    series = {
        str(row.get("name") or ""): list(row.get("data") or [])
        for row in buy.get("series") or []
        if isinstance(row, dict)
    }
    subscriptions = series.get("期间申购") or []
    redemptions = series.get("期间赎回") or []
    totals = series.get("总份额") or []
    periods: list[dict[str, Any]] = []
    for index, date in enumerate(categories):
        subscription = _to_float(subscriptions[index]) if index < len(subscriptions) else None
        redemption = _to_float(redemptions[index]) if index < len(redemptions) else None
        total = _to_float(totals[index]) if index < len(totals) else None
        if subscription is None or redemption is None or total is None:
            continue
        net = subscription - redemption
        start_shares = total - net
        periods.append(
            {
                "date": date,
                "subscription": subscription,
                "redemption": redemption,
                "net": net,
                "net_pct": net / start_shares if start_shares else None,
                "start_shares": start_shares,
                "total_shares": total,
                "large_redemption": net <= -config.LARGE_REDEMPTION_YI,
            }
        )
    periods.sort(key=lambda item: item["date"])

    holders: list[dict[str, Any]] = []
    holder = _extract_js_object(text, "Data_holderStructure")
    if holder is not None:
        holder_series = {
            str(row.get("name") or ""): list(row.get("data") or [])
            for row in holder.get("series") or []
            if isinstance(row, dict)
        }
        institution = holder_series.get("机构持有比例") or []
        individual = holder_series.get("个人持有比例") or []
        for index, date in enumerate(holder.get("categories") or []):
            inst = _to_float(institution[index]) if index < len(institution) else None
            indiv = _to_float(individual[index]) if index < len(individual) else None
            if inst is None and indiv is None:
                continue
            holders.append({"date": str(date), "institution": inst, "individual": indiv})
        holders.sort(key=lambda item: item["date"])

    name_match = _JS_OBJECT["fS_name"].search(text)
    name = name_match.group(1).strip() if name_match else ""
    return {
        "name": name,
        "periods": periods,
        "holder_history": holders,
        "source": "东方财富基金档案：规模变动与持有人结构",
        "frequency": "报告期",
        "large_redemption_rule": f"报告期净赎回不低于 {config.LARGE_REDEMPTION_YI:g} 亿份",
    }


def _fetch_fund_history_raw(code: str) -> dict[str, Any]:
    digits = code[2:] if code[:2] in {"sh", "sz", "bj"} else code
    url = config.EASTMONEY_FUND_URL.format(digits=digits)
    text = _http_get(url, referer=config.EASTMONEY_REFERER, timeout=config.HTTP_FUND_TIMEOUT_S)
    result = parse_fund_history(text)
    result["code"] = code
    return result


def fetch_fund_history(code: str) -> dict[str, Any]:
    """拉取一只基金的报告期申赎（唯一源：东方财富基金档案）。

    单源数据：失败时记录指标并生成「单源风险」告警，便于数据页展示。
    """
    if not observability.registry.allow("eastmoney_fund"):
        raise SourceError("东方财富基金档案当前处于熔断/禁用状态")
    started = time.perf_counter()
    try:
        result = _fetch_fund_history_raw(code)
        if not result.get("periods"):
            raise NoDataError(f"{code} 报告期申赎无数据")
        latency = int((time.perf_counter() - started) * 1000)
        observability.registry.record_success("eastmoney_fund", latency)
        return result
    except NoDataError:
        # 该基金没有报告期数据：不算源故障，也不发单源风险告警
        raise
    except Exception as exc:  # noqa: BLE001 - 统一记录并转成 SourceError
        observability.registry.record_failure("eastmoney_fund", str(exc))
        observability.registry.add_alert(
            "warn",
            f"报告期申赎仅东方财富单源，本次失败：{exc}",
            "eastmoney_fund",
            key="fund:single-source",
        )
        raise SourceError(f"东方财富基金档案失败：{exc}") from exc


# ---------------------------------------------------------------- 源探测


def _probe_cases() -> list[tuple[str, Callable[[], Any]]]:
    """每个源一个最小探测请求（probe CLI / 启动发现共用）。"""
    return [
        ("tencent_quote", lambda: fetch_tencent_quotes(["sh510300"], timeout=6)),
        ("sina_quote", lambda: fetch_sina_quotes(["sh510300", "sz159915"], timeout=6)),
        ("eastmoney_quote", lambda: fetch_eastmoney_quotes(["sh510300"], timeout=6)),
        ("tencent_bars", lambda: fetch_tencent_bars("sh510300", 20, timeout=6, retries=0)),
        ("eastmoney_bars", lambda: fetch_eastmoney_bars("sh510300", 20, timeout=6)),
        ("sina_bars", lambda: fetch_sina_bars("sh510300", 20, timeout=6)),
        ("tencent_intraday", lambda: _fetch_tencent_intraday("sh510300", timeout=6)),
        ("tencent_minute", lambda: fetch_tencent_minute("sh510300", 20, timeout=6)),
        ("eastmoney_minute", lambda: fetch_eastmoney_minute("sh510300", 5, 20, timeout=6)),
        ("sina_minute", lambda: fetch_sina_minute("sh510300", 5, 20, timeout=6)),
        ("eastmoney_fund", lambda: _fetch_fund_history_raw("sh510300")),
    ]


def _describe_sample(source_id: str, value: Any) -> str:
    try:
        if source_id.endswith("_quote"):
            keys = list(value)[:2] if isinstance(value, dict) else []
            sample = value.get(keys[0]) if keys else None
            price = sample.get("price") if isinstance(sample, dict) else None
            name = sample.get("name") if isinstance(sample, dict) else None
            return f"{len(value)} 只 {','.join(keys)} 样例 price={price} name={name}"
        if source_id.endswith("_bars"):
            return f"{len(value)} 根 末根={value[-1] if value else None}"
        if source_id == "tencent_intraday":
            days = value.get("days") or []
            return f"{len(days)} 天 末日={days[-1]['date'] if days else None}"
        if source_id.endswith("_minute"):
            bars = value.get("bars") or []
            return f"{len(bars)} 根 末根={bars[-1]['time'] if bars else None}"
        if source_id == "eastmoney_fund":
            periods = value.get("periods") or []
            return f"{len(periods)} 个报告期 最近={periods[-1]['date'] if periods else None}"
    except Exception:  # noqa: BLE001 - 描述失败不影响探测结果
        pass
    return type(value).__name__


def probe_source(source_id: str) -> dict[str, Any]:
    """探测单个源；成功启用、失败禁用并写原因。"""
    spec = next((item for item in observability.SOURCES if item.id == source_id), None)
    if spec is None:
        raise KeyError(source_id)
    fetch = dict(_probe_cases()).get(source_id)
    if fetch is None:
        return {"id": source_id, "ok": False, "latency_ms": None, "detail": "未配置探测方法"}
    errors: list[str] = []
    latencies: list[int] = []
    for _attempt in range(2):  # 允许一次瞬时抖动
        started = time.perf_counter()
        try:
            value = fetch()
            if _is_empty_value(value):
                raise SourceError("返回空")
            latency = int((time.perf_counter() - started) * 1000)
            latencies.append(latency)
            observability.registry.record_success(source_id, latency)
            observability.registry.enable(source_id)
            return {
                "id": source_id,
                "ok": True,
                "latency_ms": latency,
                "detail": _describe_sample(source_id, value),
                "disabled": False,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    reason = errors[-1] if errors else "未知错误"
    observability.registry.record_failure(source_id, reason)
    observability.registry.set_disabled(source_id, f"探测失败：{reason}")
    return {
        "id": source_id,
        "ok": False,
        "latency_ms": latencies[0] if latencies else None,
        "detail": reason,
        "disabled": True,
    }


def probe_all() -> list[dict[str, Any]]:
    """探测全部数据源（probe CLI / 启动发现），返回每源结果。"""
    results = []
    for spec in observability.SOURCES:
        result = probe_source(spec.id)
        result["name"] = spec.name
        result["kind"] = spec.kind
        results.append(result)
    return results

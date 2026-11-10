"""命令行入口：``python -m etfmon <serve|daily|probe|import-legacy|fixtures>``。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import api, auth, collectors, config, observability, sources, store, views

LOG = logging.getLogger("etfmon")


def setup_logging(level: int = logging.INFO) -> None:
    """日志统一输出到 stdout（systemd/journald 直接收集）。

    被管道重定向时强制 UTF-8，避免中文在 Windows 控制台/重定向时乱码；
    交互式控制台保持默认编码（Windows 控制台原生支持中文）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


# ---------------------------------------------------------------- serve


def command_serve(args: argparse.Namespace) -> int:
    """启动 API 服务 + 后台采集线程。"""
    store.ensure_layout()
    cache = collectors.QuoteCache()
    try:
        batch = collectors.refresh_quotes(cache)
        LOG.info("启动预热：%d 只报价（%s）", len(batch.quotes), batch.source)
    except sources.SourceError as exc:
        LOG.warning("启动预热报价失败，先提供服务并等待后台重试：%s", exc)
    app = views.App(cache=cache, started_at=store.now())
    collector = collectors.Collector(cache)
    if not args.no_collect:
        collector.start()
    else:
        LOG.info("--no-collect：不启动后台采集线程")
    try:
        api.serve_forever(app, host=args.host, port=args.port)
    except KeyboardInterrupt:
        LOG.info("收到 Ctrl-C，准备退出")
    finally:
        collector.stop()
    return 0


# ---------------------------------------------------------------- daily


def command_daily(args: argparse.Namespace) -> int:
    """立即执行一次日终刷新。"""
    store.ensure_layout()
    cache = collectors.QuoteCache()
    summary = collectors.daily_refresh(cache)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- probe


def command_probe(args: argparse.Namespace) -> int:
    """探测全部公开数据源（多源注册表），打印可用性/延迟/样例与禁用原因。"""
    store.ensure_layout()
    print("数据源探测（多源注册表）")
    print("=" * 86)
    results = sources.probe_all()
    for item in results:
        flag = "OK  " if item.get("ok") else "FAIL"
        legacy = " (已禁用)" if item.get("disabled") else ""
        print(f"[{flag}] {item['id']:<18} {item['name']:<16} {item['kind']:<8} {str(item.get('latency_ms')) + 'ms':<8}{legacy}")
        print(f"       {item.get('detail') or ''}")
    print("-" * 86)
    available = [item for item in results if item.get("ok")]
    disabled = [item for item in results if not item.get("ok")]
    print(f"可用源 {len(available)}/{len(results)}；不可用 {len(disabled)}")
    if disabled:
        for item in disabled:
            print(f"  - {item['id']}: {item.get('detail')}")
    groups = observability.registry.groups()
    for kind, ids in groups.items():
        print(f"分组 {kind:<8}: {' → '.join(ids)}")
    alerts = observability.registry.alerts(limit=5)
    if alerts:
        print("最近告警：")
        for alert in alerts:
            print(f"  [{alert['level']}] {alert['at']} {alert['message']}")
    print("-" * 86)
    data_dir = store.data_root()
    print(f"数据目录: {data_dir}")
    print(f"schema:   {store.read_json(store.path_for('schema_version.json'), {})}")
    print(f"实体:     热点 {len(config.HOTSPOT_CODES)} 只 / 国家队 {len(config.TEAM_CODES)} 只 / 指数 {len(config.INDEX_CODES)} 个")
    bars_files = len(list(store.path_for("bars").glob("*.json"))) if store.path_for("bars").is_dir() else 0
    flows_files = len(list(store.path_for("flows").glob("*.json"))) if store.path_for("flows").is_dir() else 0
    print(
        f"本地数据: bars {bars_files} 个文件 / flows {flows_files} 个文件 / "
        f"daily {len(store.load_daily().get('days') or {})} 天"
    )
    critical = [item for item in results if item.get("disabled") and item["kind"] != "bars"]
    if critical:
        print(f"结论: 有 {len(critical)} 个非日线源不可用（见上方原因）")
        return 1
    if not available:
        print("结论: 全部数据源不可用")
        return 1
    print("结论: 主链路可用")
    return 0


# ---------------------------------------------------------------- import-legacy


def _merge_legacy_bars(legacy_dir: Path) -> dict[str, int]:
    """旧日线 → 新 bars/*.json（只追加日期，不覆盖已有 K 线）。"""
    added_files = 0
    merged_bars = 0
    source_dir = legacy_dir / "bars"
    if not source_dir.is_dir():
        return {"files": 0, "bars": 0}
    for path in sorted(source_dir.glob("*.json")):
        payload = store.read_json(path, {})
        code = str(payload.get("code") or path.stem)
        bars = [bar for bar in payload.get("bars") or [] if isinstance(bar, dict)]
        if not bars:
            continue
        existing = store.load_bars(code)
        merged, added, warnings = store.merge_bars(existing, bars)
        if warnings:
            LOG.warning("旧数据 %s 合并告警：%s", code, "; ".join(warnings))
        if not merged:
            continue
        store.save_bars(
            code,
            merged,
            name=config.entity_name(code),
            source=str(payload.get("source") or "legacy"),
            adjustment=str(payload.get("adjustment") or "qfq"),
        )
        added_files += 1
        merged_bars += added
    return {"files": added_files, "bars": merged_bars}


def _merge_legacy_flows(legacy_dir: Path) -> dict[str, int]:
    """旧 etf_flows → 新 flows/*.json（报告期取并集，快照按日期去重）。"""
    count = 0
    source_dir = legacy_dir / "etf_flows"
    if not source_dir.is_dir():
        return {"files": 0}
    for path in sorted(source_dir.glob("*.json")):
        legacy = store.read_json(path, {})
        code = str(legacy.get("code") or path.stem)
        if not isinstance(legacy, dict) or not code:
            continue
        current = store.load_flows(code)
        merged = dict(current)
        merged.setdefault("code", code)
        merged.setdefault("name", legacy.get("name") or config.entity_name(code))
        # 报告期/持有人：新数据为空时用旧数据补，不覆盖更完整的本地数据
        for key, legacy_key in (("periods", "periods"), ("holder_history", "holder_history")):
            if not merged.get(key) and legacy.get(legacy_key):
                merged[key] = legacy[legacy_key]
        snapshots: dict[str, dict[str, Any]] = {}
        for item in [*(legacy.get("snapshots") or []), *(current.get("snapshots") or [])]:
            if isinstance(item, dict) and item.get("date") and item.get("shares") is not None:
                snapshots[str(item["date"])] = dict(item)
        if snapshots:
            merged["snapshots"] = [snapshots[date] for date in sorted(snapshots)]
        for key in ("holder_history", "periods", "snapshots", "history_source", "history_frequency", "large_redemption_rule"):
            if key in legacy and key not in merged:
                merged[key] = legacy[key]
        store.save_flows(code, merged)
        count += 1
    return {"files": count}


def _merge_legacy_daily(legacy_dir: Path) -> dict[str, int]:
    """旧 national/daily.json → 新 daily.json（只合并日记录，丢弃旧盘中样本）。

    旧盘中样本是「旧份额基线」下算出来的，迁移后当日份额会被重新采集覆盖，
    两者并排展示会矛盾，因此不导入；盘中数据等新服务在交易时段重新采样。
    """
    payload = store.read_json(legacy_dir / "national" / "daily.json", {})
    if not isinstance(payload, dict):
        return {"days": 0, "intraday_days_skipped": 0}
    current = store.load_daily()
    days = current.setdefault("days", {})
    added_days = 0
    for date, entry in (payload.get("days") or {}).items():
        date = str(date)
        if date and isinstance(entry, dict) and date not in days:
            days[date] = entry
            added_days += 1
    intraday = current.setdefault("intraday", {})
    for date in sorted(intraday)[: -config.INTRADAY_KEEP_DAYS]:
        intraday.pop(date, None)
    for date in sorted(days)[: -config.DAILY_KEEP_DAYS]:
        days.pop(date, None)
    current["updated_at"] = store.iso_now()
    store.save_daily(current)
    skipped = len(payload.get("intraday") or {})
    return {"days": added_days, "intraday_days_skipped": skipped}


def command_import_legacy(args: argparse.Namespace) -> int:
    """把旧「本地台」数据迁到新数据目录（只做本地文件转换，不联网）。"""
    store.ensure_layout()
    legacy_dir = Path(args.source).expanduser().resolve() if args.source else config.LEGACY_DATA_DIR
    if not legacy_dir.is_dir():
        LOG.error("找不到旧数据目录：%s", legacy_dir)
        return 2
    bars = _merge_legacy_bars(legacy_dir)
    flows = _merge_legacy_flows(legacy_dir)
    daily = _merge_legacy_daily(legacy_dir)
    # 迁移后立刻自愈：清掉历史遗留（以前导入过）的不一致盘中样本
    reconcile = collectors.reconcile_intraday_samples()
    summary = {
        "source": str(legacy_dir),
        "target": str(store.data_root()),
        "bars": bars,
        "flows": flows,
        "daily": daily,
        "intraday_reconcile": {
            "removed_days": len(reconcile["removed_days"]),
            "removed_samples": reconcile["removed_samples"],
        },
    }
    store.append_task("import_legacy", ok=True, detail=f"{legacy_dir} → {store.data_root()}")

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["legacy_imported_at"] = store.iso_now()
        return state

    store.update_state(mutate)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("提示：迁移只复制历史。首轮采集由 `python -m etfmon daily` 或服务启动后的后台线程完成。")
    return 0


# ---------------------------------------------------------------- fixtures


def _write_fixture(path: Path, payload: Any) -> None:
    store.write_json(path, payload)
    print(f"  写入 {path}")


def command_fixtures(args: argparse.Namespace) -> int:
    """用真实数据生成 docs/fixtures/*.json 给前端联调。"""
    store.ensure_layout()
    cache = collectors.QuoteCache()
    try:
        batch = collectors.refresh_quotes(cache)
        LOG.info("fixtures 报价预热：%d 只（%s）", len(batch.quotes), batch.source)
    except sources.SourceError as exc:
        LOG.warning("报价预热失败，用最近一次落盘快照：%s", exc)
    needs_refresh = not store.load_daily().get("days") or not store.load_hotspots_latest()
    if needs_refresh and not args.offline:
        LOG.info("本地数据不足，先跑一次日终刷新（会访问上游）")
        collectors.daily_refresh(cache)
    app = views.App(cache=cache, started_at=store.now())
    output_dir = Path(args.output).expanduser().resolve() if args.output else config.REPO_DIR / "docs" / "fixtures"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"生成前端 fixture → {output_dir}")
    try:
        collectors.warmup_intraday(cache)
    except (sources.SourceError, OSError, ValueError) as exc:
        LOG.warning("分时缓存预热失败：%s", exc)
    _write_fixture(output_dir / "overview.json", views.build_overview(app))
    _write_fixture(output_dir / "team.json", views.build_team(app))
    _write_fixture(output_dir / "hotspots.json", views.build_hotspots(app))
    _write_fixture(output_dir / "bars.json", views.build_bars(app, "sh510300", "day", 300))
    _write_fixture(output_dir / "meta.json", views.build_meta(app))
    # v2：数据源可观测性 + 分钟 K 样例
    _write_fixture(output_dir / "sources.json", views.build_sources(app))
    try:
        _write_fixture(output_dir / "minute.json", views.build_minute(app, "sh510300", 5, 240))
    except (LookupError, OSError, ValueError) as exc:
        LOG.warning("分钟 K fixture 生成失败：%s", exc)
    try:
        _write_fixture(output_dir / "team-minute.json", views.build_team_minute(app, 1, 240))
    except (LookupError, OSError, ValueError) as exc:
        LOG.warning("国家队分钟 fixture 生成失败：%s", exc)
    # 额外方便联调的两个
    _write_fixture(output_dir / "watchlist.json", views.build_watchlist(app))
    try:
        _write_fixture(output_dir / "intraday.json", views.build_intraday(app, "sh510300", 5))
    except LookupError as exc:
        LOG.warning("分时 fixture 生成失败：%s", exc)
    print("完成。")
    return 0


# ---------------------------------------------------------------- set-password


def command_set_password(args: argparse.Namespace) -> int:
    """设置/重置登录密码（写入 ETF_DATA/auth.json 并 ++epoch）。"""
    store.ensure_layout()
    if args.show:
        print(json.dumps(auth.describe(), ensure_ascii=False, indent=2))
        return 0
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        source = "stdin"
    elif args.password is not None:
        password = args.password
        source = "--password"
        print("警告：--password 会把密码留在命令行历史/进程列表，建议改用 --password-stdin", file=sys.stderr)
    else:
        print("错误：需要 --password-stdin 或 --password（或 --show 查看当前配置）", file=sys.stderr)
        return 2
    if not password:
        print("错误：密码不能为空", file=sys.stderr)
        return 2
    try:
        summary = auth.set_password(args.user, password)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    store.append_task("set_password", ok=True, detail=f"user={summary['user']} epoch={summary['epoch']}")
    print(
        json.dumps(
            {
                "ok": True,
                "user": summary["user"],
                "epoch": summary["epoch"],
                "iterations": summary["iterations"],
                "updated_at": summary["updated_at"],
                "source": source,
                "note": "已更新密码；旧会话（epoch 之前签发的 cookie）立即失效。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(prog="python -m etfmon", description="ETF 观察台后端")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 API 服务（默认 127.0.0.1:8765）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    serve.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    serve.add_argument("--no-collect", action="store_true", help="只起 HTTP，不启动后台采集线程")

    sub.add_parser("daily", help="立即执行一次日终刷新")
    sub.add_parser("probe", help="探测数据源连通性/延迟/样例")

    legacy = sub.add_parser("import-legacy", help="迁移旧 _server/hub/data（仅本地文件转换）")
    legacy.add_argument("--source", default=None, help="旧数据目录（默认仓库内 _server/hub/data）")

    fixtures = sub.add_parser("fixtures", help="生成 docs/fixtures/*.json")
    fixtures.add_argument("--output", default=None, help="输出目录（默认 docs/fixtures）")
    fixtures.add_argument("--offline", action="store_true", help="不访问上游，只用本地数据")

    password = sub.add_parser("set-password", help="设置/重置登录密码（写入 auth.json 并 ++epoch）")
    password.add_argument("--user", default="tom", help="登录用户名（默认 tom）")
    password.add_argument("--password-stdin", action="store_true", help="从 stdin 读取密码（推荐）")
    password.add_argument("--password", default=None, help="直接传密码（不推荐，会留在命令行历史）")
    password.add_argument("--show", action="store_true", help="只打印当前用户/epoch（不含哈希）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。"""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # 不带子命令时等价于 `serve`（AGENT.md 的默认用法）
        args = argparse.Namespace(host="127.0.0.1", port=8765, no_collect=False)
        return command_serve(args)
    if args.command == "serve":
        return command_serve(args)
    if args.command == "daily":
        return command_daily(args)
    if args.command == "probe":
        return command_probe(args)
    if args.command == "import-legacy":
        return command_import_legacy(args)
    if args.command == "fixtures":
        return command_fixtures(args)
    if args.command == "set-password":
        return command_set_password(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

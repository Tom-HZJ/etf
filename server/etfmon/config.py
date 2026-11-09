"""ETF 观察台后端配置：路径、实体表（universe）、阈值与上游地址。

本模块只放常量与纯函数，不做 IO，方便测试与复用。
数据目录默认 ``server/data``，可用环境变量 ``ETF_DATA`` 覆盖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 版本与标识

APP_VERSION = "2.12.0"
PRODUCT = "ETF 观察台"
BUILT_AT = "2026-11-09"
SCHEMA_VERSION = 1

#: 规则版本号（热点/趋势阈值变更时递增，写入 payload 便于追溯）
HOTSPOT_RULE_VERSION = "hotspot-self-momentum-v2"

#: 热度分权重：自身20日动量40 + 5日动量20 + MA趋势20 + 5日量能15 + 20日新高5
HEAT_RULE = (
    "自身20日动量40 + 5日动量20 + MA趋势20 + 5日量能15 + 20日新高5；"
    "热度只用于排序，不是买卖分。"
)

# ---------------------------------------------------------------- 路径

PACKAGE_DIR = Path(__file__).resolve().parent
SERVER_DIR = PACKAGE_DIR.parent
REPO_DIR = SERVER_DIR.parent
#: 旧「本地台」数据目录，仅 ``import-legacy`` 使用；不存在时跳过。
LEGACY_DATA_DIR = REPO_DIR / "_server" / "hub" / "data"

# ---------------------------------------------------------------- 时间

#: 中国自 1991 年起不再使用夏令时，固定 +08:00 与 Asia/Shanghai 等价。
#: Windows 自带的 Python 没有 IANA 时区库，``zoneinfo`` 会失败，故直接用固定偏移，
#: 避免引入 tzdata 依赖（项目要求纯标准库）。
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

MORNING_START = 9 * 60 + 30  # 09:30
MORNING_END = 11 * 60 + 30  # 11:30
AFTERNOON_START = 13 * 60  # 13:00
AFTERNOON_END = 15 * 60  # 15:00
PRE_MARKET = 9 * 60 + 15  # 09:15 起视为盘前，报价开始有意义
CLOSE_RECORD = 15 * 60 + 10  # 15:10 之后写当日收盘份额
MORNING_SESSION_MINUTES = MORNING_END - MORNING_START + 1  # 121
AFTERNOON_SESSION_MINUTES = AFTERNOON_END - AFTERNOON_START + 1  # 121
DAY_SESSION_MINUTES = MORNING_SESSION_MINUTES + AFTERNOON_SESSION_MINUTES

#: 交易日日终刷新时间（收盘后等到份额落定）
DAILY_REFRESH_HOUR = 15
DAILY_REFRESH_MINUTE = 10

# ---------------------------------------------------------------- 采集节奏

QUOTE_TTL_S = 25  # 报价缓存有效期，请求路径只用缓存
QUOTE_REFRESH_S = 20  # 盘中刷新报价间隔
QUOTE_REFRESH_IDLE_S = 300  # 非交易时段刷新间隔
INTRADAY_SAMPLE_S = 120  # 盘中国家队份额采样间隔
STALE_AFTER_S = 300  # 交易时段报价超过该秒数标记 stale
LIVE_SAMPLE_MAX_AGE_S = 600  # 盘后仍视为「今日盘中采样」的时限

# ---------------------------------------------------------------- 保留量

DAILY_KEEP_DAYS = 400
INTRADAY_KEEP_DAYS = 10
WATCHLIST_MAX = 12
HOTSPOT_HISTORY_KEEP = 40
HOTSPOT_RANK_LOOKBACK = 5  # rank_change_5d 的 5 个交易日
HOTSPOT_HISTORY_BACKFILL = 12  # 首次构建时回填的历史快照数量
STATE_TASK_HISTORY = 60
BARS_LIMIT_DEFAULT = 300
BARS_LIMIT_MAX = 5000
QUOTES_MAX_CODES = 120

# ---------------------------------------------------------------- 阈值

TREND_THRESHOLD = 0.10  # 收盘离开极值 10% 才确认转折
LARGE_REDEMPTION_YI = 50.0  # 报告期净赎回不低于 50 亿份视为大额赎回
HEAT_TOP_N = 12
OVERVIEW_TOP_N = 3
HOTSPOT_MIN_BARS = 25
SEARCH_LIMIT = 20

# ---------------------------------------------------------------- 上游

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_S = 15
HTTP_FUND_TIMEOUT_S = 20
HTTP_RETRIES = 2

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_BARS_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_INTRADAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
TENCENT_SEARCH_URL = "https://smartbox.gtimg.cn/s3/?q={query}&t=all"
EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_FUND_URL = "https://fund.eastmoney.com/pingzhongdata/{digits}.js"
TENCENT_REFERER = "https://gu.qq.com/"
EASTMONEY_REFERER = "https://quote.eastmoney.com/"
SINA_REFERER = "https://finance.sina.com.cn/"
SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
EASTMONEY_MINUTE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

#: 自选新增时现场校验报价的超时（秒）
WATCHLIST_ADD_TIMEOUT_S = 10
#: 搜索上游兜底超时（秒）；请求路径总预算 3s 内必须回退
SEARCH_UPSTREAM_TIMEOUT_S = 2.5
#: 请求路径按需拉日线/分时的超时（秒）与失败负缓存时长（秒）
ONDEMAND_TIMEOUT_S = 2
ONDEMAND_FALLBACK_TIMEOUT_S = 1
ONDEMAND_NEGATIVE_TTL_S = 60

# ---------------------------------------------------------------- 认证

#: 密码文件与会话密钥文件名（均在 ETF_DATA 下，0600）
AUTH_FILENAME = "auth.json"
SESSION_SECRET_FILENAME = "session_secret"
#: PBKDF2-HMAC-SHA256 迭代次数
PBKDF2_ITERATIONS = 260_000
#: 会话有效期（天）与 cookie 名
SESSION_DAYS = 30
SESSION_COOKIE_NAME = "etf_session"
#: 登录限速：窗口内失败次数上限与窗口长度（秒）
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 300
#: 置 1/true 时 cookie 强制加 Secure（否则按 X-Forwarded-Proto 判断）
COOKIE_SECURE_ENV = "ETF_COOKIE_SECURE"
#: 密码最短长度
PASSWORD_MIN_LENGTH = 8

# ---------------------------------------------------------------- 认证模式（v2）

#: 认证模式：local（应用内登录，开发回退）| forward（Authelia 前向认证，生产）
AUTH_MODE = os.environ.get("ETF_AUTH_MODE", "local").strip().lower()
#: 管理员用户名（逗号分隔），可调用 /api/admin/sessions*
ADMIN_USERS = os.environ.get("ETF_ADMIN_USERS", "tom")


def auth_mode() -> str:
    """动态读取认证模式（环境变量优先，便于测试与部署切换）。"""
    value = os.environ.get("ETF_AUTH_MODE", AUTH_MODE).strip().lower()
    return value if value in {"local", "forward"} else "local"


def admin_users() -> tuple[str, ...]:
    """管理员用户列表（逗号分隔）。"""
    raw = os.environ.get("ETF_ADMIN_USERS", ADMIN_USERS)
    return tuple(item.strip() for item in str(raw).split(",") if item.strip())


#: Authelia 会话存储（Redis）连接信息
REDIS_HOST = os.environ.get("ETF_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("ETF_REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("ETF_REDIS_PASSWORD", "")
REDIS_DB = int(os.environ.get("ETF_REDIS_DB", "0"))
REDIS_TIMEOUT_S = float(os.environ.get("ETF_REDIS_TIMEOUT", "2"))
#: Authelia 会话键前缀（不同版本/配置可能不同；可用环境变量覆盖）
REDIS_SESSION_PATTERN = os.environ.get("ETF_REDIS_SESSION_PATTERN", "authelia-session:*")


def redis_session_pattern() -> str:
    """动态读取 Authelia 会话键匹配模式。"""
    return os.environ.get("ETF_REDIS_SESSION_PATTERN", REDIS_SESSION_PATTERN) or "authelia-session:*"


def redis_config() -> dict[str, object]:
    """动态读取 Redis 连接配置（测试/部署可切环境变量）。"""
    return {
        "host": os.environ.get("ETF_REDIS_HOST", REDIS_HOST),
        "port": int(os.environ.get("ETF_REDIS_PORT", str(REDIS_PORT))),
        "password": os.environ.get("ETF_REDIS_PASSWORD", REDIS_PASSWORD) or None,
        "db": int(os.environ.get("ETF_REDIS_DB", str(REDIS_DB))),
        "timeout": float(os.environ.get("ETF_REDIS_TIMEOUT", str(REDIS_TIMEOUT_S))),
    }

# ---------------------------------------------------------------- 数据源可观测性

#: 熔断：连续失败次数阈值与打开时长（秒）
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_S = 60
#: 告警去重冷却（秒）与保留条数
ALERT_COOLDOWN_S = 600
#: 源被标记 disabled 的告警冷却（永久禁用时避免每 10 分钟刷屏）
ALERT_DISABLED_COOLDOWN_S = 3600
ALERT_KEEP = 100
#: 交易时段数据超过该秒数未更新 → 数据过期告警
DATA_STALE_ALERT_S = 15 * 60
#: 告警 webhook（通用 JSON POST），为空则不发送
ALERT_WEBHOOK = os.environ.get("ETF_ALERT_WEBHOOK", "").strip()


def alert_webhook() -> str:
    """动态读取告警 webhook 地址（空表示不发送）。"""
    return os.environ.get("ETF_ALERT_WEBHOOK", ALERT_WEBHOOK).strip()


#: 未知代码的报价负缓存 TTL（秒），避免反复请求上游
QUOTE_NO_DATA_TTL_S = 60

# ---------------------------------------------------------------- 分钟 K

MINUTE_INTERVALS: tuple[int, ...] = (1, 5, 15, 30, 60)
MINUTE_LIMIT_DEFAULT = 240
MINUTE_LIMIT_MAX = 1000
#: 分钟 K 缓存 TTL：盘中 / 收盘后（秒）
MINUTE_CACHE_TTL_INTRADAY_S = 60
MINUTE_CACHE_TTL_CLOSED_S = 30 * 60
#: 分钟 K 按需拉取预算：东财 3s，新浪 1s（总 ≤4s）
MINUTE_FETCH_TIMEOUT_S = 3
MINUTE_FALLBACK_TIMEOUT_S = 1
MINUTE_NEGATIVE_TTL_S = 60
#: 采集线程分钟 K 刷新间隔（秒）与刷新范围
MINUTE_REFRESH_S = 120
MINUTE_REFRESH_HOTSPOT_TOP = 6

#: 单次腾讯批量报价的代码数量（URL 长度与响应体都要可控）
QUOTE_BATCH_SIZE = 60

# ---------------------------------------------------------------- 实体表
#
# 名称以腾讯行情返回为准；这里的 name 只是离线兜底显示名。
# 代码统一写成 sh/sz + 6 位数字。


@dataclass(frozen=True, slots=True)
class Entity:
    """一个可被报价/分析引用的标的。"""

    code: str
    name: str
    direction: str
    group: str
    broad: bool = False
    in_team: bool = False


#: 国家队篮子（旧站沿用的 26 只汇金核心宽基 ETF）。任务契约要求 26 只。
TEAM_BASKET: tuple[tuple[str, str, str], ...] = (
    ("sh510300", "沪深300ETF华泰柏瑞", "沪深300"),
    ("sh510310", "沪深300ETF易方达", "沪深300"),
    ("sh510330", "沪深300ETF华夏", "沪深300"),
    ("sz159919", "沪深300ETF嘉实", "沪深300"),
    ("sh510350", "沪深300ETF工银", "沪深300"),
    ("sh510050", "上证50ETF华夏", "上证50"),
    ("sh510100", "上证50ETF易方达", "上证50"),
    ("sh510500", "中证500ETF南方", "中证500"),
    ("sh512500", "中证500ETF华夏", "中证500"),
    ("sz159922", "中证500ETF嘉实", "中证500"),
    ("sh512100", "中证1000ETF南方", "中证1000"),
    ("sz159845", "中证1000ETF华夏", "中证1000"),
    ("sh560010", "中证1000ETF广发", "中证1000"),
    ("sz159629", "中证1000ETF富国", "中证1000"),
    ("sz159915", "创业板ETF易方达", "创业板"),
    ("sz159952", "创业板ETF广发", "创业板"),
    ("sz159977", "创业板ETF天弘", "创业板"),
    ("sz159949", "创业板ETF华安", "创业板"),
    ("sh588080", "科创50ETF易方达", "科创50"),
    ("sh588050", "科创50ETF工银", "科创50"),
    ("sh588000", "科创50ETF华夏", "科创50"),
    ("sh510180", "上证180ETF华安", "其他宽基"),
    ("sh510230", "上证180金融ETF国泰", "其他宽基"),
    ("sz159901", "深证100ETF易方达", "其他宽基"),
    ("sh515800", "中证800ETF汇添富", "其他宽基"),
    ("sh560050", "MSCI中国A50ETF汇添富", "其他宽基"),
)

#: 宽基代表（只用于指数/基准与宽度参照，不参与热点排序）
BROAD_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    ("sh510300", "沪深300", "沪深300"),
    ("sh510500", "中证500", "中证500"),
    ("sh512100", "中证1000", "中证1000"),
    ("sh563300", "中证2000", "中证2000"),
    ("sz159915", "创业板", "创业板"),
    ("sh588000", "科创50", "科创50"),
    ("sh510050", "上证50", "上证50"),
    ("sh510880", "红利", "红利"),
)

#: 行业/主题代表 ETF（54 只，热点雷达排序对象）
HOTSPOT_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    # 科技
    ("sh512480", "半导体", "科技"),
    ("sz159995", "芯片", "科技"),
    ("sh588200", "科创芯片", "科技"),
    ("sz159819", "人工智能", "科技"),
    ("sh562500", "机器人", "科技"),
    ("sh516510", "云计算", "科技"),
    ("sh515880", "通信", "科技"),
    ("sh515050", "5G通信", "科技"),
    ("sh512980", "传媒", "科技"),
    ("sz159869", "游戏", "科技"),
    ("sz159852", "软件", "科技"),
    ("sz159998", "计算机", "科技"),
    ("sz159732", "消费电子", "科技"),
    # 医药
    ("sh512010", "医药", "医药"),
    ("sh512170", "医疗", "医药"),
    ("sz159992", "创新药", "医药"),
    ("sh513120", "港股创新药", "医药"),
    ("sh560080", "中药", "医药"),
    ("sz159883", "医疗器械", "医药"),
    # 消费
    ("sh515170", "食品饮料", "消费"),
    ("sh512690", "酒", "消费"),
    ("sz159996", "家电", "消费"),
    ("sz159766", "旅游", "消费"),
    ("sz159865", "养殖", "消费"),
    ("sz159825", "农业", "消费"),
    # 金融
    ("sh512000", "券商", "金融"),
    ("sh512800", "银行", "金融"),
    ("sh512070", "非银", "金融"),
    ("sh510230", "金融", "金融"),
    # 周期
    ("sh512400", "有色金属", "周期"),
    ("sh515220", "煤炭", "周期"),
    ("sh515210", "钢铁", "周期"),
    ("sz159870", "化工", "周期"),
    ("sh516780", "稀土", "周期"),
    ("sz159745", "建材", "周期"),
    ("sh518880", "黄金", "周期"),
    ("sh512580", "环保", "周期"),
    # 制造
    ("sh512660", "军工", "制造"),
    ("sh512810", "国防军工", "制造"),
    ("sh516110", "汽车", "制造"),
    ("sh515250", "智能汽车", "制造"),
    ("sz159755", "电池", "制造"),
    ("sh515790", "光伏", "制造"),
    ("sh516160", "新能源", "制造"),
    ("sz159611", "电力", "制造"),
    ("sh516950", "基建", "制造"),
    ("sh512200", "房地产", "制造"),
    ("sz159206", "卫星", "制造"),
    ("sz159227", "航空航天", "制造"),
    ("sz159378", "通用航空", "制造"),
    ("sh515030", "新能源车", "制造"),
    ("sz159566", "储能电池", "制造"),
    # 港股 / 海外
    ("sh513180", "恒生科技", "港股"),
    ("sh513770", "港股互联网", "港股"),
    # 美股（QDII，A 股上市）
    ("sz159941", "纳指100", "美股"),
    ("sh513500", "标普500", "美股"),
    ("sh513400", "道琼斯", "美股"),
    ("sh513850", "美国50", "美股"),
    ("sz159509", "纳指科技", "美股"),
)

#: 指数条（总览首屏 6 个指数）。指数没有总份额，kind 记为 stock。
INDEX_QUOTES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
)

#: 候选沪深300 ETF，按总市值动态选出基准
CSI300_CANDIDATES: tuple[str, ...] = (
    "sh510300",
    "sh510310",
    "sh510330",
    "sh510350",
    "sz159919",
)

#: 默认自选（海选 6 个，覆盖宽基/科技/周期/港股）
DEFAULT_WATCHLIST: tuple[tuple[str, str, str], ...] = (
    ("sh510300", "etf", "沪深300"),
    ("sh512480", "etf", "半导体"),
    ("sh588000", "etf", "科创50"),
    ("sz159915", "etf", "创业板"),
    ("sh512400", "etf", "有色金属"),
    ("sh513180", "etf", "恒生科技"),
)

_DEFAULT_WATCHLIST_BY_CODE = {code: (kind, label) for code, kind, label in DEFAULT_WATCHLIST}


BROAD_CODES: frozenset[str] = frozenset(code for code, _direction, _group in BROAD_UNIVERSE)


def _build_entities() -> dict[str, Entity]:
    entities: dict[str, Entity] = {}

    # 先放国家队篮子：只决定 in_team 与名称兜底，不决定 broad
    # （510230 是「其他宽基」里的金融行业 ETF，在热点雷达里仍按金融方向参与排序）。
    for code, name, group in TEAM_BASKET:
        entities[code] = Entity(code, name, group, group, broad=False, in_team=True)

    for code, direction, group in HOTSPOT_UNIVERSE:
        existing = entities.get(code)
        entities[code] = Entity(
            code,
            existing.name if existing else direction,
            direction,
            group,
            broad=False,
            in_team=bool(existing and existing.in_team),
        )

    for code, direction, group in BROAD_UNIVERSE:
        existing = entities.get(code)
        entities[code] = Entity(
            code,
            existing.name if existing else direction,
            existing.direction if existing else direction,
            existing.group if existing else group,
            broad=True,
            in_team=bool(existing and existing.in_team),
        )

    return entities


ENTITIES: dict[str, Entity] = _build_entities()
#: 所有会采集日线的代码（实体 + 指数）
ALL_CODES: tuple[str, ...] = tuple(
    dict.fromkeys([*ENTITIES.keys(), *[code for code, _name in INDEX_QUOTES]])
)
#: 热点排序对象：行业/主题代表 ETF（宽基只做基准，不参与排序）
HOTSPOT_CODES: tuple[str, ...] = tuple(
    code for code, _direction, _group in HOTSPOT_UNIVERSE if code not in BROAD_CODES
)
TEAM_CODES: tuple[str, ...] = tuple(code for code, _name, _group in TEAM_BASKET)
INDEX_CODES: tuple[str, ...] = tuple(code for code, _name in INDEX_QUOTES)
#: 报价采集范围（实体 + 指数）
QUOTE_CODES: tuple[str, ...] = tuple(
    dict.fromkeys([*TEAM_CODES, *HOTSPOT_CODES, *ENTITIES.keys(), *INDEX_CODES])
)


def data_dir() -> Path:
    """数据根目录：环境变量 ``ETF_DATA`` 优先，否则 ``server/data``。"""
    raw = os.environ.get("ETF_DATA", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (SERVER_DIR / "data").resolve()


def is_etf_code(code: str) -> bool:
    """按交易所基金代码规则判断是否 ETF/LOF；指数、股票返回 False。

    上海：5 开头（510/512/513/515/516/518/560/562/563/588…）；
    深圳：15/16/18 开头（159/160/161/162/184…）。指数（000/399）与股票不在此列。
    """
    digits = code[2:] if code[:2] in {"sh", "sz", "bj"} else code
    if len(digits) != 6 or not digits.isdigit():
        return False
    if code.startswith("sh"):
        return digits.startswith("5")
    if code.startswith("sz"):
        return digits.startswith(("15", "16", "18"))
    return False


def kind_of(code: str) -> str:
    """契约中的 kind：``etf`` 或 ``stock``（指数也只能归到 stock）。"""
    return "etf" if is_etf_code(code) else "stock"


def entity_name(code: str, fallback: str | None = None) -> str:
    """离线兜底名称（腾讯名称缓存优先由调用方处理）。"""
    entity = ENTITIES.get(code)
    if entity is not None:
        return entity.name
    if fallback:
        return fallback
    for index_code, index_name in INDEX_QUOTES:
        if index_code == code:
            return index_name
    return code


def watchlist_default() -> list[dict[str, str]]:
    """首次启动写入默认自选。"""
    return [
        {"code": code, "kind": kind, "label": label}
        for code, kind, label in DEFAULT_WATCHLIST
    ]


def now() -> datetime:
    """当前 Asia/Shanghai 时间（固定 +08:00，避免 Windows 缺 tzdata）。"""
    return datetime.now(SHANGHAI_TZ)

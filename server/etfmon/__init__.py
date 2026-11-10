"""ETF 观察台后端（纯标准库）。

模块职责：
- :mod:`etfmon.config`  常量、实体表（universe）、阈值
- :mod:`etfmon.store`   数据目录、原子 JSON 读写、交易时段
- :mod:`etfmon.sources` 腾讯/东方财富取数与解析
- :mod:`etfmon.analytics` 国家队、热点、宽度等确定性计算
- :mod:`etfmon.collectors` 后台采集线程与日终任务
- :mod:`etfmon.api`     HTTP 路由与响应组装
- :mod:`etfmon.__main__` 命令行入口（serve/daily/probe/import-legacy/fixtures）
"""

from __future__ import annotations

from .config import APP_VERSION

__all__ = ["APP_VERSION"]
__version__ = APP_VERSION

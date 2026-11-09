"""测试路径设置：让 ``python -m pytest server/tests`` 能 import etfmon。

另外给整个测试会话一个默认临时 ``ETF_DATA``：即使某个用例忘了 patch 环境变量，
也不会把 auth.json / 采集数据写进真实的 ``server/data``。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# 进程级兜底数据目录（测试用例仍可各自 patch 覆盖）
_SESSION_DATA_DIR = tempfile.TemporaryDirectory(prefix="etfmon-tests-", ignore_cleanup_errors=True)
os.environ.setdefault("ETF_DATA", _SESSION_DATA_DIR.name)

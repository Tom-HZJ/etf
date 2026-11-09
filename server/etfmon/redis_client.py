"""最小 Redis RESP 客户端（纯标准库），用于 Authelia 会话存储的计数/吊销。

只实现本项目需要的命令：``AUTH``、``SELECT``、``PING``、``DBSIZE``、``FLUSHDB``。
连接/读写超时默认 2 秒；任何协议或网络错误统一抛 :class:`RedisError`。
"""

from __future__ import annotations

import logging
import socket
from typing import Any, Mapping

from . import config

LOG = logging.getLogger("etfmon.redis")


class RedisError(RuntimeError):
    """Redis 连接/协议/命令错误。"""


class RedisClient:
    """同一连接上顺序执行简单命令。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
        timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.password = password or None
        self.db = int(db)
        self.timeout = float(timeout)
        self._sock: socket.socket | None = None
        self._file: Any = None

    @classmethod
    def from_env(cls) -> "RedisClient":
        """按 ``ETF_REDIS_*`` 环境变量创建客户端。"""
        cfg = config.redis_config()
        return cls(
            host=str(cfg["host"]),
            port=int(cfg["port"]),  # type: ignore[arg-type]
            password=cfg["password"],  # type: ignore[arg-type]
            db=int(cfg["db"]),  # type: ignore[arg-type]
            timeout=float(cfg["timeout"]),  # type: ignore[arg-type]
        )

    # -- 生命周期 ------------------------------------------------------
    def __enter__(self) -> "RedisClient":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            self._file = self._sock.makefile("rb")
        except OSError as exc:
            self.close()
            raise RedisError(f"连接 Redis 失败 {self.host}:{self.port}：{exc}") from exc
        try:
            if self.password:
                self._command("AUTH", self.password)
            if self.db:
                self._command("SELECT", str(self.db))
        except RedisError:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        except OSError:
            pass
        self._file = None
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    # -- 协议 ----------------------------------------------------------
    @staticmethod
    def _encode(*parts: str) -> bytes:
        chunks = [f"*{len(parts)}\r\n".encode("utf-8")]
        for part in parts:
            raw = str(part).encode("utf-8")
            chunks.append(f"${len(raw)}\r\n".encode("utf-8"))
            chunks.append(raw + b"\r\n")
        return b"".join(chunks)

    def _read_reply(self, fileobj: Any) -> Any:
        line = fileobj.readline()
        if not line:
            raise RedisError("Redis 连接已关闭")
        prefix, payload = line[:1], line[1:].strip()
        if prefix == b"+":
            return payload.decode("utf-8", "replace")
        if prefix == b"-":
            raise RedisError(f"Redis 返回错误：{payload.decode('utf-8', 'replace')}")
        if prefix == b":":
            try:
                return int(payload)
            except ValueError as exc:
                raise RedisError(f"Redis 整数响应非法：{payload!r}") from exc
        if prefix == b"$":
            try:
                length = int(payload)
            except ValueError as exc:
                raise RedisError(f"Redis bulk 长度非法：{payload!r}") from exc
            if length < 0:
                return None
            data = fileobj.read(length)
            trailing = fileobj.read(2)
            if len(data) != length or trailing != b"\r\n":
                raise RedisError("Redis bulk 响应不完整")
            return data.decode("utf-8", "replace")
        if prefix == b"*":
            try:
                count = int(payload)
            except ValueError as exc:
                raise RedisError(f"Redis 数组长度非法：{payload!r}") from exc
            if count < 0:
                return None
            return [self._read_reply(fileobj) for _ in range(count)]
        raise RedisError(f"无法识别的 Redis 响应前缀：{prefix!r}")

    def _command(self, *parts: str) -> Any:
        self.connect()
        assert self._sock is not None and self._file is not None
        try:
            self._sock.sendall(self._encode(*parts))
            return self._read_reply(self._file)
        except OSError as exc:
            raise RedisError(f"Redis 通信失败：{exc}") from exc

    # -- 命令 ----------------------------------------------------------
    def ping(self) -> bool:
        return self._command("PING") == "PONG"

    def dbsize(self) -> int:
        value = self._command("DBSIZE")
        if not isinstance(value, int):
            raise RedisError(f"DBSIZE 返回非整数：{value!r}")
        return value

    def flushdb(self) -> str:
        value = self._command("FLUSHDB")
        return str(value)

    def auth(self, password: str) -> bool:
        return self._command("AUTH", password) == "OK"

    def scan_keys(self, match: str = "*", *, limit: int = 20000) -> list[str]:
        """用 SCAN 遍历匹配的键（不阻塞 Redis；最多返回 ``limit`` 个）。"""
        keys: list[str] = []
        cursor = "0"
        while True:
            reply = self._command("SCAN", cursor, "MATCH", match, "COUNT", "1000")
            if not isinstance(reply, list) or len(reply) != 2:
                raise RedisError(f"SCAN 响应非法：{reply!r}")
            cursor = str(reply[0])
            batch = reply[1] or []
            keys.extend(str(item) for item in batch)
            if cursor == "0" or len(keys) >= limit:
                break
        return keys[:limit]

    def count_session_keys(self, pattern: str | None = None) -> int:
        """统计 Authelia 会话键数量（先按配置前缀 SCAN，识别不到再宽松匹配）。"""
        target = pattern or config.redis_session_pattern()
        keys = self.scan_keys(target)
        if keys:
            return len(keys)
        # 前缀可能随 Authelia 版本/配置变化：兜底扫描全部键，按 key 名含 session 识别
        all_keys = self.scan_keys("*")
        return sum(1 for key in all_keys if "session" in key.lower())

    def command(self, *parts: str) -> Any:
        """执行任意命令（测试/扩展用）。"""
        return self._command(*parts)


def session_store_stats(password: str | None = None) -> tuple[int, "RedisClient"]:
    """连上 Redis 并返回 ``(DBSIZE, client)``；调用方负责 close（兼容旧调用）。"""
    cfg = config.redis_config()
    client = RedisClient(
        host=str(cfg["host"]),
        port=int(cfg["port"]),  # type: ignore[arg-type]
        password=password if password is not None else cfg["password"],  # type: ignore[arg-type]
        db=int(cfg["db"]),  # type: ignore[arg-type]
        timeout=float(cfg["timeout"]),  # type: ignore[arg-type]
    )
    try:
        size = client.dbsize()
    except RedisError:
        client.close()
        raise
    return size, client


def session_stats() -> tuple[int, int]:
    """返回 ``(Authelia 会话键数, Redis 总键数)``；dbsize 用于 revoke-all 的删除数。"""
    client = RedisClient.from_env()
    try:
        total = client.dbsize()
        active = client.count_session_keys()
        return active, total
    finally:
        client.close()


def revoke_all_sessions() -> int:
    """清空 Authelia 会话库，返回清空前的 key 数量（= 实际删除数）。"""
    client = RedisClient.from_env()
    try:
        before = client.dbsize()
        client.flushdb()
        LOG.warning("已吊销全部 Authelia 会话（%d 个 key）", before)
        return before
    finally:
        client.close()

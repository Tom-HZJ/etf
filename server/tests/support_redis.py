"""测试辅助：最小 RESP 协议假 Redis 服务（只支持 AUTH/SELECT/PING/DBSIZE/FLUSHDB）。"""

from __future__ import annotations

import fnmatch
import socketserver
import threading
from typing import Any


class _FakeRedisHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: dict[str, Any] = self.server.state  # type: ignore[attr-defined]
        while True:
            header = self.rfile.readline()
            if not header:
                return
            if not header.startswith(b"*"):
                return
            try:
                count = int(header[1:].strip())
            except ValueError:
                return
            args: list[str] = []
            for _ in range(count):
                length_line = self.rfile.readline()
                if not length_line.startswith(b"$"):
                    return
                length = int(length_line[1:].strip())
                data = self.rfile.read(length)
                self.rfile.read(2)  # trailing CRLF
                args.append(data.decode("utf-8", "replace"))
            if not args:
                return
            command = args[0].upper()
            response = b"-ERR unknown command\r\n"
            if command == "AUTH":
                if state.get("password") is None or args[1] == state["password"]:
                    response = b"+OK\r\n"
                else:
                    response = b"-ERR invalid password\r\n"
            elif command == "SELECT":
                response = b"+OK\r\n"
            elif command == "PING":
                response = b"+PONG\r\n"
            elif command == "DBSIZE":
                response = f":{len(state.get('keys', []))}\r\n".encode("utf-8")
            elif command == "FLUSHDB":
                state["keys"] = []
                response = b"+OK\r\n"
            elif command == "SCAN":
                response = self._scan(state, args)
            state.setdefault("commands", []).append(args)
            self.wfile.write(response)
            self.wfile.flush()

    @staticmethod
    def _scan(state: dict[str, Any], args: list[str]) -> bytes:
        """极简 SCAN：一次返回全部匹配键（cursor 恒为 0）。"""
        pattern = "*"
        rest = args[1:]
        for index, token in enumerate(rest):
            if token.upper() == "MATCH" and index + 1 < len(rest):
                pattern = rest[index + 1]
        keys = [key for key in state.get("keys", []) if fnmatch.fnmatch(key, pattern)]
        chunks = [b"*2\r\n", b"$1\r\n0\r\n", f"*{len(keys)}\r\n".encode("utf-8")]
        for key in keys:
            raw = key.encode("utf-8")
            chunks.append(f"${len(raw)}\r\n".encode("utf-8") + raw + b"\r\n")
        return b"".join(chunks)


class _FakeRedisServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeRedisServer:
    """上下文管理器：``with FakeRedisServer(keys=3, password="pw") as server:``。

    ``keys`` 为 int 时生成 ``authelia:session:<n>`` 键；也可传入显式 key 列表。
    """

    def __init__(self, keys: int | list[str] = 3, password: str | None = None) -> None:
        if isinstance(keys, int):
            key_list = [f"authelia:session:{index}" for index in range(keys)]
        else:
            key_list = list(keys)
        self.state: dict[str, Any] = {"keys": key_list, "password": password, "commands": []}
        self._server = _FakeRedisServer(("127.0.0.1", 0), _FakeRedisHandler)
        self._server.state = self.state  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def __enter__(self) -> "FakeRedisServer":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)

    @property
    def commands(self) -> list[list[str]]:
        return [list(item) for item in self.state.get("commands") or []]

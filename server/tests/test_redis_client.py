"""RESP 客户端单测：AUTH/DBSIZE/FLUSHDB/PING、错误与吊销。"""

from __future__ import annotations

import os
import socket
import unittest
from unittest import mock

from etfmon import config, redis_client

from tests.support_redis import FakeRedisServer


def free_port() -> int:
    """拿一个当前没有监听的端口（用于连接失败测试）。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class RedisClientTest(unittest.TestCase):
    def test_ping_dbsize_flushdb(self) -> None:
        with FakeRedisServer(keys=5) as server:
            with redis_client.RedisClient("127.0.0.1", server.port) as client:
                self.assertTrue(client.ping())
                self.assertEqual(client.dbsize(), 5)
                self.assertEqual(client.flushdb(), "OK")
                self.assertEqual(client.dbsize(), 0)

    def test_auth_success_and_failure(self) -> None:
        with FakeRedisServer(keys=1, password="secret") as server:
            with redis_client.RedisClient("127.0.0.1", server.port, password="secret") as client:
                self.assertEqual(client.dbsize(), 1)
            with self.assertRaises(redis_client.RedisError):
                redis_client.RedisClient("127.0.0.1", server.port, password="wrong").connect()

    def test_connection_refused(self) -> None:
        client = redis_client.RedisClient("127.0.0.1", free_port(), timeout=0.5)
        with self.assertRaises(redis_client.RedisError):
            client.dbsize()

    def test_scan_and_session_count(self) -> None:
        keys = ["authelia:session:a", "authelia:session:b", "authelia:other:1", "foo"]
        with FakeRedisServer(keys=keys) as server:
            with redis_client.RedisClient("127.0.0.1", server.port) as client:
                self.assertEqual(client.dbsize(), 4)
                self.assertEqual(
                    sorted(client.scan_keys("authelia:session:*")),
                    ["authelia:session:a", "authelia:session:b"],
                )
                self.assertEqual(client.count_session_keys(), 2)
                # 前缀识别不到时，兜底扫描全部键并按 key 名含 session 统计
                self.assertEqual(client.count_session_keys("other:counter:*"), 2)

    def test_session_stats(self) -> None:
        keys = ["authelia:session:a", "authelia:session:b", "authelia:other:1"]
        with FakeRedisServer(keys=keys) as server:
            with mock.patch.dict(os.environ, {"ETF_REDIS_PORT": str(server.port), "ETF_REDIS_PASSWORD": ""}):
                self.assertEqual(redis_client.session_stats(), (2, 3))

    def test_session_store_stats_and_revoke(self) -> None:
        import os

        with FakeRedisServer(keys=7) as server:
            with mock.patch.dict(os.environ, {"ETF_REDIS_PORT": str(server.port), "ETF_REDIS_PASSWORD": ""}):
                size, client = redis_client.session_store_stats()
                self.assertEqual(size, 7)
                client.close()
                revoked = redis_client.revoke_all_sessions()
                self.assertEqual(revoked, 7)
                size_after, client_after = redis_client.session_store_stats()
                client_after.close()
                self.assertEqual(size_after, 0)


if __name__ == "__main__":
    unittest.main()

"""client.py のテスト: 接続状態遷移と再接続。フェイク MatterClient で検証する。

v1 バグ B-2（connect 失敗時のクリーンアップで元例外が失われる）の回帰テストを含む。
"""

import asyncio

import pytest

from matter_exporter.client import MatterConnection, NotConnectedError


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeMatterClient:
    """python-matter-server MatterClient の必要最小限のフェイク"""

    def __init__(
        self,
        connect_error: Exception | None = None,
        disconnect_error: Exception | None = None,
        nodes=None,
    ):
        self.connect_error = connect_error
        self.disconnect_error = disconnect_error
        self.nodes = nodes or []
        self.disconnected = False
        self._listen_stop = asyncio.Event()

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error

    async def start_listening(self, init_ready: asyncio.Event | None = None):
        if init_ready is not None:
            init_ready.set()
        await self._listen_stop.wait()  # サーバー切断のシミュレーションは stop_listening()

    def stop_listening(self):
        """テストから「サーバー側切断」を起こす"""
        self._listen_stop.set()

    async def disconnect(self):
        self.disconnected = True
        if self.disconnect_error is not None:
            raise self.disconnect_error

    def get_nodes(self):
        return self.nodes


def make_connection(clients: list[FakeMatterClient], interval=0.01) -> MatterConnection:
    """呼び出しごとに clients から順にフェイクを払い出す factory を仕込む"""
    sessions: list[FakeSession] = []
    calls = {"count": 0}

    async def factory(url):
        client = clients[min(calls["count"], len(clients) - 1)]
        calls["count"] += 1
        session = FakeSession()
        sessions.append(session)
        return client, session

    connection = MatterConnection(
        "ws://fake:5580/ws", reconnect_interval=interval, client_factory=factory
    )
    connection.test_sessions = sessions  # type: ignore[attr-defined]
    return connection


async def wait_until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class TestInitialState:
    def test_not_connected_initially(self):
        connection = make_connection([FakeMatterClient()])
        assert connection.is_connected is False

    def test_get_nodes_raises_when_not_connected(self):
        connection = make_connection([FakeMatterClient()])
        with pytest.raises(NotConnectedError):
            connection.get_nodes()


class TestConnectionLifecycle:
    async def test_connects_and_returns_nodes(self):
        fake = FakeMatterClient(nodes=["node-sentinel"])
        connection = make_connection([fake])
        await connection.start()
        try:
            await wait_until(lambda: connection.is_connected)
            assert connection.get_nodes() == ["node-sentinel"]
        finally:
            await connection.stop()

    async def test_stop_disconnects_and_closes_session(self):
        fake = FakeMatterClient()
        connection = make_connection([fake])
        await connection.start()
        await wait_until(lambda: connection.is_connected)
        await connection.stop()
        assert connection.is_connected is False
        assert fake.disconnected is True
        assert all(s.closed for s in connection.test_sessions)

    async def test_reconnects_after_server_drop(self):
        first, second = FakeMatterClient(), FakeMatterClient(nodes=["after-reconnect"])
        connection = make_connection([first, second])
        await connection.start()
        try:
            await wait_until(lambda: connection.is_connected)
            first.stop_listening()  # サーバー側切断
            await wait_until(
                lambda: connection.is_connected and connection.get_nodes() == ["after-reconnect"]
            )
        finally:
            await connection.stop()

    async def test_retries_after_connect_failure(self):
        failing = FakeMatterClient(connect_error=ConnectionRefusedError("refused"))
        working = FakeMatterClient(nodes=["ok"])
        connection = make_connection([failing, working])
        await connection.start()
        try:
            await wait_until(lambda: connection.is_connected)
            assert connection.get_nodes() == ["ok"]
        finally:
            await connection.stop()


class TestConnectOnceErrorHandling:
    async def test_original_error_preserved_over_cleanup_error(self):
        """B-2 回帰: disconnect() が二次例外を投げても、元の接続エラーが伝播する"""
        fake = FakeMatterClient(
            connect_error=ConnectionRefusedError("原因はこちら"),
            disconnect_error=ValueError("クリーンアップの二次エラー"),
        )
        connection = make_connection([fake])
        with pytest.raises(ConnectionRefusedError, match="原因はこちら"):
            await connection.connect_once()
        assert connection.is_connected is False

    async def test_session_closed_after_connect_failure(self):
        fake = FakeMatterClient(connect_error=RuntimeError("boom"))
        connection = make_connection([fake])
        with pytest.raises(RuntimeError):
            await connection.connect_once()
        assert all(s.closed for s in connection.test_sessions)

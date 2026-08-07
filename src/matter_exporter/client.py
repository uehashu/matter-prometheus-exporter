"""Matter Server との接続管理（自動再接続つき）。

設計: docs/design-v2.md 3.4節
- v1 の MatterElectricalMetrics + manage_matter_connection を 1 クラスに統合
- is_connected を公開プロパティにする（プライベート属性への外部アクセスを排除）
- 接続失敗時、クリーンアップの二次例外で元の例外を失わない
- start_listening の初期ロード完了（init_ready）を待ってから接続完了とみなす
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from matter_server.client import MatterClient
from matter_server.client.models.node import MatterNode

logger = logging.getLogger(__name__)

# (url) -> (MatterClient 互換, セッション互換) を返すファクトリ。テストではフェイクを注入する
ClientFactory = Callable[[str], Awaitable[tuple[Any, Any]]]


class NotConnectedError(ConnectionError):
    """Matter Server に接続されていない"""


async def _default_client_factory(url: str) -> tuple[MatterClient, aiohttp.ClientSession]:
    session = aiohttp.ClientSession()
    return MatterClient(url, session), session


class MatterConnection:
    def __init__(
        self,
        ws_url: str,
        reconnect_interval: float = 10.0,
        *,
        client_factory: ClientFactory | None = None,
    ):
        self._ws_url = ws_url
        self._reconnect_interval = reconnect_interval
        self._client_factory = client_factory or _default_client_factory

        self._client: Any = None
        self._session: Any = None
        self._listen_task: asyncio.Task | None = None
        self._manager_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return (
            self._client is not None
            and self._listen_task is not None
            and not self._listen_task.done()
        )

    def get_nodes(self) -> list[MatterNode]:
        if not self.is_connected:
            raise NotConnectedError("Matter Server に接続されていません")
        return self._client.get_nodes()

    async def start(self) -> None:
        """バックグラウンドの接続管理タスクを開始する"""
        self._manager_task = asyncio.create_task(self._manage())

    async def stop(self) -> None:
        """グレースフルに停止する（切断・セッションクローズを含む)"""
        self._stop_event.set()
        if self._manager_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._manager_task
            self._manager_task = None
        await self._teardown()

    async def connect_once(self) -> None:
        """1 回だけ接続を試みる。失敗時は必ず元の例外を伝播する"""
        client = session = None
        try:
            client, session = await self._client_factory(self._ws_url)
            await client.connect()

            init_ready: asyncio.Event = asyncio.Event()
            listen_task = asyncio.create_task(client.start_listening(init_ready))
            ready_waiter = asyncio.create_task(init_ready.wait())
            done, _ = await asyncio.wait(
                {listen_task, ready_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if listen_task in done:
                # 初期ロード前にリスニングが終了した = 接続失敗扱い
                ready_waiter.cancel()
                raise listen_task.exception() or ConnectionError(
                    "リスニングタスクが即時終了しました"
                )

            self._client, self._session, self._listen_task = client, session, listen_task
        except BaseException:
            # クリーンアップの二次例外で元の例外を失わない
            await self._cleanup_quietly(client, session)
            raise

    async def _manage(self) -> None:
        while not self._stop_event.is_set():
            try:
                logger.info("Matter Server へ接続を試行中: %s", self._ws_url)
                await self.connect_once()
                logger.info("Matter Server に接続しました")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Matter Server 接続失敗: %s", e)
                await self._wait_reconnect_interval()
                continue

            # 接続維持: リスニング終了（切断）または停止指示まで待つ
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            try:
                await asyncio.wait(
                    {self._listen_task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                stop_waiter.cancel()
            await self._teardown()

            if not self._stop_event.is_set():
                logger.warning(
                    "Matter Server 接続が切断されました。%s 秒後に再接続します",
                    self._reconnect_interval,
                )
                await self._wait_reconnect_interval()

    async def _wait_reconnect_interval(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=self._reconnect_interval)

    async def _teardown(self) -> None:
        listen_task, client, session = self._listen_task, self._client, self._session
        self._listen_task = self._client = self._session = None
        if listen_task is not None and not listen_task.done():
            listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await listen_task
        await self._cleanup_quietly(client, session)

    @staticmethod
    async def _cleanup_quietly(client: Any, session: Any) -> None:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()

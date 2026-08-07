"""エントリポイント: python -m matter_exporter

- 環境変数の検証に失敗した場合は終了コード 2 で明確なメッセージを出す
- シグナル（SIGTERM/SIGINT）は loop.add_signal_handler で受けてグレースフル終了する
- uvloop が利用可能なら使用する
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiohttp import web

from .client import MatterConnection
from .config import Config, ConfigError, NameResolver, load_config
from .server import create_app

logger = logging.getLogger("matter_exporter")


async def run(config: Config) -> None:
    connection = MatterConnection(config.matter_ws_url, config.reconnect_interval)
    resolver = NameResolver(config.names_file)
    app = create_app(connection, resolver, config)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.prometheus_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await site.start()
        await connection.start()
        logger.info("Matter Prometheus Exporter %s 起動", _version())
        logger.info("Matter Server: %s", config.matter_ws_url)
        logger.info(
            "HTTP: http://0.0.0.0:%s (/metrics /health /devices)", config.prometheus_port
        )
        await stop_event.wait()
        logger.info("シャットダウンします")
    finally:
        await connection.stop()
        await runner.cleanup()


def _version() -> str:
    from . import __version__

    return __version__


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv

    try:
        config = load_config()
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        raise SystemExit(2) from None

    if "--check-config" in argv:
        print("設定 OK")
        raise SystemExit(0)

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        import uvloop
    except ImportError:
        asyncio.run(run(config))
    else:
        uvloop.run(run(config))


if __name__ == "__main__":
    main()

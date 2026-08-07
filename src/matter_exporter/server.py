"""HTTP エンドポイント: /metrics /health /devices。

設計: docs/design-v2.md 3.5節
- /metrics: Matter 未接続・取得失敗時は 503（Prometheus 側で up==0 になる）
- /health: プロセス生存確認。常に 200
- /devices: シールと突き合わせて names.yaml を書くための発見用 JSON
"""

from __future__ import annotations

import logging
from typing import Protocol

from aiohttp import web
from matter_server.client.models.node import MatterNode

from .collectors import COLLECTORS, render_metrics
from .config import Config, NameResolver
from .identity import build_identity, node_device_id, sanitize

logger = logging.getLogger(__name__)

_CONNECTION_KEY = web.AppKey("connection")
_RESOLVER_KEY = web.AppKey("resolver")
_CONFIG_KEY = web.AppKey("config")


class Connection(Protocol):
    """server が必要とする Matter 接続のインターフェース（テストではフェイクを注入）"""

    @property
    def is_connected(self) -> bool: ...

    def get_nodes(self) -> list[MatterNode]: ...


def create_app(connection: Connection, resolver: NameResolver, config: Config) -> web.Application:
    app = web.Application()
    app[_CONNECTION_KEY] = connection
    app[_RESOLVER_KEY] = resolver
    app[_CONFIG_KEY] = config
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/devices", handle_devices)
    return app


async def handle_metrics(request: web.Request) -> web.Response:
    connection = request.app[_CONNECTION_KEY]
    if not connection.is_connected:
        return web.Response(
            status=503, text="# Matter Server not connected\n", content_type="text/plain"
        )
    try:
        nodes = connection.get_nodes()
        output = render_metrics(nodes, request.app[_RESOLVER_KEY])
    except Exception as e:
        logger.error("メトリクス生成に失敗: %s", e)
        return web.Response(status=503, text=f"# Error: {e}\n", content_type="text/plain")
    return web.Response(body=output, content_type="text/plain")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "healthy",
            "matter_connected": request.app[_CONNECTION_KEY].is_connected,
            "reconnect_interval": request.app[_CONFIG_KEY].reconnect_interval,
        }
    )


async def handle_devices(request: web.Request) -> web.Response:
    connection = request.app[_CONNECTION_KEY]
    if not connection.is_connected:
        return web.json_response({"error": "Matter Server not connected"}, status=503)
    try:
        nodes = connection.get_nodes()
        listing = build_device_listing(nodes, request.app[_RESOLVER_KEY])
    except Exception as e:
        logger.error("デバイス一覧の生成に失敗: %s", e)
        return web.json_response({"error": str(e)}, status=503)
    return web.json_response(listing)


def build_device_listing(nodes: list[MatterNode], resolver: NameResolver) -> list[dict]:
    """names.yaml を書くための発見用一覧。オフライン機も構成ごと列挙する"""
    listing = []
    for node in nodes:
        info = node.device_info
        endpoints = []
        for endpoint in node.endpoints.values():
            matched = [c for c in COLLECTORS if endpoint.has_cluster(c.cluster)]
            if not matched:
                continue
            identity = build_identity(endpoint)
            endpoints.append(
                {
                    "endpoint": identity.endpoint_id,
                    "sensor_types": sorted({c.sensor_type for c in matched}),
                    "name": resolver.resolve(
                        identity.device, identity.serial, identity.endpoint_id
                    ),
                }
            )
        listing.append(
            {
                "device": node_device_id(node),
                "serial": sanitize(getattr(info, "serialNumber", None)),
                "vendor": sanitize(getattr(info, "vendorName", None)),
                "product": sanitize(getattr(info, "productName", None)),
                "node_label": sanitize(getattr(info, "nodeLabel", None)),
                "available": node.available,
                "endpoints": endpoints,
            }
        )
    return listing

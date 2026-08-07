"""server.py のテスト: /metrics /health /devices。

Matter クライアントはフェイクを注入し、ネットワークは使わない。
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from matter_exporter.config import NameResolver, load_config
from matter_exporter.server import create_app


class FakeConnection:
    def __init__(self, nodes=None, connected=True, error=None):
        self._nodes = nodes or []
        self._connected = connected
        self._error = error

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_nodes(self):
        if self._error is not None:
            raise self._error
        return self._nodes


@pytest.fixture
def config():
    return load_config(env={"MATTER_RECONNECT_INTERVAL": "10"})


@pytest.fixture
async def client_factory():
    clients: list[TestClient] = []

    async def factory(connection, config, resolver=None) -> TestClient:
        app = create_app(connection, resolver or NameResolver(None), config)
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()


class TestMetricsEndpoint:
    async def test_returns_exposition_when_connected(self, plugs, config, client_factory):
        client = await client_factory(FakeConnection(nodes=plugs), config)
        response = await client.get("/metrics")
        assert response.status == 200
        assert "text/plain" in response.content_type
        body = await response.text()
        assert "matter_active_power_watts" in body
        assert "matter_endpoint_info" in body

    async def test_503_when_not_connected(self, config, client_factory):
        client = await client_factory(FakeConnection(connected=False), config)
        response = await client.get("/metrics")
        assert response.status == 503

    async def test_503_when_get_nodes_fails(self, config, client_factory):
        client = await client_factory(
            FakeConnection(error=RuntimeError("boom")), config
        )
        response = await client.get("/metrics")
        assert response.status == 503

    async def test_names_reflected(self, power_strip, config, client_factory, tmp_path):
        path = tmp_path / "names.yaml"
        path.write_text(
            'devices:\n  - serial: "AABBCCFF0001"\n    name: "タップ"\n',
            encoding="utf-8",
        )
        client = await client_factory(
            FakeConnection(nodes=power_strip), config, NameResolver(path)
        )
        body = await (await client.get("/metrics")).text()
        assert 'name="タップ"' in body


class TestHealthEndpoint:
    async def test_healthy_when_connected(self, config, client_factory):
        client = await client_factory(FakeConnection(), config)
        response = await client.get("/health")
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "healthy"
        assert data["matter_connected"] is True
        assert data["reconnect_interval"] == 10.0

    async def test_still_200_when_disconnected(self, config, client_factory):
        """ヘルスチェックはプロセス生存確認: Matter 切断中も 200"""
        client = await client_factory(FakeConnection(connected=False), config)
        response = await client.get("/health")
        assert response.status == 200
        assert (await response.json())["matter_connected"] is False


class TestDevicesEndpoint:
    async def test_lists_devices_with_endpoints(self, plugs, config, client_factory):
        client = await client_factory(FakeConnection(nodes=plugs), config)
        response = await client.get("/devices")
        assert response.status == 200
        devices = await response.json()
        assert len(devices) == 3

        node3 = next(n for n in plugs if n.node_id == 3)
        entry = next(d for d in devices if d["device"] == node3.device_info.uniqueID)
        assert entry["serial"] == node3.device_info.serialNumber
        assert entry["vendor"] == "Tapo"
        assert entry["available"] is True
        assert entry["endpoints"] == [
            {
                "endpoint": 1,
                "sensor_types": ["power"],
                "name": node3.device_info.serialNumber,  # 未命名 → serial フォールバック
            }
        ]

    async def test_offline_device_listed_with_available_false(
        self, plugs, config, client_factory
    ):
        """発見用途: オフライン機も endpoint 構成ごと列挙される"""
        client = await client_factory(FakeConnection(nodes=plugs), config)
        devices = await (await client.get("/devices")).json()
        offline = next(n for n in plugs if not n.available)
        entry = next(d for d in devices if d["device"] == offline.device_info.uniqueID)
        assert entry["available"] is False
        assert entry["endpoints"]  # 属性キャッシュから構成は分かる

    async def test_503_when_not_connected(self, config, client_factory):
        client = await client_factory(FakeConnection(connected=False), config)
        response = await client.get("/devices")
        assert response.status == 503

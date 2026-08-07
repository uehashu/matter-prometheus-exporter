"""テスト共通のフィクスチャローダー。

tests/fixtures/*.json は tools/dump_nodes.py --json と同じ形式（匿名化済み）。
これを python-matter-server の MatterNode に組み立てて、実物と同じ
データ構造に対してテストする。ネットワークは一切使わない。
"""

import json
from pathlib import Path

import pytest
from matter_server.client.models.node import MatterNode
from matter_server.common.helpers.util import dataclass_from_dict
from matter_server.common.models import MatterNodeData

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[MatterNode]:
    """フィクスチャ JSON を MatterNode のリストに組み立てる"""
    data = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return [MatterNode(dataclass_from_dict(MatterNodeData, n)) for n in data["nodes"]]


@pytest.fixture
def plugs() -> list[MatterNode]:
    """1口プラグ×3（オンライン2台 + オフライン1台、NodeLabel は 'Test Name'/空/空）"""
    return load_fixture("plugs")


@pytest.fixture
def bridge() -> list[MatterNode]:
    """ブリッジ1台（配下に UniqueID 欠落・ヌル文字埋めエンドポイントを含む）"""
    return load_fixture("bridge")


@pytest.fixture
def power_strip() -> list[MatterNode]:
    """合成の電源タップ1台（口×3 = SET topology、合計×1 = NODE topology）"""
    return load_fixture("power_strip")

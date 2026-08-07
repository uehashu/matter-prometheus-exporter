"""フィクスチャ自体の妥当性検証。

フィクスチャが設計 (docs/design-v2.md 5.2節) の要件を満たしていること、
および実データ（MAC・実IP・実シリアル）が混入していないことを機械検証する。
"""

import json
import re

from chip.clusters import Objects as Clusters

from .conftest import FIXTURES_DIR, load_fixture

# 実機由来の識別子パターン（漏洩チェック用）
REAL_DATA_PATTERNS = [
    r"B8FBB3",  # 実機 Tapo プラグの MAC/シリアル prefix
    r"6C4CBC",
    r"ACA7F1",
    r"385610",
    r"192\.168\.",
    r"延岡",
]


class TestPlugsFixture:
    def test_loads_as_matter_nodes(self):
        nodes = load_fixture("plugs")
        assert len(nodes) == 3

    def test_contains_online_and_offline(self):
        nodes = load_fixture("plugs")
        availability = sorted(n.available for n in nodes)
        assert availability == [False, True, True]

    def test_online_plugs_have_epm_cluster(self):
        nodes = load_fixture("plugs")
        for node in nodes:
            assert node.has_cluster(Clusters.ElectricalPowerMeasurement)

    def test_node_labels_cover_named_and_empty(self):
        nodes = load_fixture("plugs")
        labels = {n.device_info.nodeLabel for n in nodes}
        assert "" in labels  # 空ラベル（実機で6台該当した状況の再現）
        assert any(label for label in labels)  # 非空ラベルも1つ以上

    def test_unique_ids_are_unique(self):
        nodes = load_fixture("plugs")
        uids = [n.device_info.uniqueID for n in nodes]
        assert len(set(uids)) == len(uids)


class TestBridgeFixture:
    def test_bridge_node_flagged(self):
        nodes = load_fixture("bridge")
        assert len(nodes) == 1
        assert nodes[0].is_bridge_device

    def test_has_bridged_endpoint_without_unique_id(self):
        """ブリッジ配下は UniqueID が空 — device フォールバックの実データ再現"""
        node = load_fixture("bridge")[0]
        bridged = [ep for ep in node.endpoints.values() if ep.is_bridged_device]
        assert bridged
        assert all(not ep.device_info.uniqueID for ep in bridged)

    def test_has_null_byte_padded_strings(self):
        """ヌル文字埋め文字列 — サニタイズ対象の実データ再現"""
        node = load_fixture("bridge")[0]
        serials = [
            ep.device_info.serialNumber
            for ep in node.endpoints.values()
            if ep.is_bridged_device
        ]
        assert any("\x00" in (s or "") for s in serials)


class TestPowerStripFixture:
    def test_multiple_epm_endpoints_on_one_node(self):
        """1ノードに複数の測定エンドポイント（v1 バグ A-1 の前提条件）"""
        node = load_fixture("power_strip")[0]
        epm_endpoints = [
            ep.endpoint_id
            for ep in node.endpoints.values()
            if ep.has_cluster(Clusters.ElectricalPowerMeasurement)
        ]
        assert len(epm_endpoints) >= 3

    def test_has_both_set_and_node_topologies(self):
        """口別（SET）と合計（NODE）の両トポロジを含む"""
        node = load_fixture("power_strip")[0]
        feature_maps = {
            ep.get_cluster(Clusters.PowerTopology).featureMap
            for ep in node.endpoints.values()
            if ep.has_cluster(Clusters.PowerTopology)
        }
        assert 0b0100 in feature_maps  # SET
        assert 0b0001 in feature_maps  # NODE

    def test_an_endpoint_lacks_rms_current(self):
        """属性が未報告（None）のケースを含む — None ハンドリングの検証用"""
        node = load_fixture("power_strip")[0]
        values = [
            ep.get_attribute_value(
                Clusters.ElectricalPowerMeasurement,
                Clusters.ElectricalPowerMeasurement.Attributes.RMSCurrent,
            )
            for ep in node.endpoints.values()
            if ep.has_cluster(Clusters.ElectricalPowerMeasurement)
        ]
        assert None in values

    def test_has_cumulative_energy(self):
        node = load_fixture("power_strip")[0]
        eem_values = [
            ep.get_cluster(Clusters.ElectricalEnergyMeasurement).cumulativeEnergyImported
            for ep in node.endpoints.values()
            if ep.has_cluster(Clusters.ElectricalEnergyMeasurement)
        ]
        assert any(v is not None and v.energy > 0 for v in eem_values)


class TestNoRealDataLeakage:
    def test_fixtures_contain_no_real_identifiers(self):
        for path in FIXTURES_DIR.glob("*.json"):
            text = path.read_text()
            for pattern in REAL_DATA_PATTERNS:
                assert not re.search(pattern, text, re.IGNORECASE), (
                    f"{path.name} に実データらしきパターン {pattern} が含まれています"
                )

    def test_fixtures_contain_no_credential_clusters(self):
        """Access Control(31) / Operational Credentials(62) 等が落ちていること"""
        forbidden_clusters = {31, 62, 63, 48, 49}
        for path in FIXTURES_DIR.glob("*.json"):
            data = json.loads(path.read_text())
            for node in data["nodes"]:
                for attr_path in node["attributes"]:
                    cluster_id = int(attr_path.split("/")[1])
                    assert cluster_id not in forbidden_clusters, (
                        f"{path.name}: 資格情報クラスタ {cluster_id} が残っています"
                    )

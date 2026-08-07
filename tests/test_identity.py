"""identity.py のテスト: サニタイズ・device フォールバック連鎖・メタデータ導出"""

from matter_exporter.identity import build_identity, node_device_id, sanitize


class TestSanitize:
    def test_none_stays_none(self):
        assert sanitize(None) is None

    def test_empty_string_becomes_none(self):
        assert sanitize("") is None

    def test_null_byte_padding_becomes_none(self):
        """実機ブリッジで確認されたヌル文字埋め文字列"""
        assert sanitize("\x00" * 24) is None

    def test_whitespace_only_becomes_none(self):
        assert sanitize("   ") is None

    def test_normal_string_unchanged(self):
        assert sanitize("Smart Wi-Fi Plug") == "Smart Wi-Fi Plug"

    def test_embedded_null_bytes_are_stripped(self):
        assert sanitize("abc\x00\x00") == "abc"


class TestPlugIdentity:
    def test_device_is_unique_id(self, plugs):
        node = next(n for n in plugs if n.node_id == 3)
        identity = build_identity(node.endpoints[1])
        assert identity.device == node.device_info.uniqueID
        assert identity.endpoint_id == 1

    def test_metadata_extracted(self, plugs):
        node = next(n for n in plugs if n.node_id == 3)
        identity = build_identity(node.endpoints[1])
        assert identity.vendor == "Tapo"
        assert identity.product == "Smart Wi-Fi Plug"
        assert identity.serial == node.device_info.serialNumber

    def test_topology_is_set(self, plugs):
        node = next(n for n in plugs if n.node_id == 3)
        assert build_identity(node.endpoints[1]).topology == "SET"

    def test_device_types(self, plugs):
        node = next(n for n in plugs if n.node_id == 3)
        # Electrical Sensor (0x0510) + On/Off Plug-in Unit (0x010a)
        assert build_identity(node.endpoints[1]).device_types == "0x010a,0x0510"


class TestPowerStripIdentity:
    def test_all_endpoints_share_device(self, power_strip):
        node = power_strip[0]
        devices = {
            build_identity(node.endpoints[ep]).device for ep in (1, 2, 3, 4)
        }
        assert devices == {"F1C7FFFF00000001"}

    def test_total_endpoint_topology_is_node(self, power_strip):
        node = power_strip[0]
        assert build_identity(node.endpoints[4]).topology == "NODE"


class TestBridgedIdentity:
    def test_missing_unique_id_falls_back_to_serial(self, bridge):
        """ブリッジ配下: UniqueID='' → serial にフォールバック"""
        node = bridge[0]
        ep6 = node.endpoints[6]
        assert not ep6.device_info.uniqueID  # 前提: フィクスチャは UniqueID 空
        identity = build_identity(ep6)
        assert identity.device == ep6.device_info.serialNumber
        assert identity.device  # 非空

    def test_null_serial_falls_back_to_parent_uid_and_endpoint(self, bridge):
        """ブリッジ配下: UniqueID='' かつ serial がヌル文字 → 親UID-epN"""
        node = bridge[0]
        parent_uid = node.device_info.uniqueID
        identity = build_identity(node.endpoints[7])
        assert identity.device == f"{parent_uid}-ep7"

    def test_null_padded_product_sanitized(self, bridge):
        node = bridge[0]
        identity = build_identity(node.endpoints[7])
        assert identity.product is None

    def test_no_power_topology_yields_empty_string(self, bridge):
        node = bridge[0]
        assert build_identity(node.endpoints[6]).topology == ""


class TestNodeDeviceId:
    def test_node_device_id_is_unique_id(self, plugs):
        for node in plugs:
            assert node_device_id(node) == node.device_info.uniqueID

"""collectors.py のテスト: メトリクス生成の中核。

v1 の既知バグの回帰テストを含む:
- A-1: 複数口機器で unique_id ラベルが衝突し、最後の endpoint の値だけが残る
- A-2: オフラインノードで未定義変数を参照し /metrics 全体が落ちる
"""

import pytest
from prometheus_client.parser import text_string_to_metric_families

from matter_exporter.collectors import build_metric_families, render_metrics
from matter_exporter.config import NameResolver


@pytest.fixture
def resolver():
    """names.yaml なし（フォールバック動作）"""
    return NameResolver(None)


def samples_of(families, metric_name):
    """メトリクス名に一致する全サンプルを (labels, value) のリストで返す"""
    result = []
    for family in families:
        for sample in family.samples:
            if sample.name == metric_name:
                result.append((sample.labels, sample.value))
    return result


class TestPlugMetrics:
    def test_power_converted_to_watts(self, plugs, resolver):
        families = build_metric_families(plugs, resolver)
        node = next(n for n in plugs if n.node_id == 3)
        device = node.device_info.uniqueID
        values = {
            labels["device"]: value
            for labels, value in samples_of(families, "matter_active_power_watts")
        }
        assert values[device] == pytest.approx(17.647)  # フィクスチャの 17647 mW

    def test_voltage_and_current_converted(self, plugs, resolver):
        families = build_metric_families(plugs, resolver)
        node = next(n for n in plugs if n.node_id == 3)
        device = node.device_info.uniqueID
        voltage = {
            labels["device"]: value
            for labels, value in samples_of(families, "matter_rms_voltage_volts")
        }
        current = {
            labels["device"]: value
            for labels, value in samples_of(families, "matter_rms_current_amps")
        }
        assert voltage[device] == pytest.approx(96.395)
        assert current[device] == pytest.approx(0.330)

    def test_energy_counter_in_watt_hours(self, plugs, resolver):
        families = build_metric_families(plugs, resolver)
        node = next(n for n in plugs if n.node_id == 3)
        device = node.device_info.uniqueID
        energy = {
            (labels["device"], labels["direction"]): value
            for labels, value in samples_of(families, "matter_energy_watt_hours_total")
        }
        assert energy[(device, "import")] == pytest.approx(60513.0)  # 60513000 mWh

    def test_offline_node_yields_no_value_metrics(self, plugs, resolver):
        """A-2 回帰: オフラインノードは例外を起こさず、値メトリクス（古い値）を出さない"""
        families = build_metric_families(plugs, resolver)
        offline = next(n for n in plugs if not n.available)
        device = offline.device_info.uniqueID

        availability = dict(
            (labels["device"], value)
            for labels, value in samples_of(families, "matter_node_available")
        )
        assert availability[device] == 0

        for metric in (
            "matter_active_power_watts",
            "matter_rms_voltage_volts",
            "matter_rms_current_amps",
            "matter_energy_watt_hours_total",
        ):
            devices = [labels["device"] for labels, _ in samples_of(families, metric)]
            assert device not in devices

    def test_offline_node_still_yields_info(self, plugs, resolver):
        """オフライン中も info（メタデータ）は出し続ける。

        Grafana の join を @ end()（現在時点の名前）に固定しているため、
        info が消えるとオフライン機の過去の履歴までグラフから消えてしまう。
        """
        families = build_metric_families(plugs, resolver)
        offline = next(n for n in plugs if not n.available)
        device = offline.device_info.uniqueID

        info = {
            labels["device"]: labels
            for labels, _ in samples_of(families, "matter_endpoint_info")
        }
        assert device in info
        labels = info[device]
        assert labels["endpoint"] == "1"
        assert labels["name"] == offline.device_info.serialNumber  # 未命名 → serial
        assert labels["sensor_types"] == "power"

    def test_online_nodes_available_is_one(self, plugs, resolver):
        families = build_metric_families(plugs, resolver)
        online_devices = {n.device_info.uniqueID for n in plugs if n.available}
        availability = {
            labels["device"]: value
            for labels, value in samples_of(families, "matter_node_available")
        }
        assert all(availability[d] == 1 for d in online_devices)


class TestPowerStripMetrics:
    def test_each_outlet_has_independent_series(self, power_strip, resolver):
        """A-1 回帰: 複数口の値が endpoint ラベルで独立し、上書きされない"""
        families = build_metric_families(power_strip, resolver)
        values = {
            labels["endpoint"]: value
            for labels, value in samples_of(families, "matter_active_power_watts")
        }
        assert values == {
            "1": pytest.approx(10.5),
            "2": pytest.approx(20.0),
            "3": pytest.approx(0.0),
            "4": pytest.approx(30.5),  # 合計値エンドポイント
        }

    def test_missing_attribute_yields_no_sample(self, power_strip, resolver):
        """口3 は RMSCurrent 未報告 → サンプルを出さない（0 や NaN にしない）"""
        families = build_metric_families(power_strip, resolver)
        endpoints = [
            labels["endpoint"]
            for labels, _ in samples_of(families, "matter_rms_current_amps")
        ]
        assert "3" not in endpoints
        assert set(endpoints) == {"1", "2", "4"}

    def test_info_topology_distinguishes_total_endpoint(self, power_strip, resolver):
        families = build_metric_families(power_strip, resolver)
        topologies = {
            labels["endpoint"]: labels["topology"]
            for labels, _ in samples_of(families, "matter_endpoint_info")
        }
        assert topologies == {"1": "SET", "2": "SET", "3": "SET", "4": "NODE"}

    def test_info_is_unique_per_device_endpoint(self, power_strip, resolver):
        families = build_metric_families(power_strip, resolver)
        keys = [
            (labels["device"], labels["endpoint"])
            for labels, _ in samples_of(families, "matter_endpoint_info")
        ]
        assert len(keys) == len(set(keys)) == 4

    def test_sensor_types_is_power(self, power_strip, resolver):
        families = build_metric_families(power_strip, resolver)
        for labels, _ in samples_of(families, "matter_endpoint_info"):
            assert labels["sensor_types"] == "power"


class TestNameResolution:
    def test_names_yaml_reflected_in_info(self, power_strip, tmp_path):
        path = tmp_path / "names.yaml"
        path.write_text(
            "devices:\n"
            '  - serial: "AABBCCFF0001"\n'
            '    name: "リビング電源タップ"\n'
            "    endpoints:\n"
            '      1: "テレビ"\n',
            encoding="utf-8",
        )
        families = build_metric_families(power_strip, NameResolver(path))
        names = {
            labels["endpoint"]: labels["name"]
            for labels, _ in samples_of(families, "matter_endpoint_info")
        }
        assert names["1"] == "テレビ"
        assert names["2"] == "リビング電源タップ"  # endpoint 指定なし → 機器名

    def test_unnamed_device_falls_back_to_serial(self, plugs, resolver):
        families = build_metric_families(plugs, resolver)
        node = next(n for n in plugs if n.node_id == 3)
        names = {
            labels["device"]: labels["name"]
            for labels, _ in samples_of(families, "matter_endpoint_info")
        }
        assert names[node.device_info.uniqueID] == node.device_info.serialNumber


class TestBridgeMetrics:
    def test_bridge_without_epm_yields_only_availability(self, bridge, resolver):
        """測定クラスタを持たないブリッジは info も値も出さない"""
        families = build_metric_families(bridge, resolver)
        assert samples_of(families, "matter_node_available")
        assert not samples_of(families, "matter_active_power_watts")
        assert not samples_of(families, "matter_endpoint_info")


class TestRenderMetrics:
    def test_output_parses_as_prometheus_exposition(self, plugs, power_strip, resolver):
        """出力を prometheus_client のパーサで往復させて形式を機械検証する"""
        output = render_metrics(plugs + power_strip, resolver).decode()
        parsed = {f.name for f in text_string_to_metric_families(output)}
        assert {
            "matter_active_power_watts",
            "matter_rms_voltage_volts",
            "matter_rms_current_amps",
            "matter_energy_watt_hours",  # counter は _total サフィックスが剥がれた family 名
            "matter_node_available",
            "matter_endpoint_info",
        } <= parsed

    def test_output_contains_no_python_runtime_metrics(self, plugs, resolver):
        """専用レジストリにより python_gc_* / process_* が混入しない"""
        output = render_metrics(plugs, resolver).decode()
        assert "python_gc" not in output
        assert "process_" not in output

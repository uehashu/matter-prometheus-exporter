"""クラスタ → Prometheus メトリクスの変換。

設計: docs/design-v2.md 1節・3.3節
- スクレイプごとにメトリクスファミリを新規生成する custom collector 方式。
  状態を持たないため clear()/set() の並行競合が起きず、消えた機器の系列も残らない
- 値メトリクスのラベルは (device, endpoint) のみ。可変メタデータは
  matter_endpoint_info に分離する
- オフラインノードは matter_node_available=0 のみを出す
- 新しいセンサ種別への対応はコレクタークラスを 1 つ追加して COLLECTORS に登録する
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from chip.clusters import Objects as Clusters
from matter_server.client.models.node import MatterEndpoint, MatterNode
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from .config import NameResolver
from .identity import build_identity, node_device_id


@dataclass(frozen=True)
class Sample:
    metric: str  # ファミリのキー（_FAMILY_SPECS に一致させる）
    value: float
    extra_labels: dict[str, str] = field(default_factory=dict)


class ElectricalPowerCollector:
    """ElectricalPowerMeasurement (0x0090): 瞬時値。mW/mV/mA → W/V/A"""

    cluster = Clusters.ElectricalPowerMeasurement
    sensor_type = "power"

    _ATTRIBUTES = (
        (Clusters.ElectricalPowerMeasurement.Attributes.ActivePower, "matter_active_power_watts"),
        (Clusters.ElectricalPowerMeasurement.Attributes.RMSVoltage, "matter_rms_voltage_volts"),
        (Clusters.ElectricalPowerMeasurement.Attributes.RMSCurrent, "matter_rms_current_amps"),
    )

    def collect(self, endpoint: MatterEndpoint) -> Iterator[Sample]:
        for attribute, metric in self._ATTRIBUTES:
            raw = endpoint.get_attribute_value(self.cluster, attribute)
            if raw is not None:
                yield Sample(metric, raw / 1000.0)


class ElectricalEnergyCollector:
    """ElectricalEnergyMeasurement (0x0091): 積算電力量。mWh → Wh"""

    cluster = Clusters.ElectricalEnergyMeasurement
    sensor_type = "power"

    _DIRECTIONS = (
        (Clusters.ElectricalEnergyMeasurement.Attributes.CumulativeEnergyImported, "import"),
        (Clusters.ElectricalEnergyMeasurement.Attributes.CumulativeEnergyExported, "export"),
    )

    def collect(self, endpoint: MatterEndpoint) -> Iterator[Sample]:
        for attribute, direction in self._DIRECTIONS:
            measurement = endpoint.get_attribute_value(self.cluster, attribute)
            if measurement is not None and measurement.energy is not None:
                yield Sample(
                    "matter_energy_watt_hours",
                    measurement.energy / 1000.0,
                    {"direction": direction},
                )


COLLECTORS = [ElectricalPowerCollector(), ElectricalEnergyCollector()]

_VALUE_LABELS = ["device", "endpoint"]
# (device, endpoint) 以降に追加ラベルを持つファミリ（Sample.extra_labels の適用順を規定）
_EXTRA_LABELS: dict[str, list[str]] = {"matter_energy_watt_hours": ["direction"]}
_INFO_LABELS = [
    "device",
    "endpoint",
    "name",
    "serial",
    "vendor",
    "product",
    "topology",
    "device_types",
    "sensor_types",
]


def _new_families() -> dict[str, Metric]:
    return {
        "matter_active_power_watts": GaugeMetricFamily(
            "matter_active_power_watts",
            "Active power in watts",
            labels=_VALUE_LABELS,
        ),
        "matter_rms_voltage_volts": GaugeMetricFamily(
            "matter_rms_voltage_volts",
            "RMS voltage in volts",
            labels=_VALUE_LABELS,
        ),
        "matter_rms_current_amps": GaugeMetricFamily(
            "matter_rms_current_amps",
            "RMS current in amperes",
            labels=_VALUE_LABELS,
        ),
        "matter_energy_watt_hours": CounterMetricFamily(
            "matter_energy_watt_hours",
            "Cumulative energy in watt-hours",
            labels=[*_VALUE_LABELS, *_EXTRA_LABELS["matter_energy_watt_hours"]],
        ),
        "matter_node_available": GaugeMetricFamily(
            "matter_node_available",
            "Node availability (1=available, 0=unavailable)",
            labels=["device"],
        ),
        "matter_endpoint_info": GaugeMetricFamily(
            "matter_endpoint_info",
            "Endpoint metadata (join via device/endpoint)",
            labels=_INFO_LABELS,
        ),
    }


def build_metric_families(nodes: list[MatterNode], resolver: NameResolver) -> list[Metric]:
    """全ノードからメトリクスファミリを生成する（毎スクレイプ新規生成・状態なし）"""
    families = _new_families()

    for node in nodes:
        device = node_device_id(node)
        families["matter_node_available"].add_metric([device], 1 if node.available else 0)

        for endpoint in node.endpoints.values():
            matched = [c for c in COLLECTORS if endpoint.has_cluster(c.cluster)]
            if not matched:
                continue  # 測定クラスタを持たない endpoint は info も出さない

            identity = build_identity(endpoint)
            endpoint_label = str(identity.endpoint_id)
            name = resolver.resolve(identity.device, identity.serial, identity.endpoint_id)

            families["matter_endpoint_info"].add_metric(
                [
                    identity.device,
                    endpoint_label,
                    name,
                    identity.serial or "",
                    identity.vendor or "",
                    identity.product or "",
                    identity.topology,
                    identity.device_types,
                    ",".join(sorted({c.sensor_type for c in matched})),
                ],
                1,
            )

            if not node.available:
                # オフライン中は値メトリクス（古い測定値）を出さない。
                # ただし info はメタデータなので出し続ける — Grafana 側の join は
                # 現在時点の info に固定されており、消えると過去の履歴まで表示されなくなる
                continue

            for collector in matched:
                for sample in collector.collect(endpoint):
                    label_values = [identity.device, endpoint_label] + [
                        sample.extra_labels[k] for k in _EXTRA_LABELS.get(sample.metric, [])
                    ]
                    families[sample.metric].add_metric(label_values, sample.value)

    return list(families.values())


class _StaticCollector:
    """生成済みファミリをそのまま返すだけの一回限りのコレクター"""

    def __init__(self, families: list[Metric]):
        self._families = families

    def collect(self) -> list[Metric]:
        return self._families


def render_metrics(nodes: list[MatterNode], resolver: NameResolver) -> bytes:
    """Prometheus exposition 形式のバイト列を生成する。

    専用レジストリを使うため python_gc_* / process_* は混入しない。
    """
    registry = CollectorRegistry()
    registry.register(_StaticCollector(build_metric_families(nodes, resolver)))
    return generate_latest(registry)

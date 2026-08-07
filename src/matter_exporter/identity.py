"""MatterEndpoint → 安定した識別子とメタデータの解決。

設計: docs/design-v2.md 3.2節
- device の解決は UniqueID → serial → 親UID-epN のフォールバック連鎖
- 実機ブリッジで確認されたヌル文字埋め文字列・空 UniqueID をここで吸収する
- ブリッジ配下・composed device の差異は python-matter-server の
  MatterEndpoint.device_info プロパティに委ねる
"""

from __future__ import annotations

from dataclasses import dataclass

from chip.clusters import Objects as Clusters
from matter_server.client.models.node import MatterEndpoint, MatterNode

# PowerTopology クラスタの feature bit → 名称（Matter 1.4 仕様）
_TOPOLOGY_FEATURES = ((0b0001, "NODE"), (0b0010, "TREE"), (0b0100, "SET"))


@dataclass(frozen=True)
class EndpointIdentity:
    device: str  # 安定識別子（UniqueID / serial / 親UID-epN）
    endpoint_id: int
    serial: str | None
    vendor: str | None
    product: str | None
    topology: str  # "NODE" / "TREE" / "SET" / ""（PowerTopology クラスタなし）
    device_types: str  # "0x010a,0x0510" のような昇順カンマ結合


def sanitize(value: str | None) -> str | None:
    """ヌル文字埋め・空白のみの文字列を None に正規化する"""
    if value is None:
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def node_device_id(node: MatterNode) -> str:
    """ノードの安定識別子（UniqueID → serial → node<id>）"""
    info = node.device_info
    return (
        sanitize(getattr(info, "uniqueID", None))
        or sanitize(getattr(info, "serialNumber", None))
        or f"node{node.node_id}"
    )


def build_identity(endpoint: MatterEndpoint) -> EndpointIdentity:
    # device_info はブリッジ配下なら BridgedDeviceBasicInformation、
    # それ以外はノードの BasicInformation を返す（ライブラリが解決）
    info = endpoint.device_info
    unique_id = sanitize(getattr(info, "uniqueID", None))
    serial = sanitize(getattr(info, "serialNumber", None))

    device = unique_id or serial or f"{node_device_id(endpoint.node)}-ep{endpoint.endpoint_id}"

    return EndpointIdentity(
        device=device,
        endpoint_id=endpoint.endpoint_id,
        serial=serial,
        vendor=sanitize(getattr(info, "vendorName", None)),
        product=sanitize(getattr(info, "productName", None)),
        topology=_topology_of(endpoint),
        device_types=_device_types_of(endpoint),
    )


def _topology_of(endpoint: MatterEndpoint) -> str:
    cluster = endpoint.get_cluster(Clusters.PowerTopology)
    if cluster is None:
        return ""
    feature_map = cluster.featureMap or 0
    for bit, name in _TOPOLOGY_FEATURES:
        if feature_map & bit:
            return name
    return ""


def _device_types_of(endpoint: MatterEndpoint) -> str:
    cluster = endpoint.get_cluster(Clusters.Descriptor)
    if cluster is None:
        return ""
    type_ids = sorted({entry.deviceType for entry in cluster.deviceTypeList})
    return ",".join(f"0x{type_id:04x}" for type_id in type_ids)

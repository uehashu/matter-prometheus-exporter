"""NodeLabel 書き戻し（tools/write_labels.py のコアロジック）。

設計: docs/design-v2.md 2.3節
- 書き戻すのは names.yaml で明示的に命名された機器のみ（フォールバック名は書かない）
- NodeLabel の制約は 32 バイト（実機検証済み。docs/matter-identity-and-naming.md 5節）。
  マルチバイト文字の途中で切らずに切り詰める
- オフライン機には書けないため計画から除外する
"""

from __future__ import annotations

from dataclasses import dataclass

from matter_server.client.models.node import MatterNode

from .config import NameResolver
from .identity import node_device_id, sanitize

NODE_LABEL_MAX_BYTES = 32
NODE_LABEL_ATTRIBUTE_PATH = "0/40/5"  # endpoint 0 / BasicInformation / NodeLabel


def truncate_label(name: str, limit: int = NODE_LABEL_MAX_BYTES) -> str:
    """UTF-8 で limit バイトに収まるよう、文字境界で切り詰める"""
    encoded = name.encode("utf-8")
    if len(encoded) <= limit:
        return name
    return encoded[:limit].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class LabelWrite:
    node_id: int
    device: str
    serial: str | None
    current_label: str | None
    new_label: str


def build_write_plan(nodes: list[MatterNode], resolver: NameResolver) -> list[LabelWrite]:
    """書き込みが必要な (ノード, 新ラベル) の一覧を作る"""
    plan: list[LabelWrite] = []
    for node in nodes:
        if not node.available:
            continue  # オフライン機には書けない
        info = node.device_info
        device = node_device_id(node)
        serial = sanitize(getattr(info, "serialNumber", None))

        name = resolver.device_name(device, serial)
        if name is None:
            continue  # 明示的に命名された機器のみ書き戻す

        new_label = truncate_label(name)
        current_label = getattr(info, "nodeLabel", None)
        if new_label == current_label:
            continue  # 既に同じ値
        plan.append(
            LabelWrite(
                node_id=node.node_id,
                device=device,
                serial=serial,
                current_label=current_label,
                new_label=new_label,
            )
        )
    return plan

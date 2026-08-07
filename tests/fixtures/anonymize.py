#!/usr/bin/env python3
"""実機ダンプ（tools/dump_nodes.py --json の出力）を匿名化してフィクスチャ化する。

方針:
- 許可リスト方式。テストに必要なクラスタだけを残し、それ以外
  （Access Control / Operational Credentials 等、資格情報を含み得るもの）は丸ごと落とす
- 識別子（UniqueID / SerialNumber / MAC / IP）はノード順で決定的な架空値に置換する。
  プラグの「SerialNumber = MAC」という実機の性質は架空値でも維持する
- ヌル文字埋め文字列はテスト対象の実データなので、そのまま保持する
- 人名を含み得る NodeLabel は、既知の無害値（'Test Name'・空文字）以外を汎用名に置換する

使い方:
    python3 anonymize.py raw.json --nodes 3,15,16 --out plugs.json
    python3 anonymize.py raw.json --nodes 1 --out bridge.json
"""

import argparse
import base64
import json

# テストに使うクラスタのみ許可（それ以外は資格情報を含み得るため落とす）
ALLOWED_CLUSTERS = {
    6,  # OnOff
    29,  # Descriptor
    40,  # BasicInformation
    51,  # GeneralDiagnostics（NetworkInterfaces のみ残す）
    57,  # BridgedDeviceBasicInformation
    64,  # FixedLabel
    65,  # UserLabel
    144,  # ElectricalPowerMeasurement
    145,  # ElectricalEnergyMeasurement
    156,  # PowerTopology
}

GENERIC_BRIDGED_NAMES = ["玄関ロック", "エアコン", "テレビ", "照明", "カーテン"]
KNOWN_HARMLESS_LABELS = {"", "Test Name"}


def fake_serial(index: int) -> str:
    """MAC 形式 12 桁 hex の架空シリアル（実機プラグの serial=MAC の性質を維持）"""
    return f"AABBCC{index:06X}"


def fake_unique_id(index: int) -> str:
    return f"F1C70000{index:08X}"


def fake_mac_b64(index: int) -> str:
    return base64.b64encode(bytes.fromhex(fake_serial(index))).decode()


def fake_ipv4_b64(index: int) -> str:
    return base64.b64encode(bytes([10, 0, 0, 10 + index])).decode()


def is_null_padded(value: str) -> bool:
    return isinstance(value, str) and "\x00" in value


def anonymize_node(node: dict, index: int, name_counter: list[int]) -> dict:
    out = {k: v for k, v in node.items() if k != "attributes"}
    out["attribute_subscriptions"] = []
    attributes: dict = {}

    for path, value in node["attributes"].items():
        endpoint_id, cluster_id, attribute_id = (int(x) for x in path.split("/"))
        if cluster_id not in ALLOWED_CLUSTERS:
            continue

        # GeneralDiagnostics は NetworkInterfaces(0) のみ。MAC/IP を架空値に
        if cluster_id == 51:
            if attribute_id != 0:
                continue
            value = [
                {
                    "0": nic.get("0"),
                    "1": nic.get("1"),
                    "4": fake_mac_b64(index),
                    "5": [fake_ipv4_b64(index)],
                    "6": [],
                    "7": nic.get("7"),
                }
                for nic in (value or [])
            ]

        # BasicInformation: シリアルと UniqueID を置換、ラベルは無害値のみ保持
        if cluster_id == 40:
            if attribute_id == 15 and not is_null_padded(value):
                value = fake_serial(index)
            elif attribute_id == 18:
                value = fake_unique_id(index)
            elif attribute_id == 5 and value not in KNOWN_HARMLESS_LABELS:
                value = f"デバイス{index}"

        # BridgedDeviceBasicInformation: 人名を汎用名に。ヌル文字埋めは保持
        if cluster_id == 57:
            if attribute_id == 5 and value not in KNOWN_HARMLESS_LABELS:
                value = GENERIC_BRIDGED_NAMES[name_counter[0] % len(GENERIC_BRIDGED_NAMES)]
                name_counter[0] += 1
            elif attribute_id == 15 and not is_null_padded(value):
                # 長さを保った架空 hex（実機は 32 桁 hex のシリアルだった）
                value = ("5E5A" * 16)[: len(value)]
            elif attribute_id == 3 and not is_null_padded(value):
                pass  # 製品名はそのまま（個人情報ではない）

        attributes[path] = value

    out["attributes"] = attributes
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="tools/dump_nodes.py --json の出力ファイル")
    parser.add_argument("--nodes", required=True, help="対象 node_id（カンマ区切り）")
    parser.add_argument("--out", required=True, help="出力先 JSON")
    args = parser.parse_args()

    node_ids = [int(x) for x in args.nodes.split(",")]
    data = json.loads(open(args.input, encoding="utf-8").read())

    name_counter = [0]
    nodes = [
        anonymize_node(node, i, name_counter)
        for i, node in enumerate(
            n for n in data["nodes"] if n["node_id"] in node_ids
        )
    ]

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes}, f, indent=1, ensure_ascii=False)
    print(f"{len(nodes)} node(s) -> {args.out}")


if __name__ == "__main__":
    main()

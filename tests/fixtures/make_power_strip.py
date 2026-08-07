#!/usr/bin/env python3
"""複数口の電源タップを合成してフィクスチャ化する。

実機に電源タップが存在しないため、実機プラグの属性ワイヤ形式を複製して合成する。
設計 (docs/design-v2.md 5.2節) の要件:
- 1 ノードに複数の測定エンドポイント（口 1..3 = PowerTopology SET）
- 合計値エンドポイント（4 = PowerTopology NODE）
- 属性未報告（None）のケースを含む（口 3 は RMSCurrent を持たない）

使い方:
    python3 make_power_strip.py --out power_strip.json
"""

import argparse
import base64
import json

SERIAL = "AABBCCFF0001"
UNIQUE_ID = "F1C7FFFF00000001"


def outlet_attributes(
    endpoint_id: int, power_mw: int, voltage_mv: int, current_ma: int | None, energy_mwh: int
) -> dict:
    """口1つぶんの属性（Electrical Sensor 0x0510 + On/Off Plug-in Unit 0x010A）"""
    attrs = {
        f"{endpoint_id}/29/0": [{"0": 1296, "1": 1}, {"0": 266, "1": 1}],
        f"{endpoint_id}/29/1": [3, 4, 6, 29, 144, 145, 156],
        f"{endpoint_id}/29/2": [],
        f"{endpoint_id}/29/3": [],
        f"{endpoint_id}/6/0": True,
        f"{endpoint_id}/144/0": 2,
        f"{endpoint_id}/144/1": 4,
        f"{endpoint_id}/144/8": power_mw,
        f"{endpoint_id}/144/11": voltage_mv,
        f"{endpoint_id}/144/65532": 2,
        f"{endpoint_id}/144/65533": 1,
        f"{endpoint_id}/145/1": {"0": energy_mwh, "3": 1968260977, "4": 1968310979},
        f"{endpoint_id}/145/65532": 5,
        f"{endpoint_id}/145/65533": 1,
        f"{endpoint_id}/156/0": [endpoint_id],
        f"{endpoint_id}/156/65532": 4,  # SET
        f"{endpoint_id}/156/65533": 1,
    }
    if current_ma is not None:
        attrs[f"{endpoint_id}/144/12"] = current_ma
    return attrs


def total_attributes(endpoint_id: int, power_mw: int, voltage_mv: int, current_ma: int) -> dict:
    """ノード全体の合計値エンドポイント（Electrical Sensor のみ、topology=NODE）"""
    return {
        f"{endpoint_id}/29/0": [{"0": 1296, "1": 1}],
        f"{endpoint_id}/29/1": [29, 144, 156],
        f"{endpoint_id}/29/2": [],
        f"{endpoint_id}/29/3": [],
        f"{endpoint_id}/144/0": 2,
        f"{endpoint_id}/144/1": 4,
        f"{endpoint_id}/144/8": power_mw,
        f"{endpoint_id}/144/11": voltage_mv,
        f"{endpoint_id}/144/12": current_ma,
        f"{endpoint_id}/144/65532": 2,
        f"{endpoint_id}/144/65533": 1,
        f"{endpoint_id}/156/65532": 1,  # NODE
        f"{endpoint_id}/156/65533": 1,
    }


def build_power_strip() -> dict:
    attributes = {
        # endpoint 0: Root Node
        "0/29/0": [{"0": 22, "1": 1}],
        "0/29/1": [29, 40, 51],
        "0/29/2": [],
        "0/29/3": [1, 2, 3, 4],
        "0/40/1": "ACME",
        "0/40/3": "Smart Power Strip",
        "0/40/5": "",
        "0/40/15": SERIAL,
        "0/40/18": UNIQUE_ID,
        "0/51/0": [
            {
                "0": "eth0",
                "1": True,
                "4": base64.b64encode(bytes.fromhex(SERIAL)).decode(),
                "5": [base64.b64encode(bytes([10, 0, 0, 99])).decode()],
                "6": [],
                "7": 2,
            }
        ],
    }
    # 口 1..3（口 3 は RMSCurrent 未報告）
    attributes |= outlet_attributes(1, 10500, 100000, 105, 1234000)
    attributes |= outlet_attributes(2, 20000, 100000, 200, 5678000)
    attributes |= outlet_attributes(3, 0, 100000, None, 0)
    # 合計値エンドポイント（topology=NODE）
    attributes |= total_attributes(4, 30500, 100000, 305)

    return {
        "node_id": 20,
        "date_commissioned": "2026-01-01T00:00:00",
        "last_interview": "2026-01-01T00:00:00",
        "interview_version": 6,
        "available": True,
        "is_bridge": False,
        "attributes": attributes,
        "attribute_subscriptions": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="power_strip.json")
    args = parser.parse_args()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"nodes": [build_power_strip()]}, f, indent=1, ensure_ascii=False)
    print(f"1 node -> {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Matter Server の全ノードを読み取り専用でダンプし、識別子とトポロジを一覧する。

python-matter-server の WebSocket API を生で叩くため chip クラスタライブラリは不要で、
依存は本体と同じ aiohttp のみ。送信するコマンドは get_nodes だけで、書き込みは行わない。

名前付け用のマッピングを作る際に、シールの MAC アドレスから unique_id を引く用途を想定している。
Matter のデータモデルと識別子の背景は docs/matter-identity-and-naming.md を参照。

使い方:
    python3 tools/dump_nodes.py --url ws://192.168.1.7:5580/ws
    python3 tools/dump_nodes.py --url ws://... --json nodes.json   # 生データも保存
"""

import argparse
import asyncio
import base64
import json
import os
import sys

import aiohttp

# クラスタID
DESCRIPTOR = 0x001D
BASIC_INFORMATION = 0x0028
GENERAL_DIAGNOSTICS = 0x0033
BRIDGED_DEVICE_BASIC_INFORMATION = 0x0039
FIXED_LABEL = 0x0040
USER_LABEL = 0x0041
ELECTRICAL_POWER_MEASUREMENT = 0x0090
ELECTRICAL_ENERGY_MEASUREMENT = 0x0091
POWER_TOPOLOGY = 0x009C
ON_OFF = 0x0006

FEATURE_MAP = 0xFFFC

BASIC_ATTRS = {
    1: "VendorName",
    3: "ProductName",
    5: "NodeLabel",
    15: "SerialNumber",
    17: "Reachable",
    18: "UniqueID",
}
EPM_ATTRS = {4: "Voltage", 8: "ActivePower", 11: "RMSVoltage", 12: "RMSCurrent"}
PWRTL_FEATURES = {0: "NODE", 1: "TREE", 2: "SET", 3: "DYPF"}
CLUSTER_FLAGS = {
    ELECTRICAL_POWER_MEASUREMENT: "EPM",
    ELECTRICAL_ENERGY_MEASUREMENT: "EEM",
    POWER_TOPOLOGY: "PWRTL",
    ON_OFF: "OnOff",
    FIXED_LABEL: "FixedLabel",
    USER_LABEL: "UserLabel",
}


def format_mac(b64: str | None) -> str | None:
    """octstr(base64) の HardwareAddress を MAC 表記に変換"""
    if not b64:
        return None
    return ":".join(f"{x:02x}" for x in base64.b64decode(b64))


def format_ipv4(b64: str) -> str:
    return ".".join(str(x) for x in base64.b64decode(b64))


async def fetch_nodes(url: str) -> tuple[dict, list[dict]]:
    """get_nodes のみを送って全ノードの生データを取得する"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, max_msg_size=64 * 1024 * 1024) as ws:
            server_info = json.loads((await ws.receive()).data)
            await ws.send_json({"message_id": "1", "command": "get_nodes", "args": {}})
            while True:
                msg = json.loads((await ws.receive()).data)
                if msg.get("message_id") != "1":
                    continue  # 購読イベント等は読み飛ばす
                if "error_code" in msg:
                    raise RuntimeError(f"get_nodes failed: {msg}")
                return server_info, msg["result"]


def build_tree(attributes: dict) -> dict[int, dict[int, dict[int, object]]]:
    """"endpoint/cluster/attribute" のフラットな辞書を階層構造に組み直す"""
    tree: dict[int, dict[int, dict[int, object]]] = {}
    for path, value in attributes.items():
        endpoint_id, cluster_id, attribute_id = (int(x) for x in path.split("/"))
        tree.setdefault(endpoint_id, {}).setdefault(cluster_id, {})[attribute_id] = value
    return tree


def print_node(node: dict) -> None:
    tree = build_tree(node["attributes"])

    print("=" * 78)
    print(
        f"NODE {node['node_id']}  available={node['available']}  is_bridge={node['is_bridge']}"
    )

    basic = tree.get(0, {}).get(BASIC_INFORMATION, {})
    for attribute_id, name in BASIC_ATTRS.items():
        if attribute_id in basic:
            print(f"  BasicInformation.{name:13s} = {basic[attribute_id]!r}")

    for nic in tree.get(0, {}).get(GENERAL_DIAGNOSTICS, {}).get(0) or []:
        ipv4 = [format_ipv4(x) for x in nic.get("5") or []]
        print(
            f"  NIC name={nic.get('0')!r} operational={nic.get('1')} type={nic.get('7')} "
            f"mac={format_mac(nic.get('4'))} ipv4={ipv4}"
        )

    for endpoint_id in sorted(tree):
        clusters = tree[endpoint_id]
        descriptor = clusters.get(DESCRIPTOR, {})
        device_types = [f"0x{t['0']:04X}(rev{t['1']})" for t in descriptor.get(0, [])]
        parts_list = descriptor.get(3, [])
        flags = [label for cid, label in CLUSTER_FLAGS.items() if cid in clusters]

        print(
            f"  --- endpoint {endpoint_id}: types={device_types} "
            f"parts={parts_list} {' '.join(flags)}"
        )

        if tag_list := descriptor.get(4):
            print(f"      Descriptor.TagList = {tag_list}")
        if FIXED_LABEL in clusters:
            print(f"      FixedLabel.LabelList = {clusters[FIXED_LABEL].get(0)}")
        if USER_LABEL in clusters:
            print(f"      UserLabel.LabelList  = {clusters[USER_LABEL].get(0)}")

        if BRIDGED_DEVICE_BASIC_INFORMATION in clusters:
            bridged = clusters[BRIDGED_DEVICE_BASIC_INFORMATION]
            shown = {name: bridged[a] for a, name in BASIC_ATTRS.items() if a in bridged}
            print(f"      BridgedDeviceBasicInformation = {shown}")

        if POWER_TOPOLOGY in clusters:
            topology = clusters[POWER_TOPOLOGY]
            feature_map = topology.get(FEATURE_MAP, 0)
            features = [n for bit, n in PWRTL_FEATURES.items() if feature_map & (1 << bit)]
            print(
                f"      PowerTopology FeatureMap=0b{feature_map:04b} -> {features or ['(none)']} "
                f"AvailableEndpoints={topology.get(0)} ActiveEndpoints={topology.get(1)}"
            )

        if ELECTRICAL_POWER_MEASUREMENT in clusters:
            epm = clusters[ELECTRICAL_POWER_MEASUREMENT]
            values = {name: epm[a] for a, name in EPM_ATTRS.items() if a in epm}
            print(f"      EPM FeatureMap=0b{epm.get(FEATURE_MAP, 0):05b} {values}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("MATTER_WS_URL", "ws://localhost:5580/ws"),
        help="Matter Server WebSocket URL (既定: $MATTER_WS_URL または ws://localhost:5580/ws)",
    )
    parser.add_argument("--json", help="生のノードデータをこのパスに保存する")
    args = parser.parse_args()

    server_info, nodes = await fetch_nodes(args.url)

    print("=== server info ===")
    print(json.dumps(server_info, indent=2, ensure_ascii=False))
    print()

    for node in nodes:
        print_node(node)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {"server_info": server_info, "nodes": nodes},
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"\n生データを {args.json} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())

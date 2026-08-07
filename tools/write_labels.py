#!/usr/bin/env python3
"""names.yaml の機器名を Matter 機器の NodeLabel に書き戻す CLI（任意機能）。

- デフォルトは dry-run（計画の表示のみ）。実際に書くには --apply を付ける
- 書き戻すのは names.yaml で明示的に命名された機器のみ
- 32 バイト（日本語約 10 文字）に文字境界で切り詰める
- 書き込み後に読み戻して検証する

使い方:
    python3 tools/write_labels.py --url ws://192.168.1.7:5580/ws --names names.yaml
    python3 tools/write_labels.py --url ws://... --names names.yaml --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import aiohttp  # noqa: E402
from matter_server.client import MatterClient  # noqa: E402

from matter_exporter.config import NameResolver  # noqa: E402
from matter_exporter.labels import (  # noqa: E402
    NODE_LABEL_ATTRIBUTE_PATH,
    build_write_plan,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Matter Server WebSocket URL")
    parser.add_argument("--names", required=True, type=Path, help="names.yaml のパス")
    parser.add_argument(
        "--apply", action="store_true", help="実際に書き込む（省略時は dry-run）"
    )
    args = parser.parse_args()

    if not args.names.exists():
        print(f"エラー: {args.names} が見つかりません", file=sys.stderr)
        return 2
    resolver = NameResolver(args.names)

    async with aiohttp.ClientSession() as session:
        client = MatterClient(args.url, session)
        await client.connect()
        init_ready = asyncio.Event()
        listen_task = asyncio.create_task(client.start_listening(init_ready))
        await init_ready.wait()

        try:
            plan = build_write_plan(client.get_nodes(), resolver)
            if not plan:
                print("書き込み対象はありません（未命名・同一値・オフラインは除外）")
                return 0

            print(f"{'[dry-run] ' if not args.apply else ''}書き込み計画: {len(plan)} 件")
            for entry in plan:
                print(
                    f"  node {entry.node_id} (serial={entry.serial}): "
                    f"{entry.current_label!r} -> {entry.new_label!r}"
                )
            if not args.apply:
                print("\n実際に書き込むには --apply を付けてください")
                return 0

            failures = 0
            for entry in plan:
                await client.write_attribute(
                    entry.node_id, NODE_LABEL_ATTRIBUTE_PATH, entry.new_label
                )
                readback = await client.read_attribute(
                    entry.node_id, NODE_LABEL_ATTRIBUTE_PATH
                )
                actual = readback.get(NODE_LABEL_ATTRIBUTE_PATH)
                if actual == entry.new_label:
                    print(f"  node {entry.node_id}: 書き込み成功 ({entry.new_label!r})")
                else:
                    failures += 1
                    print(
                        f"  node {entry.node_id}: ⚠️ 読み戻し不一致 "
                        f"(期待={entry.new_label!r} 実際={actual!r})",
                        file=sys.stderr,
                    )
            return 1 if failures else 0
        finally:
            listen_task.cancel()
            await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

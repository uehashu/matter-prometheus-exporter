"""labels.py のテスト: NodeLabel 書き戻しの切り詰めと書き込み計画"""

import pytest

from matter_exporter.config import NameResolver
from matter_exporter.labels import build_write_plan, truncate_label


class TestTruncateLabel:
    def test_short_ascii_unchanged(self):
        assert truncate_label("Fridge") == "Fridge"

    def test_exactly_32_bytes_unchanged(self):
        assert truncate_label("A" * 32) == "A" * 32

    def test_33_bytes_truncated_to_32(self):
        assert truncate_label("A" * 33) == "A" * 32

    def test_japanese_truncated_at_character_boundary(self):
        """32 バイト制限（実機検証済み）: 日本語 11 文字(33B) → 10 文字(30B)。
        マルチバイト文字の途中で切らない"""
        assert truncate_label("延岡自宅玄関あいうえお") == "延岡自宅玄関あいうえ"

    def test_japanese_10_chars_unchanged(self):
        assert truncate_label("延岡自宅玄関あいうえ") == "延岡自宅玄関あいうえ"


@pytest.fixture
def named_resolver(tmp_path):
    """plugs フィクスチャの node 3（serial AABBCC000000）に名前を付ける"""
    path = tmp_path / "names.yaml"
    path.write_text(
        "devices:\n"
        '  - serial: "AABBCC000000"\n'
        '    name: "冷蔵庫"\n',
        encoding="utf-8",
    )
    return NameResolver(path)


class TestBuildWritePlan:
    def test_plans_write_for_explicitly_named_device(self, plugs, named_resolver):
        plan = build_write_plan(plugs, named_resolver)
        node3 = next(n for n in plugs if n.node_id == 3)
        assert len(plan) == 1
        entry = plan[0]
        assert entry.node_id == 3
        assert entry.current_label == node3.device_info.nodeLabel
        assert entry.new_label == "冷蔵庫"

    def test_unnamed_devices_not_planned(self, plugs):
        """serial フォールバック名は書き戻さない（明示的な名前のみ）"""
        assert build_write_plan(plugs, NameResolver(None)) == []

    def test_identical_label_skipped(self, plugs, tmp_path):
        node3 = next(n for n in plugs if n.node_id == 3)
        path = tmp_path / "names.yaml"
        path.write_text(
            f'devices:\n  - serial: "{node3.device_info.serialNumber}"\n'
            f'    name: "{node3.device_info.nodeLabel}"\n',
            encoding="utf-8",
        )
        assert build_write_plan(plugs, NameResolver(path)) == []

    def test_offline_device_excluded(self, plugs, tmp_path):
        offline = next(n for n in plugs if not n.available)
        path = tmp_path / "names.yaml"
        path.write_text(
            f'devices:\n  - serial: "{offline.device_info.serialNumber}"\n'
            '    name: "オフライン機"\n',
            encoding="utf-8",
        )
        assert build_write_plan(plugs, NameResolver(path)) == []

    def test_long_name_truncated_in_plan(self, plugs, tmp_path):
        path = tmp_path / "names.yaml"
        path.write_text(
            'devices:\n  - serial: "AABBCC000000"\n'
            '    name: "とても長い名前のデバイスをここに書いてみる"\n',
            encoding="utf-8",
        )
        plan = build_write_plan(plugs, NameResolver(path))
        assert len(plan) == 1
        assert len(plan[0].new_label.encode("utf-8")) <= 32

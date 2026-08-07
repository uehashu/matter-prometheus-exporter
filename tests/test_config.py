"""config.py のテスト: 環境変数の検証と names.yaml の解決・リロード"""

import logging
import os

import pytest

from matter_exporter.config import ConfigError, NameResolver, load_config


class TestLoadConfig:
    def test_defaults(self):
        config = load_config(env={})
        assert config.matter_ws_url == "ws://localhost:5580/ws"
        assert config.prometheus_port == 8000
        assert config.reconnect_interval == 10.0
        assert config.log_level == logging.INFO
        assert config.names_file is None

    def test_reads_all_env_vars(self, tmp_path):
        names = tmp_path / "names.yaml"
        names.write_text("devices: []")
        config = load_config(
            env={
                "MATTER_WS_URL": "ws://matter.local:5580/ws",
                "PROMETHEUS_EXPORTER_PORT": "9100",
                "MATTER_RECONNECT_INTERVAL": "30",
                "LOG_LEVEL": "debug",
                "MATTER_NAMES_FILE": str(names),
            }
        )
        assert config.matter_ws_url == "ws://matter.local:5580/ws"
        assert config.prometheus_port == 9100
        assert config.reconnect_interval == 30.0
        assert config.log_level == logging.DEBUG
        assert config.names_file == names

    def test_invalid_log_level_raises_clear_error(self):
        with pytest.raises(ConfigError, match="LOG_LEVEL"):
            load_config(env={"LOG_LEVEL": "verbose"})

    def test_non_numeric_interval_raises_clear_error(self):
        with pytest.raises(ConfigError, match="MATTER_RECONNECT_INTERVAL"):
            load_config(env={"MATTER_RECONNECT_INTERVAL": "abc"})

    def test_non_positive_interval_raises(self):
        with pytest.raises(ConfigError, match="MATTER_RECONNECT_INTERVAL"):
            load_config(env={"MATTER_RECONNECT_INTERVAL": "0"})

    def test_invalid_port_raises(self):
        with pytest.raises(ConfigError, match="PROMETHEUS_EXPORTER_PORT"):
            load_config(env={"PROMETHEUS_EXPORTER_PORT": "70000"})

    def test_default_env_is_process_environ(self, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_EXPORTER_PORT", "9999")
        assert load_config().prometheus_port == 9999


NAMES_YAML = """
devices:
  - serial: "AABBCC000000"
    name: "冷蔵庫"
  - serial: "aa:bb:cc:ff:00:01"     # MAC 表記（コロン・小文字）でも書ける
    name: "リビング電源タップ"
    endpoints:
      1: "テレビ"
      2: "録画機"
  - unique_id: "F1C7000000000002"
    name: "書斎PC"
"""


class TestNameResolver:
    @pytest.fixture
    def resolver(self, tmp_path) -> NameResolver:
        path = tmp_path / "names.yaml"
        path.write_text(NAMES_YAML, encoding="utf-8")
        return NameResolver(path)

    def test_resolves_by_serial(self, resolver):
        assert resolver.resolve("F1C7000000000000", "AABBCC000000", 1) == "冷蔵庫"

    def test_resolves_by_unique_id(self, resolver):
        assert resolver.resolve("F1C7000000000002", "UNRELATED", 1) == "書斎PC"

    def test_serial_matching_normalizes_mac_notation(self, resolver):
        """names.yaml 側がコロン区切り小文字でも、機器側の hex 表記と照合できる"""
        assert resolver.resolve("X", "AABBCCFF0001", 3) == "リビング電源タップ"

    def test_endpoint_specific_name_wins(self, resolver):
        assert resolver.resolve("X", "AABBCCFF0001", 1) == "テレビ"
        assert resolver.resolve("X", "AABBCCFF0001", 2) == "録画機"

    def test_unmatched_device_falls_back_to_serial(self, resolver):
        assert resolver.resolve("F1C7DEADBEEF0000", "CCDDEE000000", 1) == "CCDDEE000000"

    def test_unmatched_without_serial_falls_back_to_device(self, resolver):
        assert resolver.resolve("F1C7DEADBEEF0000", None, 1) == "F1C7DEADBEEF0000"

    def test_no_file_configured_uses_fallback(self):
        resolver = NameResolver(None)
        assert resolver.resolve("DEV1", "SER1", 1) == "SER1"

    def test_missing_file_uses_fallback(self, tmp_path):
        resolver = NameResolver(tmp_path / "nonexistent.yaml")
        assert resolver.resolve("DEV1", "SER1", 1) == "SER1"

    def test_reloads_when_mtime_changes(self, resolver, tmp_path):
        assert resolver.resolve("X", "AABBCC000000", 1) == "冷蔵庫"
        path = tmp_path / "names.yaml"
        path.write_text(
            'devices:\n  - serial: "AABBCC000000"\n    name: "新しい名前"\n',
            encoding="utf-8",
        )
        # 同一秒内の書き換えでも検知できるよう mtime を明示的に進める
        stat = path.stat()
        os.utime(path, (stat.st_atime, stat.st_mtime + 10))
        assert resolver.resolve("X", "AABBCC000000", 1) == "新しい名前"

    def test_invalid_yaml_keeps_last_good_config(self, resolver, tmp_path):
        assert resolver.resolve("X", "AABBCC000000", 1) == "冷蔵庫"
        path = tmp_path / "names.yaml"
        path.write_text("devices: [broken", encoding="utf-8")
        stat = path.stat()
        os.utime(path, (stat.st_atime, stat.st_mtime + 10))
        # 壊れた YAML では直前の有効な設定を使い続ける
        assert resolver.resolve("X", "AABBCC000000", 1) == "冷蔵庫"

    def test_initially_invalid_yaml_uses_fallback(self, tmp_path):
        path = tmp_path / "names.yaml"
        path.write_text("devices: [broken", encoding="utf-8")
        resolver = NameResolver(path)
        assert resolver.resolve("DEV1", "SER1", 1) == "SER1"

    def test_entry_without_key_is_skipped(self, tmp_path):
        path = tmp_path / "names.yaml"
        path.write_text(
            'devices:\n  - name: "キーなし"\n  - serial: "AABBCC000000"\n    name: "冷蔵庫"\n',
            encoding="utf-8",
        )
        resolver = NameResolver(path)
        assert resolver.resolve("X", "AABBCC000000", 1) == "冷蔵庫"


class TestConfigVal:
    def test_config_is_immutable(self):
        config = load_config(env={})
        with pytest.raises(AttributeError):
            config.prometheus_port = 1234  # type: ignore[misc]

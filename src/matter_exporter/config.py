"""環境変数の検証と names.yaml（ユーザー命名）の読み込み。

設計: docs/design-v2.md 2.1節・3.4節
- 環境変数の不正値は起動時に ConfigError で明確に報告する
- names.yaml は mtime を見てスクレイプ時に自動リロードする
- 壊れた YAML では直前の有効な設定を使い続ける（設定ミスでメトリクスを止めない）
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """設定値の検証エラー"""


@dataclass(frozen=True)
class Config:
    matter_ws_url: str
    prometheus_port: int
    reconnect_interval: float
    log_level: int
    names_file: Path | None


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """環境変数から設定を読み込み、検証する"""
    if env is None:
        env = os.environ

    matter_ws_url = env.get("MATTER_WS_URL", "ws://localhost:5580/ws")

    port_raw = env.get("PROMETHEUS_EXPORTER_PORT", "8000")
    try:
        prometheus_port = int(port_raw)
    except ValueError:
        raise ConfigError(
            f"PROMETHEUS_EXPORTER_PORT は整数で指定してください: {port_raw!r}"
        ) from None
    if not 1 <= prometheus_port <= 65535:
        raise ConfigError(
            f"PROMETHEUS_EXPORTER_PORT は 1〜65535 で指定してください: {prometheus_port}"
        )

    interval_raw = env.get("MATTER_RECONNECT_INTERVAL", "10")
    try:
        reconnect_interval = float(interval_raw)
    except ValueError:
        raise ConfigError(
            f"MATTER_RECONNECT_INTERVAL は数値で指定してください: {interval_raw!r}"
        ) from None
    if reconnect_interval <= 0:
        raise ConfigError(
            f"MATTER_RECONNECT_INTERVAL は正の数で指定してください: {reconnect_interval}"
        )

    level_raw = env.get("LOG_LEVEL", "INFO").upper()
    if level_raw not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"LOG_LEVEL は {'/'.join(VALID_LOG_LEVELS)} のいずれかで指定してください: "
            f"{env.get('LOG_LEVEL')!r}"
        )
    log_level = getattr(logging, level_raw)

    names_raw = env.get("MATTER_NAMES_FILE")
    names_file = Path(names_raw) if names_raw else None

    return Config(
        matter_ws_url=matter_ws_url,
        prometheus_port=prometheus_port,
        reconnect_interval=reconnect_interval,
        log_level=log_level,
        names_file=names_file,
    )


def _normalize_serial(serial: str) -> str:
    """MAC 表記のゆらぎ（コロン・ハイフン区切り、小文字）を吸収する"""
    return serial.replace(":", "").replace("-", "").upper()


@dataclass
class _NameEntry:
    name: str | None = None
    endpoints: dict[int, str] = field(default_factory=dict)


class NameResolver:
    """names.yaml に基づき (device, serial, endpoint) → 表示名 を解決する。

    解決の優先順: endpoints[endpoint_id] → name → serial → device
    ファイルは mtime の変化を検知して自動リロードする。
    """

    def __init__(self, path: Path | None):
        self._path = path
        self._mtime: float | None = None
        self._by_uid: dict[str, _NameEntry] = {}
        self._by_serial: dict[str, _NameEntry] = {}
        self._reload_if_changed()

    def resolve(self, device: str, serial: str | None, endpoint_id: int) -> str:
        self._reload_if_changed()
        entry = self._find_entry(device, serial)
        if entry is not None:
            if endpoint_name := entry.endpoints.get(endpoint_id):
                return endpoint_name
            if entry.name:
                return entry.name
        return serial or device

    def device_name(self, device: str, serial: str | None) -> str | None:
        """明示的に設定された機器名のみを返す（フォールバックしない。書き戻し用）"""
        self._reload_if_changed()
        entry = self._find_entry(device, serial)
        return entry.name if entry is not None else None

    def _find_entry(self, device: str, serial: str | None) -> _NameEntry | None:
        entry = self._by_uid.get(device)
        if entry is None and serial:
            entry = self._by_serial.get(_normalize_serial(serial))
        return entry

    def _reload_if_changed(self) -> None:
        if self._path is None:
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return  # ファイルが無い間はフォールバック動作（既存設定は維持）
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._load()
            logger.info("names.yaml を読み込みました: %s", self._path)
        except Exception as e:  # YAML/構造エラー: 直前の有効な設定を使い続ける
            logger.error("names.yaml の読み込みに失敗（直前の設定を維持）: %s", e)

    def _load(self) -> None:
        data = yaml.safe_load(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        devices = (data or {}).get("devices") or []
        if not isinstance(devices, list):
            raise ValueError("devices はリストで指定してください")

        by_uid: dict[str, _NameEntry] = {}
        by_serial: dict[str, _NameEntry] = {}
        for raw in devices:
            if not isinstance(raw, dict):
                logger.warning("devices の要素が辞書ではありません（無視）: %r", raw)
                continue
            entry = _NameEntry(
                name=raw.get("name"),
                endpoints={int(k): str(v) for k, v in (raw.get("endpoints") or {}).items()},
            )
            if uid := raw.get("unique_id"):
                by_uid[str(uid)] = entry
            elif serial := raw.get("serial"):
                by_serial[_normalize_serial(str(serial))] = entry
            else:
                logger.warning("serial / unique_id のない要素を無視しました: %r", raw)

        self._by_uid = by_uid
        self._by_serial = by_serial

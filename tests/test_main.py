"""__main__.py のテスト: 設定エラー時の起動失敗（サブプロセスで検証）"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def run_module(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ | {"PYTHONPATH": str(REPO_ROOT / "src")} | extra_env
    return subprocess.run(
        [sys.executable, "-m", "matter_exporter", "--check-config"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=30,
    )


class TestConfigValidationAtStartup:
    def test_invalid_log_level_exits_with_code_2_and_message(self):
        result = run_module({"LOG_LEVEL": "verbose"})
        assert result.returncode == 2
        assert "LOG_LEVEL" in result.stderr

    def test_invalid_interval_exits_with_code_2(self):
        result = run_module({"MATTER_RECONNECT_INTERVAL": "abc"})
        assert result.returncode == 2
        assert "MATTER_RECONNECT_INTERVAL" in result.stderr

    def test_valid_config_passes_check(self):
        result = run_module({"LOG_LEVEL": "INFO"})
        assert result.returncode == 0

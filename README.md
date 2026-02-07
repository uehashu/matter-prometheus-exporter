# Matter Prometheus Exporter

python-matter-serverからデータを取得し、Prometheus形式でメトリクスを公開するエクスポーター。

## 概要

Matter対応のスマートプラグやスマート電源タップから電力消費データをリアルタイムで取得し、Prometheusで監視可能にする。
後々は温湿度などにも対応するかも。

### 主な機能

- 電力メトリクスの取得（消費電力・電圧・電流）
- Prometheus形式でのエクスポート
- Matter Server自動再接続
- ヘルスチェックエンドポイント

### 公開メトリクス

```
matter_active_power_watts           # 消費電力（W）
matter_rms_voltage_volts            # 有効電圧（V）
matter_rms_current_amps             # 有効電流（A）
matter_node_label                   # ノード名ラベル
matter_node_available               # ノード可用性（1=利用可能, 0=利用不可）
```

## 前提条件

- Python 3.11以上
- 稼働中のMatter Server (python-matter-server)
  - WebSocketエンドポイント: `ws://<host>:<port>/ws`
  - https://github.com/home-assistant-libs/python-matter-server

## セットアップ

### Docker

```bash
# 環境変数ファイルを作成
cp dot.env .env

# .envファイルを編集
vim .env

# 起動
docker-compose up -d
```

### Pythonで直接実行

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# 環境変数を設定
export MATTER_WS_URL="ws://localhost:5580/ws"

# 実行
python3 src/matter_prometheus_exporter.py
```

### 環境変数

| 変数名                      | デフォルト値                        | 説明                                             |
| --------------------------- | ----------------------------------- | ------------------------------------------------ |
| `MATTER_WS_URL`             | `ws://host.docker.internal:5580/ws` | Matter Server WebSocketのURL                     |
| `LOG_LEVEL`                 | `INFO`                              | ログレベル (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MATTER_RECONNECT_INTERVAL` | `10`                                | 再接続間隔（秒）                                 |
| `PROMETHEUS_EXPORTER_PORT`  | `8000`                              | Prometheusメトリクス公開ポート                   |

## エンドポイント

### `/metrics` - Prometheusメトリクス

**サンプル出力:**

```
# HELP matter_active_power_watts Active power consumption in watts
# TYPE matter_active_power_watts gauge
matter_active_power_watts{unique_id="ABC123"} 15

# HELP matter_rms_voltage_volts RMS voltage in volts
# TYPE matter_rms_voltage_volts gauge
matter_rms_voltage_volts{unique_id="ABC123"} 100.528

# HELP matter_rms_current_amps RMS current in amperes
# TYPE matter_rms_current_amps gauge
matter_rms_current_amps{unique_id="ABC123"} 0.275

# HELP matter_node_label Node label a.k.a Name
# TYPE matter_node_label gauge
matter_node_label{unique_id="ABC123",node_label="Living Room Plug_1"} 1.0

# HELP matter_node_available Node availability status (1=available, 0=unavailable)
# TYPE matter_node_available gauge
matter_node_available{unique_id="ABC123"} 1.0
```

### `/health` - ヘルスチェック

**サンプル出力:**

```
{"status": "healthy", "matter_connected": true, "reconnect_interval": 10}%
```

## 参考

- [Matter Protocol](https://buildwithmatter.com/)
- [python-matter-server](https://github.com/home-assistant-libs/python-matter-server)
- [Prometheus](https://prometheus.io/)

# Matter Prometheus Exporter

python-matter-serverからデータを取得し、Prometheus形式でメトリクスを公開するエクスポーター。

## 概要

Matter対応のスマートプラグや電源タップから電力データをリアルタイムで取得し、Prometheusで監視可能にする。

### 主な機能

- 電力メトリクスの取得（消費電力・電圧・電流・積算電力量）
- 複数口デバイス（電源タップ等）対応 — `(device, endpoint)` の複合キーで口ごとに独立した時系列
- ユーザー命名（names.yaml）— シールのMACアドレスで機器を指定し、Grafanaに表示する名前を付けられる
- Matter Server自動再接続
- ヘルスチェック・デバイス発見エンドポイント
- Grafanaダッシュボード同梱（import一発）

### 公開メトリクス

```
# 値メトリクス（ラベルは device / endpoint のみ）
matter_active_power_watts{device, endpoint}                # 消費電力（W）
matter_rms_voltage_volts{device, endpoint}                 # 実効電圧（V）
matter_rms_current_amps{device, endpoint}                  # 実効電流（A）
matter_energy_watt_hours_total{device, endpoint, direction} # 積算電力量（Wh, Counter）
matter_node_available{device}                              # ノード可用性（1/0）

# infoメトリクス（名前などの可変メタデータはこちらに分離）
matter_endpoint_info{device, endpoint, name, serial, vendor, product,
                     topology, device_types, sensor_types}
```

- `device` = Matter の `UniqueID`（不変）。名前を変えても時系列は途切れない
- オフラインのノードは `matter_node_available 0` のみを出力する（古い値を出さない）
- 設計の背景は [docs/design-v2.md](docs/design-v2.md) を参照

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

### Docker Compose

```yaml
services:
  matter-prometheus-exporter:
    image: ghcr.io/uehashu/matter-prometheus-exporter:latest
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl --fail http://localhost:8000/health || exit 1"]
      start_period: 10s
      interval: 30s
      timeout: 10s
      retries: 3
    environment:
      MATTER_WS_URL: ws://host.docker.internal:5580/ws
      LOG_LEVEL: INFO
      MATTER_RECONNECT_INTERVAL: 10
      # 名前付け機能を使う場合
      # MATTER_NAMES_FILE: /config/names.yaml
    # volumes:
    #   - ./names.yaml:/config/names.yaml:ro
    ports:
      - "${PROMETHEUS_EXPORTER_PORT:-8000}:8000"

    # コンテナからホストにアクセスするための設定
    extra_hosts:
      - host.docker.internal:host-gateway
```

### Pythonで直接実行

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# 環境変数を設定
export MATTER_WS_URL="ws://localhost:5580/ws"

# 実行
PYTHONPATH=src python3 -m matter_exporter
```

### 環境変数

| 変数名                      | デフォルト値             | 説明                                             |
| --------------------------- | ------------------------ | ------------------------------------------------ |
| `MATTER_WS_URL`             | `ws://localhost:5580/ws` | Matter Server WebSocketのURL                     |
| `LOG_LEVEL`                 | `INFO`                   | ログレベル (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MATTER_RECONNECT_INTERVAL` | `10`                     | 再接続間隔（秒）                                 |
| `PROMETHEUS_EXPORTER_PORT`  | `8000`                   | HTTPサーバのポート                               |
| `MATTER_NAMES_FILE`         | （なし）                 | names.yamlのパス。未指定なら名前付け機能は無効   |

## 名前付け機能（names.yaml）

機器に表示名を付ける。キーは**シールに記載のMACアドレス**（Tapo等では `SerialNumber` = MAC）
または Matter の `unique_id`。

```yaml
devices:
  - serial: "AA:BB:CC:DD:EE:FF"   # シールのMAC（コロン・小文字も可）
    name: "冷蔵庫"                 # 1口機器はこれだけでよい

  - serial: "112233445566"        # 複数口機器（電源タップ等）
    name: "リビング電源タップ"       # 機器自体の名前
    endpoints:
      1: "テレビ"
      2: "録画機"

  - unique_id: "0123456789ABCDEF" # unique_id 直指定も可
    name: "書斎PC"
```

- ファイルは**自動リロード**される（編集にプロセス再起動不要）
- 名前は `matter_endpoint_info` の `name` ラベルに出る。値メトリクスのラベルは不変なので、
  **名前を変更しても時系列は途切れない**
- 機器の `serial` / `unique_id` は `/devices` エンドポイントで確認できる

### 機器への書き戻し（任意）

names.yaml の名前を機器本体の `NodeLabel` に書き戻すと、Home Assistant等の
他のコントローラーからも同じ名前が見える（32バイト = 日本語約10文字に切り詰め）。

```bash
# 計画の表示のみ（dry-run）
python3 tools/write_labels.py --url ws://192.168.1.7:5580/ws --names names.yaml

# 実際に書き込む
python3 tools/write_labels.py --url ws://192.168.1.7:5580/ws --names names.yaml --apply
```

## エンドポイント

### `/metrics` - Prometheusメトリクス

**サンプル出力:**

```
matter_active_power_watts{device="F1C7000000000001",endpoint="1"} 15.425
matter_rms_voltage_volts{device="F1C7000000000001",endpoint="1"} 95.412
matter_rms_current_amps{device="F1C7000000000001",endpoint="1"} 0.276
matter_energy_watt_hours_total{device="F1C7000000000001",direction="import",endpoint="1"} 60532.0
matter_node_available{device="F1C7000000000001"} 1.0
matter_endpoint_info{device="F1C7000000000001",endpoint="1",name="冷蔵庫",serial="...",vendor="Tapo",product="Smart Wi-Fi Plug",topology="SET",device_types="0x010a,0x0510",sensor_types="power"} 1.0
```

Matter Server未接続・取得失敗時は503を返す（Prometheus側で `up == 0` になる）。

### `/health` - ヘルスチェック

プロセス生存確認。常に200を返す。

```
{"status": "healthy", "matter_connected": true, "reconnect_interval": 10.0}
```

### `/devices` - デバイス発見

names.yamlを書くための一覧。シールと突き合わせて `serial` を確認する用途。

```json
[
  {
    "device": "F1C7000000000001",
    "serial": "AABBCC000001",
    "vendor": "Tapo",
    "product": "Smart Wi-Fi Plug",
    "node_label": "Test Name",
    "available": true,
    "endpoints": [
      {"endpoint": 1, "sensor_types": ["power"], "name": "冷蔵庫"}
    ]
  }
]
```

## Grafana

[grafana/dashboard.json](grafana/dashboard.json) をimportする（データソースにPrometheusを選択）。

- 電力センサ（`sensor_types=~".*power.*"`）のみが対象。他種別のセンサを将来追加しても影響しない
- デバイスごとの行が自動生成される（消費電力Stat・推移グラフ・電圧/電流・稼働状態）
- 全体行に合計消費電力と期間内電力量（kWh）

代表クエリ（凡例に名前を出すjoin）:

```promql
matter_active_power_watts
  * on(device, endpoint) group_left(name)
  matter_endpoint_info
```

## 開発

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest        # テスト（実機不要。フィクスチャで動く）
ruff check .  # lint
```

CIはpush/PRでテストとlintを実行する。Dockerイメージはタグ（`x.y.z`）のpushで
ghcr.ioに公開される。

## ドキュメント

- [v2設計: メトリクス体系と名前付け機能](docs/design-v2.md)
- [Matter デバイスの識別と名前付けに関する調査](docs/matter-identity-and-naming.md)

## ツール

- `tools/dump_nodes.py` — 全ノードの識別子・エンドポイント構成・PowerTopologyを読み取り専用でダンプ
- `tools/write_labels.py` — names.yamlの名前を機器のNodeLabelに書き戻す（dry-runがデフォルト）

## 参考

- [Matter Protocol](https://buildwithmatter.com/)
- [python-matter-server](https://github.com/home-assistant-libs/python-matter-server)
- [Prometheus](https://prometheus.io/)

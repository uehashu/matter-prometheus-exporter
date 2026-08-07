# v2 設計: メトリクス体系と名前付け機能

作成日: 2026-08-07
ステータス: 実装済み（2026-08-07、実機で動作確認済み）

## 前提

- 既存の時系列データは**破棄してよい**。互換レイヤーは作らず、クリーンブレイクで v2.0.0 とする
- 機器は当面**電力測定機器のみ**を想定する。ただし**1口以上（複数エンドポイント）の機器**が
  将来追加されることを前提に設計する
- 将来、電力以外のセンサ（温湿度等）をメトリクス化しても、
  **電力センサのみを対象とした Grafana ダッシュボードが影響を受けない**こと
- Grafana の可視化は現行運用と同程度のシンプルさを維持する
  （機器ごとの行に Stat + 時系列グラフ + 可用性、程度）

背景となる Matter の仕様調査・実機調査は
[matter-identity-and-naming.md](matter-identity-and-naming.md) を参照。
本文書はその調査で確定した事実（識別子の安定性、PowerTopology、NodeLabel の制約等）を
前提として引用する。

## 設計原則

1. **時系列の識別子は `(device, endpoint)` の複合キー一本に統一する。**
   Matter で安定・必須・不変なのは `BasicInformation.UniqueID` のみであり、
   口（コンセント）の区別は `endpoint_id` との組でしか表せない
2. **値メトリクスには不変ラベルのみを載せる。人間向けの名前・可変メタデータは
   info メトリクスに分離する。** 名前を変更しても時系列が途切れない
3. **Grafana 側の手間は「join を 1 回書く」以上に増やさない。**
   join 済みのダッシュボード JSON をリポジトリで配布して吸収する
4. **クラスタ→メトリクスの変換は追加しやすい構造にする。**
   新しいセンサ種別への対応は「コレクターを 1 つ足す」で完結させる

---

## 1. メトリクス体系

### 1.1 値メトリクス

ラベルは `device` / `endpoint`（+ 必要なら方向等の固有ラベル）のみ。

```text
# 瞬時値（Gauge）
matter_active_power_watts{device="<UniqueID>", endpoint="<endpoint_id>"}
matter_rms_voltage_volts {device="<UniqueID>", endpoint="<endpoint_id>"}
matter_rms_current_amps  {device="<UniqueID>", endpoint="<endpoint_id>"}

# 積算電力量（Counter）— ElectricalEnergyMeasurement (0x0091) から取得
matter_energy_watt_hours_total{device="...", endpoint="...", direction="import"}

# 可用性はノード単位（Matter 上 available はノードの属性のため endpoint を付けない）
matter_node_available{device="<UniqueID>"}
```

- `device` = `BasicInformation.UniqueID`（16 桁 hex）。
  ブリッジ配下エンドポイントで UniqueID が取れない場合のフォールバックは 3.2 節
- `endpoint` = Matter の endpoint id（文字列化した整数）
- 単位換算は現行踏襲: mW/mV/mA → W/V/A（÷1000）。
  電力量は EEM の mWh → Wh（÷1000）
- **ノードがオフライン（`available=False`）のときは値メトリクスを出力しない。**
  古い値が生きた値として見え続けるのを防ぎ、
  Grafana では自然に欠測（No data / 途切れ）として表示される
- ただし **info メトリクスと `matter_node_available` はオフライン中も出し続ける**（v2.1.0〜）。
  info はメタデータであり測定値ではないため。Grafana の join は
  `matter_endpoint_info @ end()`（表示範囲末尾 = 現在の名前）に固定しており、
  info が消えるとオフライン機の過去の履歴までグラフから消えてしまう

### 1.2 info メトリクス

`(device, endpoint)` につき**必ず 1 本**。値は常に 1。

```text
matter_endpoint_info{
    device="<UniqueID>", endpoint="1",
    name="冷蔵庫",                  # names.yaml 由来。未設定時は serial にフォールバック
    serial="AABBCCDDEEFF",         # シールの MAC = 機器の照合キー（実機調査で一致を確認済み）
    vendor="Tapo", product="Smart Wi-Fi Plug",
    topology="SET",                # PowerTopology の feature（NODE/TREE/SET/なし）
    device_types="0x0510,0x010a",  # Descriptor.DeviceTypeList の生値（hex、昇順カンマ結合）
    sensor_types="power"           # マッチしたコレクター由来の意味的種別（昇順カンマ結合）
} 1
```

| ラベル | 中身 | 用途 |
|---|---|---|
| `name` | names.yaml のユーザー命名 | Grafana の凡例・行タイトル |
| `serial` | `BasicInformation.SerialNumber` | シールとの照合 |
| `topology` | PowerTopology feature | `NODE`（合計値）の機器が混ざったときの二重計上除外 |
| `device_types` | Descriptor の生 Device Type ID | デバッグ・仕様との突き合わせ |
| `sensor_types` | コレクターが実際にマッチした種別 | **Grafana でのセンサ種別フィルタ** |

設計判断:

- **`sensor_types` を生値（`device_types`）と別に持つ**のは、フィルタを人間可読にするため。
  現時点の値は `power` のみ。将来温湿度コレクターを追加すればその endpoint に
  `temperature,humidity` 等が付き、電力ダッシュボードには一切影響しない
- **info メトリクスを種別ごとに複数本にしない**（例: `matter_endpoint_info{type="power"}` と
  `{type="onoff"}` の 2 本）。`group_left` join が many-to-many になって壊れるため、
  複数種別はカンマ結合ラベルで表現する
- `name` は必ず存在する（未設定時 serial → それも無ければ device にフォールバック）。
  **join が欠けて系列が消えることがない**

### 1.3 レジストリ

デフォルトレジストリを使わず**専用 `CollectorRegistry`** で出力する。
`python_gc_*` / `process_*` の混入を排除し、`/metrics` を機器データのみにする。

### 1.4 現行 v1 からの変更一覧

| v1 | v2 | 理由 |
|---|---|---|
| `unique_id` ラベル | `device` + **`endpoint` 追加** | 複数口機器で値が上書きされるバグの根治 |
| `matter_node_label`（`node_label="Name_1"` 合成） | `matter_endpoint_info` に統合、合成廃止 | info metric パターンの正式化 |
| （なし） | `matter_energy_watt_hours_total` | 実機全台が EEM 実装済み。kWh パネルが `increase()` で作れる |
| `matter_node_available{unique_id}` | `matter_node_available{device}` | ラベル名統一。ノード単位は維持 |
| オフライン時も直前値が残る | オフライン時は値メトリクスを出さない | 死んだ機器の値が生きて見える問題の解消 |
| デフォルトレジストリ | 専用レジストリ | GC メトリクス等の混入排除 |

---

## 2. 名前付け機能

方針は調査文書 6 節の結論（案A を正、案C を任意機能）に従う。

### 2.1 names.yaml

```yaml
# キーは serial（シールの MAC）または unique_id
devices:
  - serial: "AABBCCDDEEFF"       # シールを見て書く
    name: "冷蔵庫"                # 1口機器はこれだけでよい

  - serial: "112233445566"       # 複数口機器（電源タップ等）
    name: "リビング電源タップ"      # 機器自体の名前（endpoint 未指定時のフォールバック兼用）
    endpoints:
      1: "テレビ"
      2: "録画機"
      3: "ゲーム機"

  - unique_id: "0123456789ABCDEF"  # unique_id 直指定も可
    name: "書斎PC"
```

- 内部では必ず `UniqueID` に解決して保持する（serial は照合キー、unique_id が主キー）
- 名前解決の優先順: `endpoints[id]` → `name` → serial → device
- **スクレイプ時に mtime を確認して自動リロード。** 名前変更に再起動不要
- ファイルパスは環境変数 `MATTER_NAMES_FILE`（未指定なら名前付け機能は無効、
  フォールバック動作のみ）
- 不正な YAML はエラーログを出して**直前の有効な設定を使い続ける**
  （設定ミスでメトリクスを止めない）

### 2.2 発見用エンドポイント `/devices`

全ノードの識別情報を JSON で返す読み取り専用エンドポイント。
シールと突き合わせて names.yaml を書くための手段。

```json
[
  {
    "device": "0123456789ABCDEF",
    "serial": "AABBCCDDEEFF",
    "vendor": "Tapo",
    "product": "Smart Wi-Fi Plug",
    "node_label": "",
    "available": true,
    "endpoints": [
      {"endpoint": 1, "sensor_types": ["power"], "name": "冷蔵庫"}
    ]
  }
]
```

### 2.3 書き戻しツール `tools/write_labels.py`（任意機能）

- names.yaml を読み、UTF-8 で 32 バイトに切り詰めて `BasicInformation.NodeLabel` に書く
  （32 バイト制限は実機検証済み。日本語なら 10 文字）
- **エクスポーター本体は読み取り専用を維持**し、書き込みは明示的に実行する CLI に隔離する
- 複数口機器では機器名（`name`）のみを書く（NodeLabel はノード単位のため）

---

## 3. アーキテクチャ

### 3.1 モジュール構成

```text
src/matter_exporter/
  __main__.py     # python -m matter_exporter
  config.py       # 環境変数の検証 + names.yaml の読み込み・監視
  client.py       # Matter Server 接続・再接続（現行 2 クラスを統合、is_connected を公開）
  identity.py     # MatterEndpoint → (device, endpoint, EndpointInfo) の解決
  collectors.py   # クラスタ → メトリクスの変換
  server.py       # aiohttp: /metrics /health /devices
tools/
  dump_nodes.py   # （既存）読み取り専用ダンプ
  write_labels.py # NodeLabel 書き戻し CLI
tests/
  fixtures/       # 実機ダンプを匿名化したノードデータ
grafana/
  dashboard.json  # 配布用ダッシュボード
```

### 3.2 identity.py — 識別の一元化

`MatterEndpoint.device_info` プロパティ（python-matter-server 提供）を使い、
通常機器 / ブリッジ配下 / composed device の差異をライブラリに委ねる。
その上で本モジュールが吸収するのは:

- **サニタイズ**: ヌル文字埋め文字列（実機のブリッジで確認済み）を None 扱いにする
- **フォールバック**: `UniqueID` が無い場合（ブリッジ配下で実測）は
  `serial` → `"<親UniqueID>-ep<endpoint_id>"` の順で `device` を決める
- `sensor_types` / `device_types` / `topology` の導出

### 3.3 collectors.py — custom collector 方式

prometheus_client の Gauge を保持し `clear()`/`set()` する現行方式をやめ、
**スクレイプごとに `GaugeMetricFamily` / `CounterMetricFamily` を新規生成する**
custom collector 方式に切り替える。

```python
class ElectricalPowerCollector:
    cluster = Clusters.ElectricalPowerMeasurement
    sensor_type = "power"

    def collect(self, endpoint) -> Iterable[Sample]:
        if (mw := endpoint.get_attribute_value(...ActivePower)) is not None:
            yield Sample("matter_active_power_watts", mw / 1000.0)
        ...

COLLECTORS = [ElectricalPowerCollector(), ElectricalEnergyCollector()]
```

- endpoint が持つクラスタにマッチしたコレクターだけが実行される
- endpoint の `sensor_types` は「マッチしたコレクターの `sensor_type` の集合」として決まる
- 新センサ対応 = コレクタークラスを 1 つ追加して登録するだけ

この方式の副次効果:

- 状態を持たないため `clear()` → `set()` の並行スクレイプ競合が**構造的に消滅**
- 消えたデバイスの stale 系列が自然に消える
- 現行バグ A-1（endpoint 衝突）/ A-2（未定義変数）/ A-3（break 位置）の
  該当コードがそもそも存在しなくなる

### 3.4 接続管理（client.py）

現行の「オンデマンド取得 + バックグラウンド再接続」の設計は維持する。
統合時に以下を解消する:

- `_connected` への外部アクセス → `is_connected` プロパティを公開
- `connect()` 失敗時に `disconnect()` の例外で元エラーが失われる問題
- シグナルハンドラを `loop.add_signal_handler()` に変更
- 環境変数の検証（不正な `LOG_LEVEL` / 非数値インターバル）を `config.py` で起動時に行い、
  明確なエラーメッセージで終了する

### 3.5 HTTP エンドポイント（server.py）

| パス | 内容 |
|---|---|
| `/metrics` | Prometheus 形式。Matter 未接続・取得失敗時は 503（現行踏襲。Prometheus 側で `up==0` になる） |
| `/health` | 常に 200 + JSON（現行踏襲: プロセス生存確認。`matter_connected` を含む） |
| `/devices` | 2.2 節の発見用 JSON |

ポートは `PROMETHEUS_EXPORTER_PORT` 環境変数で変更可能にする
（現行はハードコードで README と不整合）。

---

## 4. Grafana

### 4.1 ダッシュボード構成

現行運用のレイアウト（機器ごとの行 = Stat + 時系列 + 可用性）を、
**変数 + Repeat row で機器台数に依存しない形**にして `grafana/dashboard.json` として配布する。

```text
変数 ep: matter_endpoint_info{sensor_types=~".*power.*"} から (device, endpoint, name) を列挙
行:      Repeat by $ep — 電力センサの endpoint ごとに 1 行が自動生成される
```

**電力センサのみの可視化**は変数のフィルタ `sensor_types=~".*power.*"` で担保する。
将来、電力以外のセンサをメトリクス化してもこのダッシュボードには現れない。
（値メトリクス側も元々 EPM を持つ endpoint にしか存在しないため二重に安全）

### 4.2 代表クエリ

```promql
# 消費電力（凡例 = {{name}}）
  matter_active_power_watts
* on(device, endpoint) group_left(name)
  matter_endpoint_info

# 今日の電力量 kWh
  increase(matter_energy_watt_hours_total[$__range]) / 1000
* on(device, endpoint) group_left(name)
  matter_endpoint_info

# 全体合計（合計値トポロジの機器を除外して二重計上を防ぐ）
sum(
    matter_active_power_watts
  * on(device, endpoint) group_left(topology)
    matter_endpoint_info{topology!="NODE"}
)

# 可用性
matter_node_available
```

名前の変更は names.yaml の編集だけで反映され（次スクレイプから凡例が変わる）、
`(device, endpoint)` が不変なので**時系列は途切れない**。

---

## 5. テスト設計

現行 v1 にはテストが 1 本もなく、CI もイメージのビルドのみである（調査文書 D-1）。
v2 では**実機なしで全ロジックを検証できる**ことをテスト基盤の要件とする。

### 5.1 戦略: 実機ダンプをフィクスチャとして再生する

python-matter-server の `MatterNode` は「属性パス → 値」のフラットな辞書
（`tools/dump_nodes.py --json` が保存する形式そのもの）から構築できる。
これを利用し、**実機ダンプを匿名化した JSON をテスト内で `MatterNode` に組み立て、
identity / collectors を実物と同じデータ構造に対して検証する**。

```python
def load_fixture(name: str) -> list[MatterNode]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [MatterNode(dataclass_from_dict(MatterNodeData, n)) for n in data["nodes"]]
```

- Matter Server への接続・WebSocket・ネットワークは単体テストでは一切使わない
- `chip.clusters` のクラスタ定義（`home-assistant-chip-clusters`）は
  既存依存 `python-matter-server` に含まれるため、追加の重い依存は不要

### 5.2 フィクスチャ

`tests/fixtures/` に以下を置く。

| フィクスチャ | 内容 | 由来 |
|---|---|---|
| `plugs.json` | 1口プラグ複数台（EPM+EEM+PWRTL、オンライン/オフライン混在、NodeLabel 空/重複） | 実機ダンプを匿名化 |
| `bridge.json` | ブリッジ + 配下エンドポイント（UniqueID 欠落、ヌル文字埋め文字列を含む） | 実機ダンプを匿名化 |
| `power_strip.json` | **1ノード複数口**（endpoint 1..3 が各々 EPM を持つ）+ `topology=NODE` の合計値エンドポイント | **合成**（実機に存在しないが v2 の主目的のため必須） |

- 匿名化は `tests/fixtures/anonymize.py`（ダンプ JSON → 架空の MAC/IP/シリアル/名前に
  決定的に置換するスクリプト）で行い、再生成可能にする
- 合成フィクスチャは実機ダンプの構造を複製してエンドポイントを増やして作る

### 5.3 モジュール別テストケース

**config.py**
- 環境変数: 不正な `LOG_LEVEL` / 非数値インターバルで明確なエラー、正常系のデフォルト値
- names.yaml: serial 指定 / unique_id 指定 / endpoints 指定の解決、
  不正 YAML で直前の有効設定を維持、mtime 変更で再読み込み、未指定時の無効動作

**identity.py**
- 名前解決の優先順: `endpoints[id]` → `name` → serial → device
- ヌル文字埋め文字列のサニタイズ（bridge フィクスチャで実データ再現）
- `device` のフォールバック連鎖: UniqueID 欠落 → serial → `<親UniqueID>-ep<id>`
- `sensor_types` / `device_types` / `topology` の導出（PWRTL feature bit の解釈を含む）

**collectors.py**
- 単位換算: mW/mV/mA → W/V/A、EEM mWh → Wh
- 属性が None（未報告）のとき該当サンプルを出さない
- **複数口フィクスチャで endpoint ごとに独立した系列が出る**（v1 バグ A-1 の回帰テスト）
- **オフラインノードは `matter_node_available 0` のみ**（v1 バグ A-2 の回帰テスト）
- info メトリクスが `(device, endpoint)` につき厳密に 1 本
- `topology="NODE"` のエンドポイントにも値は出るが `topology` ラベルで区別できる

**server.py**（aiohttp の test client を使用、Matter クライアントはフェイクを注入）
- `/metrics`: 正常時 200 + 期待どおりの exposition、未接続/取得失敗時 503
- `/metrics` の出力を prometheus_client のパーサで往復させ、形式の妥当性を機械検証する
- `/devices`: フィクスチャどおりの JSON、names.yaml の名前が反映される
- `/health`: 常に 200、`matter_connected` の真偽

**client.py**
- 再接続ロジックはフェイクの MatterClient で状態遷移のみ検証
  （接続失敗時に元の例外が失われないこと = v1 バグ B-2 の回帰テストを含む）

### 5.4 CI

`.github/workflows/test.yml` を新設し、push / PR で実行する
（現行の docker-publish はタグ push のみで、テストを一切走らせていない）。

```yaml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: ruff check .
      - run: pytest
```

- `requirements-dev.txt` を新設: `pytest` / `pytest-asyncio` / `ruff`
- Docker イメージのビルドは従来どおりタグ push 時のみ。
  テストが通らない限りタグを打たない運用とする

### 5.5 テストしないもの

- 実機・実 Matter Server との結合（`tools/dump_nodes.py` による手動確認で代替）
- python-matter-server 自体の挙動（上流のテスト範囲）

## 6. 移行

- 互換レイヤーなし。v2.0.0 としてタグ付けし、README のメトリクス一覧を全面更新する
- 既存の Grafana ダッシュボードは `grafana/dashboard.json` の import で置き換える
- 破棄対象: 旧メトリクス名のラベル体系（`unique_id` 単独キー、`matter_node_label`）

## 7. 実装計画

各段階で「実装 + そのモジュールのテスト」を対で完成させる（5 節のケースを配分）。

1. テスト基盤: `requirements-dev.txt` + CI ワークフロー + フィクスチャ
   （実機ダンプの匿名化・複数口の合成データ作成、`load_fixture` ヘルパー）
2. パッケージ骨格 + `config.py`（環境変数検証・names.yaml のテストを含む）
3. `client.py`（現行 2 クラスの統合と堅牢化）
4. `identity.py` + `collectors.py`（メトリクス体系の本体。A-1/A-2 の回帰テストを含む）
5. `server.py` + names.yaml 連携（/metrics /health /devices）
6. `grafana/dashboard.json` + README / docs 更新
7. `tools/write_labels.py`（任意機能）

## 8. 未決事項

- ~~`matter_energy_watt_hours_total` の属性選択~~ → **解決**: 実機で
  `CumulativeEnergyImported` の実装を確認（例: 60513000 mWh = 60.5 kWh の実データ）。
  Cumulative の import/export を採用した
- Docker イメージの非 root 実行・curl 依存の除去などインフラ改善は本設計と独立に扱う

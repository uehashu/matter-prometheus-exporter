# Matter デバイスの識別と名前付けに関する調査

調査日: 2026-08-07

## 背景と目的

本エクスポーターが公開するメトリクスに、ユーザーが自分で付けた名前を載せたい。
機器を操作するアプリからは Matter 上の名前を編集できないため、エクスポーター側で
名前を管理する仕組みが要る。

そのために必要な前提として、以下を確定させる。

1. Matter のデータモデル上、機器と測定値をどの粒度で一意に識別できるか
2. 機器のシールに書かれた情報と Matter のデータを照合できるか
3. 名前をどこに保持するか（外部設定 / Matter の機器側）

あわせて、既知の不具合（後述）が実環境でどう影響しているかを確認する。

---

## 1. Matter のデータモデル

```
Fabric（1つの Matter ネットワーク）
└── Node          … ネットワーク上でアドレス可能な単位。Node ID を持つ
    └── Endpoint  … 機能単位。Endpoint 0 は必ず Root Node
        └── Cluster   … 機能の実体（On/Off, ElectricalPowerMeasurement 等）
            └── Attribute
```

### Endpoint 0 の特殊性

Endpoint 0 はアプリケーション機能を持たず、機器全体の管理情報を置く。
`Basic Information` クラスタ（0x0028）はここに 1 つだけ存在する。

python-matter-server の実装もエンドポイント 0 決め打ちである。

```python
# matter_server/client/models/node.py — MatterNode
@property
def device_info(self) -> Clusters.BasicInformation:
    """Returns BasicInformation from the Node itself (endpoint 0)."""
    return self.get_cluster(0, Clusters.BasicInformation)
```

つまり `UniqueID` / `SerialNumber` / `NodeLabel` は**ノード単位の属性**であり、
エンドポイント単位では存在しない。

### 「1 機器 = 1 ノード」は成立しないことがある

ブリッジは 1 ノードの中に配下の全デバイスをエンドポイントとして並べる。
この場合「エンドポイント = 物理的に別の機器」になる。
ブリッジ配下のエンドポイントには `BridgedDeviceBasicInformation`（0x0039）が付き、
エンドポイントごとの識別情報がそこに入る。

python-matter-server はこの差異を吸収するプロパティを提供している。

```python
# MatterEndpoint
@property
def device_info(self):
    if self.is_bridged_device:
        return self.get_cluster(Clusters.BridgedDeviceBasicInformation)
    if compose_parent := self.node.get_compose_parent(self.endpoint_id):
        return compose_parent.device_info
    return self.node.device_info
```

`MatterNode.get_compose_parent()` / `get_compose_child_ids()` は
`Descriptor.PartsList`（0x001D / 0x0003）から構築されている。

---

## 2. 電力測定の意味は Power Topology クラスタが決める

Matter 1.3 の `Electrical Sensor` デバイスタイプ（0x0510）は
`Power Topology`（0x009C）を**必須**とする。

```xml
<deviceType id="0x0510" name="Electrical Sensor" revision="1">
  <classification class="utility" scope="endpoint"/>
  <clusters>
    <cluster id="0x0090" name="Electrical Power Measurement"  → optional (choice a, min 1)
    <cluster id="0x0091" name="Electrical Energy Measurement" → optional (choice a, min 1)
    <cluster id="0x009C" name="Power Topology"                → mandatoryConform
```

Power Topology の feature が、その測定値が**どの範囲の電力か**を示す。

| feature | 意味 |
|---|---|
| `NODE` (bit 0) | ノード全体の電力 |
| `TREE` (bit 1) | 自エンドポイントとその子エンドポイント |
| `SET` (bit 2) | `AvailableEndpoints` で指定されたエンドポイント群 |
| `DYPF` (bit 3) | 対象エンドポイント集合が動的に変わる |

**エンドポイントごとに `ElectricalPowerMeasurement` があっても、それが「口ごとの電力」とは限らない。**
`NODE` トポロジなら合計値であり、口別値と混ぜて集計すると二重計上になる。

---

## 3. 識別子の候補と評価

| 識別子 | 取得元 | 仕様上の扱い | 安定性 |
|---|---|---|---|
| `UniqueID` | BasicInformation `0x0012` | **mandatory** / `persistence="fixed"` / read-only | **最高**。必ず存在し、工場出荷時から不変 |
| `SerialNumber` | BasicInformation `0x000F` | **optional** / fixed / read-only | 高。ただし存在しない機器があり得る |
| MAC アドレス | GeneralDiagnostics `0x0033` → `NetworkInterfaces[].HardwareAddress` | GeneralDiagnostics は Root Node で **mandatory** | 高 |
| `node_id` | Fabric が割り当て | — | **低。再ペアリングで変わる** |
| `endpoint_id` | Descriptor | — | 中（ブリッジは動的に増減する） |

### シールの「Matter ID」は識別子として使えない

シールに印字された QR コードと 11 桁のペアリングコードは Onboarding Payload
（Vendor ID / Product ID / Discriminator / Passcode）であり、コミッショニング用の資格情報である。

- Passcode は本来秘密情報であり、識別子として扱うべきではない
- 安価な機器では同一機種の全個体で同じ値が焼かれている実例がある
- コミッショニング完了後にコントローラー側から読み出す手段が基本的にない

**照合に使えるのは MAC アドレス、または（存在すれば）SerialNumber。**

### エンドポイント単位の一意性

仕様上、非ブリッジのエンドポイントには固有 ID が存在しない。
存在するのは `endpoint_id`（ノード内でのみ一意な整数）と、
ブリッジ配下のみの `BridgedDeviceBasicInformation.UniqueID` である。

したがって口を一意に指すキーは必然的に複合キーになる。

```
(UniqueID, endpoint_id)
```

Home Assistant の Matter 統合も同じ結論で、entity の unique_id に必ず endpoint_id を含めている。

```python
# homeassistant/components/matter/entity.py
self._attr_unique_id = (
    f"{node_device_id}-{endpoint.endpoint_id}-"
    f"{entity_info.entity_description.key}-"
    f"{...cluster_id}-{...attribute_id}"
)
```

---

## 4. 実機調査の結果

Matter Server（python-matter-server, sdk_version 2025.7.0, schema_version 11）に対し、
`get_nodes` で全ノードを読み取って調査した。

> 以下、MAC アドレス・IP アドレス・機器の実名はマスクしている。

### 構成

**9 ノード** = ブリッジ 1 台（CANDY HOUSE Hub3）+ スマートプラグ 8 台（Tapo Smart Wi-Fi Plug）。
**電源タップは無い。** プラグは全機が下記の構成だった。

```
--- endpoint 0: types=['0x0016(rev1)'] parts=[1]           ← Root Node
--- endpoint 1: types=['0x0510(rev1)', '0x010A(rev1)']     ← Electrical Sensor + On/Off Plug-in Unit
    PowerTopology FeatureMap=0b0100 -> ['SET'] AvailableEndpoints=[1]
    EPM FeatureMap=0b00010 {'ActivePower': 17647, 'RMSVoltage': 96395, 'RMSCurrent': 330}
```

**1 ノード = 1 エンドポイント = 1 口。** Electrical Sensor と On/Off Plug-in Unit が
同一エンドポイントに同居している。

`PowerTopology` は `SET` / `AvailableEndpoints=[1]` で、
「この測定値はエンドポイント 1 の電力である」と機器が明示している。
第 2 節で述べた「合計値かもしれない」問題は本環境では発生しない。

### 識別子の実測

| 識別子 | 存在 | 一意性 | 備考 |
|---|---|---|---|
| `UniqueID` | 9/9 | **9/9 ユニーク** | 16 桁 hex。MAC とは無関係 |
| `SerialNumber` | 9/9 | **9/9 ユニーク** | **プラグでは MAC そのもの** |
| MAC | 9/9 | ユニーク | 取得経路も確認済み |
| `NodeLabel` | 9/9 | **3 種類のみ** | 同一値が 2 台、空文字が 6 台 |

**スマートプラグ 8 台すべてで `SerialNumber` = MAC アドレスだった。**

```
node  3 sn='XXXXXXXX85BD'  mac=xx:xx:xx:xx:85:bd  → 一致
node  8 sn='XXXXXXXXE984'  mac=xx:xx:xx:xx:e9:84  → 一致
...  8/8 すべて一致
node  1 sn='<32桁hex>'      mac=xx:xx:xx:xx:19:e9  → 不一致（ブリッジのみ別体系）
```

シールの MAC アドレスから機器を一意に特定できる。

**`NodeLabel` は識別子として機能していない。** 6 台が空文字、2 台が同一の値だった。
名前付け機能が必要という判断はこれで裏付けられた。

### ラベル系クラスタは 1 台も実装していない

```
FixedLabel(0x0040) / UserLabel(0x0041) / Descriptor.TagList(0x001D/0x0004)
  → 全 9 ノード・全エンドポイントで不在
```

いずれも optional クラスタであり、実装は機器依存である。

### ブリッジ配下のデータ品質

ブリッジの一部エンドポイントで `ProductName` と `SerialNumber` が
ヌル文字埋め（`'\x00'` × 24）になっていた。
また `BridgedDeviceBasicInformation` に **`UniqueID`（属性 0x0012）が存在しない**
（属性 1, 3, 5, 9, 10, 15, 17 のみ）。

これらのエンドポイントは `ElectricalPowerMeasurement` を持たないため現状は無害だが、
`MatterEndpoint.device_info` への移行時にはサニタイズが必須である。

---

## 5. NodeLabel 書き込みの検証

`BasicInformation.NodeLabel`（0x0005）は仕様上 `writePrivilege="manage"` で書き込み可能。
実機 1 台で検証した（検証後に元の値へ復元済み）。

```
[1] 読み取り (書き込み前): {'0/40/5': 'Test Name'}
[2] 書き込み結果: [{'Path': {'EndpointId': 0, 'ClusterId': 40, 'AttributeId': 5}, 'Status': 0}]
[3] 読み戻し: {'0/40/5': 'MPE-WRITE-TEST'}   → 反映を確認
[4] 復元:     読み戻し='Test Name'            → 原状復帰
```

`Status: 0` は SUCCESS。python-matter-server の資格情報で `manage` 権限を満たしている。

### 長さ制限は「バイト数」

仕様の `maxLength=32` がバイト数か文字数かを実機で確定させた。

```
ASCII 32文字 (32B)   status=0     成功
ASCII 33文字 (33B)   status=135   拒否
日本語 10文字 (30B)   status=0     成功
日本語 11文字 (33B)   status=135   拒否
日本語 32文字 (96B)   status=135   拒否
```

`status=135` は `0x87 = CONSTRAINT_ERROR`。**バイト長で強制されている。**

**NodeLabel に入る日本語は最大 10 文字。**

---

## 6. 名前付け機能の設計判断

### 検討した 3 案

| | 案A: 外部マッピング | 案B: UserLabel 書き込み | 案C: NodeLabel 書き込み |
|---|---|---|---|
| 実現性 | 確実 | **不可**（クラスタ不在） | **実証済み** |
| 粒度 | エンドポイント単位可 | エンドポイント単位 | **ノード単位のみ** |
| 名前の長さ | 無制限 | — | **32 バイト = 日本語 10 文字** |
| 保存先 | 設定ファイル | 機器本体 | 機器本体（不揮発） |
| 他コントローラーから見える | ✗ | ✓ | ✓ |
| 工場リセット | 名前が残る | 名前が消える | 名前が消える |
| エクスポーターの性質 | 読み取り専用のまま | 書き込み権限が必要 | 書き込み権限が必要 |

**案B は却下。** `UserLabel` クラスタが 1 台も実装されていない。

### 結論: 案A を正とし、案C を任意機能として重ねる

- **マッピングファイルを唯一の正**とする。長さ制限がなく、エンドポイント単位に拡張でき、
  工場リセットにも耐える
- **オプションで「機器へ名前を書き戻す」機能**を持たせる。32 バイトに切り詰めて
  `NodeLabel` に書けば、機器付属アプリや Home Assistant からも同じ名前が見える
- エクスポーター本体は読み取り専用のままにし、書き戻しは明示的に叩く別機能とする

マッピングのキーには `serial_number`（= シールの MAC）を使えるようにする。
ユーザーがシールを見て設定を書けることが重要である。
内部では `UniqueID` に解決して保持する。

### Prometheus のラベル設計

**ユーザー命名をメトリクスのラベルに直接載せない。**
名前を変えるたびに時系列が別物になり、グラフが途切れる。

不変の識別子だけを値メトリクスに持たせ、可変メタデータは info メトリクスに分離する。

```
# 値メトリクス — 不変の識別子のみ
matter_active_power_watts{unique_id="...", endpoint_id="1"} 15

# info メトリクス — 可変メタデータはこちらに集約
matter_device_info{unique_id="...", endpoint_id="1",
                   friendly_name="冷蔵庫", node_label="...",
                   serial_number="...", mac="..."} 1
```

```promql
matter_active_power_watts
  * on(unique_id, endpoint_id) group_left(friendly_name)
  matter_device_info
```

既存の `matter_node_label` メトリクスはすでにこの info metric パターンになっている。
設計の方向性は元々正しいので、これを拡張する形が自然である。

---

## 7. 既知の不具合への影響

### エンドポイント衝突（`unique_id` が口を区別できない）

`unique_id` はノード単位の値なので、1 ノードに複数の測定エンドポイントがあると
同一ラベルセットに繰り返し `set()` することになり、最後の値だけが残る。

**本環境では 1 ノード 1 エンドポイントのため顕在化していない。**
電源タップを追加した時点で発火する。修正には `endpoint_id` を識別子に含める必要があり、
既存の時系列は断絶する。

### `endpoint_id` 未定義参照（`matter_electrical_metrics.py:291`）

ノードが `available=False` のとき、束縛されていない `endpoint_id` を参照している。

本環境ではオフライン機の処理順がたまたま最後で、
直前のノードが残した `endpoint_id=1` を拾って偶然正しい値になっている。
**全機が endpoint 1 だから一致しているだけである。**

最初に `ElectricalPowerMeasurement` を持つノードがオフラインになると
`NameError` が発生し、`/metrics` が丸ごと 503 になる。運用中に起こり得る。

### 空文字 `NodeLabel` の扱い

判定が `if metric.node_label is not None:` のため空文字も通り、
`matter_node_label{node_label="_1"}` という無意味な時系列を出している。
本環境では 6 台が該当する。

---

## 未確認事項

- `endpoint_id` のファームウェア更新をまたいだ不変性（仕様の明文を確認できていない）
- ブリッジ以外のベンダーで `SerialNumber` = MAC が成立するか（本環境は 1 ベンダーのみ）

---

## 出典

### 仕様・データモデル

- [connectedhomeip data_model 1.4 — BasicInformationCluster.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/BasicInformationCluster.xml)
- [同 — PowerTopology.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/PowerTopology.xml)
- [同 — ElectricalPowerMeasurement.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/ElectricalPowerMeasurement.xml)
- [同 — Descriptor-Cluster.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/Descriptor-Cluster.xml)
- [同 — Label-Cluster-UserLabelCluster.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/Label-Cluster-UserLabelCluster.xml)
- [同 — DiagnosticsGeneral.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/DiagnosticsGeneral.xml)
- [同 — device_types/ElectricalSensor.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/device_types/ElectricalSensor.xml)
- [同 — device_types/RootNodeDeviceType.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/device_types/RootNodeDeviceType.xml)

### 実装

- [python-matter-server — client/models/node.py](https://github.com/home-assistant-libs/python-matter-server/blob/main/matter_server/client/models/node.py)
- [python-matter-server — client/client.py](https://github.com/home-assistant-libs/python-matter-server/blob/main/matter_server/client/client.py)
- [Home Assistant — components/matter/entity.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/entity.py)
- [Home Assistant — components/matter/helpers.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/helpers.py)
- [Home Assistant — components/matter/sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/sensor.py)

### 解説

- [The Device Data Model | Google Home Developers](https://developers.home.google.com/matter/primer/device-data-model)
- [The Matter Data Model | Silicon Labs](https://docs.silabs.com/matter/latest/matter-fundamentals-data-model/)

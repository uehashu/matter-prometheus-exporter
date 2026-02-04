import asyncio
import logging
import weakref
import warnings
import aiohttp
from dataclasses import dataclass
from matter_server.client import MatterClient
from matter_server.client.models.node import MatterNode
from chip.clusters import Objects as Clusters


@dataclass
class ElectricalNodeMetrics:
    node_id: int
    endpoint_id: int
    unique_id: str | None = None  # ユニークID（文字列）
    serial_number: str | None = None  # シリアル番号（文字列）
    node_label: str | None = None  # ノード名（文字列）
    active_power_w: float | None = None  # 電力（W）
    rms_voltage_v: float | None = None  # 有効電圧（V）
    rms_current_a: float | None = None  # 有効電流（A）
    available: bool = True  # ノードが利用可能かどうか。電源OFFなどで利用不可の場合False


class MatterElectricalMetrics:
    def __init__(
        self,
        ws_server_url: str = "ws://localhost:5580/ws",
        logger: logging.Logger | None = None,
    ):
        """
        コンストラクタ

        :param ws_server_url: Matter Server WebSocketのURL
        :param logger: ロガーインスタンス（省略時はデフォルトロガーを使用）
        """
        self._ws_server_url: str = ws_server_url
        self._client: MatterClient = None
        self._session: aiohttp.ClientSession = None
        self._connected: bool = False

        # ロガーを設定（外部から注入されない場合はデフォルトを作成）
        if logger is None:
            self._logger = logging.getLogger(self.__class__.__name__)
            # デフォルトロガーの場合のみ、基本的なハンドラーを設定
            if not self._logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                    )
                )
                self._logger.addHandler(handler)
                self._logger.setLevel(logging.WARNING)  # デフォルトレベル
        else:
            self._logger = logger

        self._logger.debug(f"MatterElectricalMetrics初期化: {self._ws_server_url}")

        # ファイナライザーを登録
        self._finalizer = weakref.finalize(self, self._cleanup_callback, False)

    @staticmethod
    def _cleanup_callback(was_connected: bool):
        """
        ファイナライザーのクリーンアップコールバック
        """
        if was_connected:
            warnings.warn(
                "MatterElectricalMetricsが適切にdisconnect()されずに破棄されました。"
                "リソースリークの可能性があります。async with文の使用を推奨します。",
                ResourceWarning,
                stacklevel=2,
            )

    async def connect(self):
        """
        Matter Serverとの接続を確立
        """

        if self._connected:
            self._logger.debug("既に接続済みです")
            return

        try:
            self._logger.debug(f"Matter Serverに接続中: {self._ws_server_url}")
            self._session = aiohttp.ClientSession()
            self._client = MatterClient(self._ws_server_url, self._session)
            await self._client.connect()
            self._logger.debug("Matter Serverに接続完了")

            self._connected = True

            # start_listening()をバックグラウンドタスクとして実行
            # 例外処理を追加
            async def listening_with_error_handling():
                try:
                    await self._client.start_listening()
                except Exception as e:
                    self._logger.error(f"リスニングタスクでエラー: {e}")
                    # 接続状態を無効化
                    self._connected = False

            self._listening_task = asyncio.create_task(listening_with_error_handling())
            self._logger.debug("リスニング開始（バックグラウンドタスク）")

            # 接続成功時にファイナライザーを更新（接続状態を記録）
            self._finalizer.detach()
            self._finalizer = weakref.finalize(self, self._cleanup_callback, True)
            self._logger.info("Matter Server接続成功")

        except Exception as e:
            self._logger.error(f"Matter Server接続失敗: {e}")
            await self.disconnect()
            raise ConnectionError(f"Matter Server接続エラー: {e}")

    async def disconnect(self):
        """
        Matter Serverとの接続を切断
        """
        self._logger.debug("接続切断中...")

        # リスニングタスクをキャンセル
        if hasattr(self, "_listening_task") and not self._listening_task.done():
            self._listening_task.cancel()
            try:
                await self._listening_task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.disconnect()
            self._client = None
            self._logger.debug("Matter Clientを切断")

        if self._session:
            await self._session.close()
            self._session = None
            self._logger.debug("HTTPセッションをクローズ")

        self._connected = False

        # 適切にクリーンアップされたのでファイナライザーを無効化
        if hasattr(self, "_finalizer"):
            self._finalizer.detach()

        self._logger.info("Matter Server接続切断完了")

    async def __aenter__(self):
        """
        非同期コンテキストマネージャのエントリポイント
        """
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """
        非同期コンテキストマネージャのエグジットポイント
        """
        await self.disconnect()

    def _ensure_connected(self):
        """
        接続状態を確認
        """
        if not self._connected or not self._client:
            raise ConnectionError("Matter Serverに接続されていません。")

    async def get_basic_information(self) -> dict[int, dict[str, str]] | None:
        """
        Basic Informationクラスタからデータを取得
        """

        self._ensure_connected()
        basic_info = {}

        try:
            nodes: list[MatterNode] = await self._client.get_nodes()
            self._logger.debug(f"取得したノード数: {len(nodes)}")
        except TypeError:
            nodes = self._client.get_nodes()
            self._logger.debug(f"同期的にノードを取得: {len(nodes)}")
        except Exception as e:
            self._logger.error(f"ノード取得エラー: {e}")
            return None

        for node in nodes:
            for endpoint_id, endpoint in node.endpoints.items():
                if not endpoint.has_cluster(Clusters.BasicInformation):
                    continue

                self._logger.debug(
                    f"ノード {node.node_id}, エンドポイント {endpoint_id}: Basic Information取得中"
                )
                info = {}

                unique_id = endpoint.get_attribute_value(
                    Clusters.BasicInformation,
                    Clusters.BasicInformation.Attributes.UniqueID,
                )
                if unique_id is not None:
                    info["unique_id"] = unique_id

                serial_number = endpoint.get_attribute_value(
                    Clusters.BasicInformation,
                    Clusters.BasicInformation.Attributes.SerialNumber,
                )
                if serial_number is not None:
                    info["serial_number"] = serial_number

                node_label = endpoint.get_attribute_value(
                    Clusters.BasicInformation,
                    Clusters.BasicInformation.Attributes.NodeLabel,
                )
                if node_label is not None:
                    info["node_label"] = node_label

                if info:
                    basic_info[node.node_id] = info

        return basic_info

    async def get_metrics_with_electrical(self) -> list[ElectricalNodeMetrics] | None:
        """
        電力メトリクスと基本情報を組み合わせて取得
        """

        self._ensure_connected()
        metrics_with_electrical: list[ElectricalNodeMetrics] = []

        try:
            nodes: list[MatterNode] = await self._client.get_nodes()
            self._logger.debug(f"取得したノード数: {len(nodes)}")
        except TypeError:
            nodes = self._client.get_nodes()
            self._logger.debug(f"同期的にノードを取得: {len(nodes)}")
        except Exception as e:
            self._logger.error(f"ノード取得エラー: {e}")
            return None

        for node in nodes:
            if not node.has_cluster(Clusters.ElectricalPowerMeasurement):
                self._logger.debug(
                    f"ノード {node.node_id}: ElectricalPowerMeasurementクラスターなし"
                )
                continue
            if not node.has_cluster(Clusters.BasicInformation):
                self._logger.debug(
                    f"ノード {node.node_id}: BasicInformationクラスターなし"
                )
                continue

            node_id = node.node_id
            unique_id = None
            serial_number = None
            node_label = None

            for endpoint in node.endpoints.values():
                if endpoint.has_cluster(Clusters.BasicInformation):
                    unique_id = endpoint.get_attribute_value(
                        Clusters.BasicInformation,
                        Clusters.BasicInformation.Attributes.UniqueID,
                    )
                    serial_number = endpoint.get_attribute_value(
                        Clusters.BasicInformation,
                        Clusters.BasicInformation.Attributes.SerialNumber,
                    )
                    node_label = endpoint.get_attribute_value(
                        Clusters.BasicInformation,
                        Clusters.BasicInformation.Attributes.NodeLabel,
                    )
                break  # Basic Informationは1つのエンドポイントにしかないと仮定

            # 電源OFFなどでノードが利用不可の場合、各値取得はスキップ
            if not node.available:
                self._logger.debug(f"ノード {node.node_id}: 利用不可")
                em = ElectricalNodeMetrics(
                    node_id=node_id,
                    unique_id=unique_id,
                    serial_number=serial_number,
                    node_label=node_label,
                    endpoint_id=endpoint_id,
                    available=False,
                )
                metrics_with_electrical.append(em)
                continue

            self._logger.debug(f"ノード {node.node_id}: 利用可能")

            for endpoint_id, endpoint in node.endpoints.items():
                if not endpoint.has_cluster(Clusters.ElectricalPowerMeasurement):
                    continue
                active_power_mw = endpoint.get_attribute_value(
                    Clusters.ElectricalPowerMeasurement,
                    Clusters.ElectricalPowerMeasurement.Attributes.ActivePower,
                )
                rms_voltage_mv = endpoint.get_attribute_value(
                    Clusters.ElectricalPowerMeasurement,
                    Clusters.ElectricalPowerMeasurement.Attributes.RMSVoltage,
                )
                rms_current_ma = endpoint.get_attribute_value(
                    Clusters.ElectricalPowerMeasurement,
                    Clusters.ElectricalPowerMeasurement.Attributes.RMSCurrent,
                )

                active_power_w: float | None = (
                    active_power_mw / 1000.0 if active_power_mw is not None else None
                )
                rms_voltage_v: float | None = (
                    rms_voltage_mv / 1000.0 if rms_voltage_mv is not None else None
                )
                rms_current_a: float | None = (
                    rms_current_ma / 1000.0 if rms_current_ma is not None else None
                )

                em = ElectricalNodeMetrics(
                    node_id=node_id,
                    unique_id=unique_id,
                    serial_number=serial_number,
                    node_label=node_label,
                    endpoint_id=endpoint_id,
                    active_power_w=active_power_w,
                    rms_voltage_v=rms_voltage_v,
                    rms_current_a=rms_current_a,
                    available=True,
                )
                metrics_with_electrical.append(em)

        return metrics_with_electrical


if __name__ == "__main__":

    async def main():
        logger = logging.getLogger()
        # logger.setLevel(logging.DEBUG)
        logger.setLevel(logging.WARNING)
        log_handler = logging.StreamHandler()
        logger.addHandler(log_handler)
        async with MatterElectricalMetrics(
            ws_server_url="ws://192.168.1.7:5580/ws",
            logger=logger,
        ) as mem:

            # 10回試行、各試行ごとに1秒待機
            for i in range(10):
                print(f"試行 {i+1}/10")

                metrics = await mem.get_metrics_with_electrical()
                for em in metrics:
                    print(repr(em))

                if not metrics:
                    print("データが取得できませんでした")

                if i < 9:  # 最後の試行では待機しない
                    await asyncio.sleep(10)

            print("全試行完了")

    asyncio.run(main())

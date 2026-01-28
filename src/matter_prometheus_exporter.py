#!/usr/bin/env python3
"""
Matter Prometheus Exporter

MatterデバイスからElectrical Power Measurementデータを取得し、
Prometheus形式でメトリクスを公開するexporter
"""

import asyncio
import logging
import os
import signal
from prometheus_client import Gauge, generate_latest
from aiohttp import web
from matter_electrical_metrics import MatterElectricalMetrics


class MatterPrometheusExporter:
    def __init__(
        self,
        matter_ws_url: str = "ws://localhost:5580/ws",
        prometheus_port: int = 8000,
        log_level: int = logging.WARNING,
        logger: logging.Logger = None,
    ):
        """
        Matter Prometheus Exporter

        :param matter_ws_url: Matter Server WebSocketのURL
        :param prometheus_port: Prometheusメトリクスを公開するポート
        :param log_level: ログレベル
        :param logger: ロガーインスタンス
        """
        self.matter_ws_url = matter_ws_url
        self.prometheus_port = prometheus_port
        self.log_level = log_level
        self.logger = logger or self._setup_default_logger()

        # Prometheusメトリクスを定義
        self.setup_prometheus_metrics()

        # Matter クライアント
        self.matter_client = None

        # シャットダウンフラグ
        self._shutdown_event = asyncio.Event()

    def _setup_default_logger(self) -> logging.Logger:
        """デフォルトロガーをセットアップ"""
        logger = logging.getLogger(self.__class__.__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )
            logger.addHandler(handler)
            logger.setLevel(self.log_level)  # コンストラクタのlog_levelを使用
            logger.propagate = False  # 親ロガーへの伝播を無効化
        return logger

    def setup_prometheus_metrics(self):
        """Prometheusメトリクスを定義"""
        self.active_power_gauge = Gauge(
            "matter_active_power_watts",
            "Active power consumption in watts",
            ["unique_id"],
        )
        self.rms_voltage_gauge = Gauge(
            "matter_rms_voltage_volts",
            "RMS voltage in volts",
            ["unique_id"],
        )
        self.rms_current_gauge = Gauge(
            "matter_rms_current_amps",
            "RMS current in amperes",
            ["unique_id"],
        )

        self.node_label_gauge = Gauge(
            "matter_node_label",
            "Node label a.k.a Name",
            ["unique_id", "node_label"],
        )

    async def update_metrics(self):
        """Matterデータを取得してPrometheusメトリクスを更新"""
        try:
            # Matter接続状態をチェック
            if self.matter_client is None:
                self.logger.warning("Matterクライアントが初期化されていません")
                return

            metrics_data = await self.matter_client.get_metrics_with_electrical()

            if not metrics_data:
                self.logger.warning("メトリクスデータが取得できませんでした")
                return

            # 既存のメトリクスをクリア
            self.active_power_gauge.clear()
            self.rms_voltage_gauge.clear()
            self.rms_current_gauge.clear()
            self.node_label_gauge.clear()

            # 取得したデータでメトリクスを更新
            for metric in metrics_data:
                unique_id = (
                    metric.unique_id or f"node_{metric.node_id}_ep_{metric.endpoint_id}"
                )
                if metric.active_power_w is not None:
                    self.active_power_gauge.labels(unique_id=unique_id).set(
                        metric.active_power_w
                    )
                if metric.rms_voltage_v is not None:
                    self.rms_voltage_gauge.labels(unique_id=unique_id).set(
                        metric.rms_voltage_v
                    )
                if metric.rms_current_a is not None:
                    self.rms_current_gauge.labels(unique_id=unique_id).set(
                        metric.rms_current_a
                    )
                if metric.node_label is not None:
                    node_label = f"{metric.node_label}_{metric.endpoint_id}"
                    self.node_label_gauge.labels(
                        unique_id=unique_id, node_label=node_label
                    ).set(1)
            self.logger.debug(f"{len(metrics_data)}個のメトリクスを更新しました")

        except Exception as e:
            self.logger.error(f"メトリクス更新エラー: {e}")

    async def handle_metrics(self, request):
        """Prometheusメトリクスエンドポイント（オンデマンド取得）"""
        try:
            # Matter接続状態をチェック
            if self.matter_client is None:
                self.logger.warning("Matter Serverがまだ接続されていません")
                return web.Response(status=503, content_type="text/plain")

            # リアルタイムでメトリクスを更新
            self.logger.debug("メトリクスをリアルタイム取得中...")
            await self.update_metrics()

            # Prometheus形式で出力
            output = generate_latest()
            return web.Response(text=output.decode("utf-8"), content_type="text/plain")
        except Exception as e:
            self.logger.error(f"メトリクス生成エラー: {e}")
            return web.Response(status=500, text="Internal Server Error")

    async def handle_health(self, request):
        """ヘルスチェックエンドポイント"""
        is_connected = (
            self.matter_client is not None
            and hasattr(self.matter_client, "_connected")
            and self.matter_client._connected
        )

        status = "healthy" if is_connected else "unhealthy"
        status_code = 200 if is_connected else 503

        return web.json_response(
            {"status": status, "matter_connected": is_connected}, status=status_code
        )

    async def run(self):
        """Prometheus exporterを実行"""
        self.logger.info(f"Matter Prometheus Exporter開始（オンデマンド方式）")
        self.logger.info(f"Matter Server: {self.matter_ws_url}")
        self.logger.info(f"Prometheus Port: {self.prometheus_port}")

        # aiohttp Webサーバーを設定
        app = web.Application()
        app.router.add_get("/metrics", self.handle_metrics)
        app.router.add_get("/health", self.handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.prometheus_port)

        try:
            await site.start()
            self.logger.info(
                f"Prometheusサーバー起動: http://0.0.0.0:{self.prometheus_port}"
            )
            self.logger.info("エンドポイント: /metrics, /health")

            # Matter クライアントと接続
            async with MatterElectricalMetrics(
                ws_server_url=self.matter_ws_url,
                logger=self.logger.getChild("matter"),
            ) as matter_client:
                self.matter_client = matter_client
                self.logger.info("Matter Serverに接続完了")

                # シャットダウンイベントを待機（オンデマンド方式）
                self.logger.info("オンデマンドメトリクスモードで動作中...")
                await self._shutdown_event.wait()

        except Exception as e:
            self.logger.error(f"予期しないエラー: {e}")
            raise
        finally:
            await runner.cleanup()
            self.logger.info("Prometheus exporter終了")

    def shutdown(self):
        """グレースフルシャットダウン"""
        self.logger.info("シャットダウンシグナルを受信")
        self._shutdown_event.set()


async def main():
    """メイン関数"""
    # 環境変数から設定を読み込み
    matter_ws_url = os.getenv("MATTER_WS_URL", "ws://192.168.1.7:5580/ws")
    prometheus_port = int(os.getenv("PROMETHEUS_EXPORTER_PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "WARNING")

    # ログレベル設定
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Exporterを作成
    exporter = MatterPrometheusExporter(
        matter_ws_url=matter_ws_url,
        prometheus_port=prometheus_port,
        log_level=getattr(logging, log_level.upper()),
    )

    # シグナルハンドラーを設定（Docker対応）
    def signal_handler(signum, frame):
        logging.getLogger().info(f"シグナル {signum} を受信")
        exporter.shutdown()

    # SIGTERM (Docker stop), SIGINT (Ctrl+C) をキャッチ
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        await exporter.run()
    except Exception as e:
        logging.getLogger().error(f"エラー: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

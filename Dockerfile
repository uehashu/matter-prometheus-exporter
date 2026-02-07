# Multi-stage build for optimization
FROM python:3-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# 実行環境のデフォルト設定 (環境変数で上書き可能)
ENV MATTER_WS_URL=ws://host.docker.internal:5580/ws
ENV MATTER_RECONNECT_INTERVAL=10
ENV LOG_LEVEL=INFO

EXPOSE 8000

ENTRYPOINT ["python", "src/matter_prometheus_exporter.py"]

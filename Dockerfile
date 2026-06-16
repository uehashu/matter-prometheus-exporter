# Multi-stage build for optimization
FROM python:3-slim@sha256:b316bdd48110d963d54ce090b4eaeb673cd572393a4fa3867a1824aa477d7940

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

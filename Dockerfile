FROM python:3-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015

WORKDIR /app

# ヘルスチェック用に curl をインストール
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONPATH=/app/src

# 実行環境のデフォルト設定 (環境変数で上書き可能)
ENV MATTER_WS_URL=ws://host.docker.internal:5580/ws
ENV MATTER_RECONNECT_INTERVAL=10
ENV LOG_LEVEL=INFO

EXPOSE 8000

ENTRYPOINT ["python", "-m", "matter_exporter"]

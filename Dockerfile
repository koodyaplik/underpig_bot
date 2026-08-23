FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system bot \
    && useradd --system --gid bot --home-dir /app bot \
    && mkdir -p /data /models \
    && chown bot:bot /data /models

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install ".[voice]"

USER bot

CMD ["python", "-m", "app.main"]

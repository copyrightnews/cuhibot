# ── Base image ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="naimurnstu" \
      description="Cuhi Bot — Telegram media downloader (Instagram/TikTok/Facebook/X)" \
      version="1.1.0"

# ── System dependencies ─────────────────────────────────────────────────────────
# ffmpeg  : video processing (gallery-dl post-processors)
# curl    : useful for Railway health-check debugging
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

# ── Python dependencies (separate layer for caching) ───────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ────────────────────────────────────────────────────────────
COPY bot.py .

# ── Runtime ─────────────────────────────────────────────────────────────────────
# DATA_ROOT and COOKIES_ROOT should be set via environment variables in Railway
# to point at a persistent volume (e.g. /app/data/storage and /app/data/cookies)
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]

# syntax=docker/dockerfile:1

# yt-dlp download engine for Render's free web-service tier.
#
# Replaces the previous cobalt wrapper: cobalt 11.7.1 cannot consume the
# SABR-only formats YouTube now serves this account, while yt-dlp (with
# the Node-based EJS JS-challenge solver) downloads them fine — proven in
# a local POC (full 44-minute audio, byte-exact).
#
# Image contents: Python 3.12 + yt-dlp 2026.10.26 (pip-pinned), the Node
# 24 binary (yt-dlp runs its EJS challenge solver through it), ffmpeg
# (format merging), and a small stdlib HTTP API (server.py). The boot
# script (render-start.sh) converts $YT_COOKIES_JSON into /cookies.txt
# and starts the server; API keys come from $API_KEYS_JSON at runtime.
# No secrets are ever baked into the image or committed to this repo.

FROM node:24-bookworm-slim AS nodebin

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp's JS challenge solver needs a standalone node binary in PATH.
COPY --from=nodebin /usr/local/bin/node /usr/local/bin/node

RUN pip install "yt-dlp==2026.10.26" && yt-dlp --version && node --version

WORKDIR /app
COPY server.py render-start.sh ./
RUN chmod +x render-start.sh

CMD ["/app/render-start.sh"]

# --- build the dashboard -------------------------------------------------
FROM node:24-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
# the dashboard embeds the developer guide (src/components/DeveloperGuide.tsx imports ../../../docs/index.html)
COPY apps/docs/ /docs/
RUN npm run build

# --- api + worker (one service) -----------------------------------------
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app/apps/api
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api/app ./app
COPY apps/widget /app/apps/widget
COPY apps/docs /app/apps/docs
COPY seed /app/seed
COPY --from=web /web/dist /app/apps/web/dist
ENV PATH="/app/apps/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8080
# The LiveKit voice worker (app/voice_agent.py) runs next to the API when LiveKit is configured, restarted if it exits
CMD ["sh", "-c", "if [ -n \"$LIVEKIT_URL\" ]; then (while true; do python -m app.voice_agent start; sleep 5; done) & fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]

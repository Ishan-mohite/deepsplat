# Build from the repository root:  docker build -f backend/Dockerfile -t deepsplat-backend .
# With the hloc VPS (large, torch):  --build-arg WITH_VPS=1   (GPU recommended for map indexing)
FROM python:3.12-slim

ARG WITH_VPS=0
RUN if [ "$WITH_VPS" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
      && rm -rf /var/lib/apt/lists/*; fi
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gcp
COPY localization ./localization
RUN if [ "$WITH_VPS" = "1" ]; then uv pip install --python /app/.venv -r localization/requirements-vps.txt; fi
COPY inspection ./inspection
COPY elevmap ./elevmap
COPY backend ./backend

ENV PATH="/app/.venv/bin:$PATH" PORT=8080
# Cloud Run sets $PORT
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]

FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ARG VERSION=0.0.0+docker
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GEMINI_BRIDGE_HOST=0.0.0.0 \
    GEMINI_BRIDGE_PORT=18088 \
    GEMINI_BRIDGE_ACCOUNTS=/data/accounts.json \
    GEMINI_BRIDGE_LOG_DIR=/data/logs \
    GEMINI_BRIDGE_CONFIG=/app/bridge.yaml \
    GEMINI_BRIDGE_WEB_DIR=/app/web/dist
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_GEMINI_WEBAPI=$VERSION pip install '.[server]' \
    && groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid bridge --no-create-home bridge \
    && mkdir -p /data /app/web/dist \
    && chown bridge:bridge /data \
    && chmod 700 /data
COPY --from=web /build/web/dist/ /app/web/dist/
COPY --chmod=644 bridge.example.yaml /app/bridge.yaml
COPY --chmod=644 docker/entrypoint.py /app/entrypoint.py
USER 10001:10001
EXPOSE 18088
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18088/admin/', timeout=4).close()"
CMD ["python", "/app/entrypoint.py"]

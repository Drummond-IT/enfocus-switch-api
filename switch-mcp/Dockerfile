# Automation web service (and CLI jobs such as the digest) in a container.
# The MCP server itself normally runs on each user's machine (Claude Desktop / Claude Code);
# this image is for the always-on service that Switch and the web portal call.
#
#   docker build -t enfocus-switch-mcp .
#   docker run --rm -p 127.0.0.1:8765:8765 --env-file /etc/enfocus-switch-mcp/config.env \
#     -v /etc/enfocus-switch-mcp:/etc/enfocus-switch-mcp:ro -v enfocus-data:/data enfocus-switch-mcp
#
# See docs/handoff/deployment.md for the full procedure.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Dependencies come from requirements.lock (exported from uv.lock, with hashes), so builds are reproducible.
# Regenerate after changing dependencies:
#   uv export --frozen --no-dev --extra pace --no-emit-project -o requirements.lock
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps . \
    && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin switchmcp \
    && mkdir -p /data \
    && chown switchmcp /data

# Inside the container the service must listen on all interfaces; publish the port only to
# 127.0.0.1 or a reverse proxy on the host (see docs/handoff/deployment.md).
ENV SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8765 \
    SWITCH_DOWNLOAD_DIR=/data/downloads \
    ANALYTICS_DB=/data/analytics.sqlite \
    AUDIT_LOG=/data/audit.jsonl

USER switchmcp
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,ssl,sys,urllib.request as u; tls=bool(os.environ.get('SERVICE_TLS_CERT')); ctx=ssl._create_unverified_context() if tls else None; url=('https' if tls else 'http')+'://127.0.0.1:'+os.environ.get('SERVICE_PORT','8765')+'/health'; sys.exit(0 if u.urlopen(url, timeout=4, context=ctx).status == 200 else 1)"

ENTRYPOINT ["enfocus-switch-mcp"]
CMD ["service"]

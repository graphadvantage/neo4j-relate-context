FROM node:24-bookworm-slim AS frontend
ARG RELATE_MINIFY=0
ENV RELATE_MINIFY=${RELATE_MINIFY}
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run typecheck && npm test && npm run build

FROM node:24-trixie-slim
ARG RELATE_VERSION=1.3.0
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY docker/install-relate.sh /tmp/install-relate.sh
RUN RELATE_VERSION=${RELATE_VERSION} sh /tmp/install-relate.sh \
    && mv /root/.local/bin/relate /usr/local/bin/relate && rm /tmp/install-relate.sh \
    && relate --version --json
WORKDIR /app
COPY pyproject.toml memory_graph.py memory_mcp.py database_lock.py aura_sync.py ./
COPY relate_tap ./relate_tap
COPY docker/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps .
COPY --from=frontend /build/dist ./frontend/dist
COPY frontend/vendor/relate-web/LICENSE ./licenses/relate-web-LICENSE
ENV PATH="/opt/venv/bin:$PATH" RELATE_TAP_SPOOL=/spool RELATE_CONSOLE_DATA=/data RELATE_CONSOLE_HOST=0.0.0.0 RELATE_CONSOLE_FRONTEND=/app/frontend/dist RELATE_TAP_WRITER=console
EXPOSE 8080
ENTRYPOINT ["relate-tap"]

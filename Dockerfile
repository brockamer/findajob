# syntax=docker/dockerfile:1.7

# findajob image
# Base: Python 3.12 on Debian slim. Single stage — aichat-ng and supercronic
# are prebuilt binaries. No compilation needed.

FROM python:3.12-slim-bookworm

ARG AICHAT_NG_VERSION=v0.31.0
ARG AICHAT_NG_ARCH=x86_64-unknown-linux-musl
ARG AICHAT_NG_SHA256=8e1f5a9cf09ae651168f2a425de20b2f6e8702072d47a7052c6229fa366aa57b
ARG SUPERCRONIC_VERSION=v0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
ARG SUPERCRONIC_FILE=supercronic-linux-amd64

# System packages in a single layer. gosu is Debian's drop-privilege helper —
# used by the entrypoint to exec the scheduler as a non-root user matching
# the host's PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        rclone \
        sqlite3 \
        tini \
        gosu \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# aichat-ng — blob42 fork, prebuilt musl binary (static, no libc dep).
# SHA256-verified against the tarball before extraction.
RUN set -eux; \
    curl -fsSL -o /tmp/aichat-ng.tar.gz \
        "https://github.com/blob42/aichat-ng/releases/download/${AICHAT_NG_VERSION}/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}.tar.gz"; \
    echo "${AICHAT_NG_SHA256}  /tmp/aichat-ng.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/aichat-ng.tar.gz -C /tmp; \
    install -m 0755 /tmp/aichat-ng /usr/local/bin/aichat-ng; \
    rm -f /tmp/aichat-ng.tar.gz /tmp/aichat-ng; \
    /usr/local/bin/aichat-ng --version

# supercronic — SHA1-verified.
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/${SUPERCRONIC_FILE}"; \
    echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic; \
    printf '* * * * * true\n' > /tmp/probe-crontab && \
        /usr/local/bin/supercronic -test /tmp/probe-crontab && \
        rm /tmp/probe-crontab

# Editable install — src/ must be present before pip install -e . can register
# the findajob package. Copy pyproject.toml + src/ together, install, then copy
# the rest of the app so source edits (scripts, ops) don't invalidate the
# pip layer cache unnecessarily.
WORKDIR /app
COPY pyproject.toml /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir --break-system-packages -e .

# App code and bundled config.
# /opt/findajob/bundled-config/ holds tracked config files (roles/,
# scoring_schema.json, model_pricing.yaml, reference.docx, strip-bookmarks.lua).
# The entrypoint (created in Task 3) seeds these into /app/config/ on container
# start, AFTER the bind-mount attaches — preventing the bind-mount from
# shadowing tracked config.
COPY scripts/ /app/scripts/
COPY config/ /opt/findajob/bundled-config/
COPY ops/crontab /app/crontab
COPY ops/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Path resolution — tell src/findajob/paths.py where we live.
ENV JSP_BASE=/app

# tini as PID 1 for signal propagation; entrypoint creates the runtime user
# at PUID:PGID, seeds bundled config, and execs the CMD under gosu.
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["supercronic", "/app/crontab"]

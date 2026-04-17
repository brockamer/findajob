# syntax=docker/dockerfile:1.7

# findajob image
# Base: Python 3.12 on Debian slim. Single stage — aichat-ng is a prebuilt binary,
# supercronic is a prebuilt binary; no compilation needed.

FROM python:3.12-slim-bookworm

ARG AICHAT_NG_VERSION=v0.31.0
ARG AICHAT_NG_ARCH=x86_64-unknown-linux-musl
ARG SUPERCRONIC_VERSION=v0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
ARG SUPERCRONIC_FILE=supercronic-linux-amd64

# System packages — keep minimal
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pandoc \
        rclone \
        sqlite3 \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# su-exec (tiny drop-privilege helper; Alpine's version, compiled for Debian as gosu alternative)
# We use gosu here since it's in Debian repos — functionally equivalent to su-exec.
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# aichat-ng (blob42 fork, prebuilt musl binary — static, no libc dep)
RUN set -eux; \
    curl -fsSL -o /tmp/aichat-ng.tar.gz \
        "https://github.com/blob42/aichat-ng/releases/download/${AICHAT_NG_VERSION}/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}.tar.gz"; \
    tar -xzf /tmp/aichat-ng.tar.gz -C /tmp; \
    install -m 0755 "/tmp/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}/aichat-ng" /usr/local/bin/aichat-ng; \
    rm -rf /tmp/aichat-ng.tar.gz "/tmp/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}"; \
    /usr/local/bin/aichat-ng --version

# supercronic
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/${SUPERCRONIC_FILE}"; \
    echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic; \
    /usr/local/bin/supercronic -version

# Python deps — copy pyproject first for better layer caching
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir --break-system-packages -e . || \
    (pip install --no-cache-dir -e . --root-user-action=ignore)

# App code
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY config/roles/ /app/config/roles/
COPY ops/crontab /app/crontab
COPY ops/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Path resolution — tell src/findajob/paths.py where we live
ENV JSP_BASE=/app

# Tini as PID 1 for clean signal propagation; entrypoint drops privileges
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["supercronic", "/app/crontab"]

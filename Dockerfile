# syntax=docker/dockerfile:1.7

# findajob image
# Base: Python 3.12 on Debian slim. Single stage — supercronic is a prebuilt
# binary. No compilation needed.

FROM python:3.12-slim-bookworm

ARG SUPERCRONIC_VERSION=v0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
ARG SUPERCRONIC_FILE=supercronic-linux-amd64

# Build SHA — baked in at image build time so /config/gmail/ disclosure
# banner links audit URLs to the exact commit running.
ARG BUILD_SHA=main
ENV FINDAJOB_BUILD_SHA=${BUILD_SHA}

# System packages in a single layer. gosu is Debian's drop-privilege helper —
# used by the entrypoint to exec the scheduler as a non-root user matching
# the host's PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        sqlite3 \
        tini \
        gosu \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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
COPY docs/ /app/docs/
COPY ops/scheduled-jobs.yaml /app/scheduled-jobs.yaml
COPY ops/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Path resolution — tell src/findajob/paths.py where we live.
ENV JSP_BASE=/app

# tini as PID 1 for signal propagation; entrypoint creates the runtime user
# at PUID:PGID, seeds bundled config, and execs the CMD under gosu.
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["supercronic", "/app/crontab"]

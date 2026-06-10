# syntax=docker/dockerfile:1.7

# findajob image
# Base: Python 3.12 on Debian slim. A throwaway `builder` stage resolves the
# locked dependency set with `uv sync --locked` into a self-contained virtualenv
# at /app/.venv; the runtime stage COPYs that venv in and puts its bin dir at the
# front of PATH, so bare `python3` (from the entrypoint and every cron command)
# resolves to the venv interpreter with findajob + deps already installed. uv and
# its build tooling never enter the final image — only the prebuilt /app/.venv
# crosses the stage boundary.

# builder — produce /app/.venv from uv.lock.
#
# `uv sync --locked` installs the project + its locked dependencies into the
# project virtualenv (UV_PROJECT_ENVIRONMENT=/app/.venv). `--locked` fails the
# build loudly if uv.lock has drifted from pyproject.toml (vs --frozen, which
# would silently use a stale lock). uv validates every distribution against the
# per-package sha256 hashes recorded in uv.lock, so this preserves the
# supply-chain integrity the old `pip install --require-hashes` step provided —
# the lockfile is the hash source rather than an exported requirements.txt.
#
# findajob is `source = { editable = "." }` in uv.lock, so `uv sync` installs it
# EDITABLE: the venv carries a .pth/finder pointing at /app/src rather than a
# built wheel. This is deliberate. findajob ships load-bearing non-Python package
# data (web templates, CSS/JS, the SVG, SQL migrations, the staging persona
# fixture) under src/findajob with no package-data/MANIFEST.in config; a
# `--no-editable` setuptools wheel would silently exclude those files and uvicorn
# would 500 on every page. The editable install exposes the live source tree
# instead, so the runtime stage MUST also COPY src/ to the identical /app/src.
#
# WORKDIR is /app and UV_PROJECT_ENVIRONMENT is /app/.venv in BOTH stages because
# pyvenv.cfg and the editable finder bake absolute paths — the build-time paths
# must equal the runtime paths. UV_PYTHON_DOWNLOADS=0 forces uv to build the venv
# against this base image's /usr/local Python (3.12) instead of a downloaded
# managed interpreter; the runtime stage shares the identical base, so the
# COPYed venv's interpreter home is guaranteed present. uv is pinned from PyPI so
# this needs no external image tag (do not regress to a mutable uv:latest image).
#
# Copy order keeps the deps-only layer (`--no-install-project`) keyed solely on
# pyproject.toml + uv.lock, so it is reused across src-only edits — important for
# the QEMU-emulated linux/arm64 build leg.
FROM python:3.12-slim-bookworm AS builder
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# --break-system-packages: this base image's pip carries a PEP 668
# EXTERNALLY-MANAGED marker (the prior deps stage needed the same flag); uv is a
# throwaway build tool in this discarded stage, so installing it into the
# builder's system Python is harmless. The flag is a no-op if the marker is absent.
RUN pip install --no-cache-dir --break-system-packages uv==0.11.7
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3.12
COPY src/ /app/src/
RUN uv sync --locked --no-dev --python /usr/local/bin/python3.12

# runtime image.
FROM python:3.12-slim-bookworm

# TARGETARCH is set automatically by buildx when --platform is passed
# (linux/amd64 → "amd64", linux/arm64 → "arm64"). Used below to dispatch
# to the per-arch supercronic binary so the same Dockerfile builds
# multi-arch images.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.29

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
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# supercronic — SHA1-verified per arch.
# SHA1s recomputed locally via `curl ... | sha1sum` for v0.2.29:
#   amd64: cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
#   arm64: 512f6736450c56555e01b363144c3c9d23abed4c
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) SUPERCRONIC_FILE=supercronic-linux-amd64; SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b ;; \
        arm64) SUPERCRONIC_FILE=supercronic-linux-arm64; SUPERCRONIC_SHA1SUM=512f6736450c56555e01b363144c3c9d23abed4c ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/${SUPERCRONIC_FILE}"; \
    echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic; \
    printf '* * * * * true\n' > /tmp/probe-crontab && \
        /usr/local/bin/supercronic -test /tmp/probe-crontab && \
        rm /tmp/probe-crontab

WORKDIR /app

# Bring in the prebuilt virtualenv (findajob + locked deps, hash-verified) from
# the builder stage, and put its bin dir at the front of PATH. This is the
# mechanism that replaces the old --break-system-packages install into the
# system Python: bare `python3` from ops/entrypoint.sh and every supercronic cron
# command resolves to /app/.venv/bin/python3, which has findajob and all
# dependencies. gosu (entrypoint) and supercronic both inherit this PATH, so no
# per-command interpreter path is needed.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# src/ must be present at /app/src for the editable install's finder to resolve
# `import findajob` and to expose the package data (web templates, migrations,
# static assets) that a built wheel would not carry.
COPY src/ /app/src/

# App code and bundled config.
# /opt/findajob/bundled-config/ holds tracked config files (roles/,
# scoring_schema.json, model_pricing.yaml, reference.docx, strip-bookmarks.lua).
# The entrypoint seeds these into /app/config/ on container start, AFTER the
# bind-mount attaches — preventing the bind-mount from shadowing tracked config.
COPY scripts/ /app/scripts/
COPY config/ /opt/findajob/bundled-config/
COPY docs/ /app/docs/
COPY ops/scheduled-jobs.yaml /app/scheduled-jobs.yaml
COPY ops/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Path resolution — tell src/findajob/paths.py where we live.
ENV JSP_BASE=/app

# Declare the port uvicorn serves (entrypoint runs it on 8090). This is metadata,
# not a runtime binding — but Fly's "Launch from GitHub" scanner reads EXPOSE to
# set internal_port; without it the scanner defaults to 8080 and the deploy's
# health check targets the wrong port (#1008). Keep this in sync with the
# uvicorn --port in ops/entrypoint.sh and internal_port in fly.toml / ops/fly.toml.
EXPOSE 8090

# tini as PID 1 for signal propagation; entrypoint creates the runtime user
# at PUID:PGID, seeds bundled config, and execs the CMD under gosu.
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["supercronic", "/app/crontab"]

# syntax=docker/dockerfile:1
#
# One stage, one target, one command: the launcher.
#
# There were five targets here, one per backend service, sharing a `base` stage so
# that the lockfile resolved once instead of five times. They are gone because the
# thing they described is gone: `single_process.py` composes the backend surfaces in
# one process, so a second target would name a topology nothing runs, and the `base`
# stage existed only to be shared by targets that no longer exist.
#
# What the collapse does not do is delete a service *module*. `services/kernel`,
# `services/gateway` and the rest are still separate trees that may not import each
# other's internals; the import-boundary tests still police that inside one process.
# The launcher is allowed to see both because it sits outside `services/` by design.

FROM python:3.12-slim-bookworm AS backend

# uv is pinned like everything else. An unpinned tool in the build is the same
# class of problem as an unpinned dependency in the lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Install into the system environment so the command is plain `python`, with no
    # `uv run` resolution step on every container start.
    UV_PROJECT_ENVIRONMENT=/usr/local \
    # `packages/` and `services/` are import roots; see backend/pyproject.toml.
    PYTHONPATH=/app/packages:/app/services \
    # Makes servicekit.runtime bind every interface rather than loopback (R34).
    COMPANY_OS_IN_CONTAINER=1

WORKDIR /app

# Dependencies first, and only the two files that describe them, so editing
# source does not invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY packages/ packages/
COPY services/ services/

# The launcher, copied on its own line because it is not in either import root. It
# lives at the top of the backend tree precisely so that it is not a service, and
# the image's one command is the same command the README documents for contributors
# without Docker — `python single_process.py`, byte-identical minus `uv run`.
COPY single_process.py ./

# Non-root, and owning the one directory a single-process SQLite run would write
# to. Compose points at Postgres, but the image should not require root to work
# either way.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser:appuser /app/var
USER appuser

# Exactly one instance of this, ever. Compose declines to scale it because the
# service has a fixed container name; the store's writer lease is what actually
# enforces it, and covers the restart, the zombie and the host-side launcher
# pointed at the compose store that compose cannot see.
CMD ["python", "single_process.py"]

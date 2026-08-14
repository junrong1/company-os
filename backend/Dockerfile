# syntax=docker/dockerfile:1
#
# One base stage, five service targets. Dependency resolution and installation
# happen once in `base`; each service target adds only its command. Seven
# separate builds would resolve the same lockfile seven times and produce seven
# copies of the same site-packages.

FROM python:3.12-slim-bookworm AS base

# uv is pinned like everything else. An unpinned tool in the build is the same
# class of problem as an unpinned dependency in the lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Install into the system environment so the command is plain `python -m`,
    # with no `uv run` resolution step on every container start.
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

# Non-root, and owning the one directory a single-process SQLite run would write
# to. Compose points at Postgres, but the image should not require root to work
# either way.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser:appuser /app/var
USER appuser


FROM base AS kernel
# Exactly one instance of this, ever. Compose enforces it through a fixed
# container name; from U6 the store enforces it through a writer lease.
CMD ["python", "-m", "kernel.main"]

FROM base AS gateway
CMD ["python", "-m", "gateway.main"]

FROM base AS domain
CMD ["python", "-m", "domain.main"]

FROM base AS agents
CMD ["python", "-m", "agents.main"]

FROM base AS report
CMD ["python", "-m", "report.main"]

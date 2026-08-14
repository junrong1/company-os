"""The FastAPI app every service is built from.

Nothing here knows what any service does. It installs logging, mounts the status
endpoint, and gives a service a place to hang its own startup work through
`on_start` — which is where the kernel puts its store retry and, at U9, its tick
tasks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from servicekit import logging as svclog
from servicekit.status import Dependency, status_router

# A service's startup hook: given the app, return an async teardown callable (or
# None). Returning teardown rather than accepting a second hook keeps setup and
# cleanup symmetric and in one place.
StartHook = Callable[[FastAPI], Any]


def create_service_app(
    service: str,
    *,
    dependencies: list[Dependency] | None = None,
    version_extra: Callable[[], dict[str, Any]] | None = None,
    reports_store: bool = False,
    on_start: StartHook | None = None,
    title: str | None = None,
) -> FastAPI:
    svclog.configure(service)
    log = svclog.get_logger(service)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        teardown = None
        if on_start is not None:
            teardown = on_start(app)
            if hasattr(teardown, "__await__"):
                teardown = await teardown

        log.info("service started")
        try:
            yield
        finally:
            # Cancellation happens after the yield, explicitly. A task whose
            # handle is dropped can be collected mid-execution, and the failure
            # mode is that the work silently stops rather than raising.
            if callable(teardown):
                result = teardown()
                if hasattr(result, "__await__"):
                    await result
            log.info("service stopped")

    app = FastAPI(title=title or f"Company OS — {service}", lifespan=lifespan)
    app.include_router(
        status_router(
            service,
            dependencies=dependencies,
            version_extra=version_extra,
            reports_store=reports_store,
        )
    )
    return app

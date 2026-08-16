"""The one structured status endpoint every service exposes.

Healthy is defined per service, not once for all seven. The kernel without a
store is unhealthy — it cannot append, which is its whole job. The gateway
without a kernel is *healthy and degraded*: it is the surface a contributor
already has open, and it should be able to say "the kernel is down" rather than
going dark alongside it. Encoding that as one blanket rule is how a status page
starts lying.

The payload is deliberately the thing you paste into an issue: who I am, what I
was built from, which versions I believe in, where my store is, and what I can
and cannot reach.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response

from servicekit.probes import ENV_STORE_URL, parse_store_url, store_url
from servicekit.versions import git_sha, resolve_versions


@dataclass(frozen=True)
class Dependency:
    """One thing a service talks to, and whether the service is broken without it.

    `required=False` is not decoration: it is the difference between reporting a
    peer's outage and joining it.
    """

    name: str
    probe: Callable[[], tuple[bool, str]]
    required: bool = True
    note: str = ""


@dataclass
class ServiceStatus:
    service: str
    dependencies: list[Dependency] = field(default_factory=list)
    version_extra: Callable[[], dict[str, Any]] | None = None
    reports_store: bool = False
    #: Which variable names the store this service reports. Named rather than assumed,
    #: because the report reaches the same database through the read-only role and a
    #: status payload that showed the writer's DSN would be reporting a connection this
    #: service does not have.
    store_env_var: str = ENV_STORE_URL

    def build(self) -> tuple[dict[str, Any], bool]:
        checked: list[dict[str, Any]] = []
        healthy = True

        for dependency in self.dependencies:
            try:
                reachable, detail = dependency.probe()
            except Exception as exc:  # noqa: BLE001 - a probe must never 500 the status
                reachable, detail = False, f"probe raised {type(exc).__name__}: {exc}"

            entry = {
                "name": dependency.name,
                "required": dependency.required,
                "reachable": reachable,
                "detail": detail,
            }
            if dependency.note:
                entry["note"] = dependency.note
            checked.append(entry)

            if dependency.required and not reachable:
                healthy = False

        extra = self.version_extra() if self.version_extra else None
        payload: dict[str, Any] = {
            "service": self.service,
            "healthy": healthy,
            "status": "healthy" if healthy else "unhealthy",
            "git_sha": git_sha(),
            "versions": resolve_versions(extra),
            "dependencies": checked,
            "checked_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }

        if self.reports_store:
            target = parse_store_url(store_url(self.store_env_var))
            payload["store"] = {"backend": target.backend, "at": target.describe()}

        degraded = [d["name"] for d in checked if not d["reachable"] and not d["required"]]
        if degraded:
            payload["degraded"] = degraded

        return payload, healthy


def build_status(
    service: str,
    dependencies: list[Dependency] | None = None,
    version_extra: Callable[[], dict[str, Any]] | None = None,
    reports_store: bool = False,
    store_env_var: str = ENV_STORE_URL,
) -> tuple[dict[str, Any], bool]:
    return ServiceStatus(
        service=service,
        dependencies=list(dependencies or []),
        version_extra=version_extra,
        reports_store=reports_store,
        store_env_var=store_env_var,
    ).build()


def status_router(
    service: str,
    dependencies: list[Dependency] | None = None,
    version_extra: Callable[[], dict[str, Any]] | None = None,
    reports_store: bool = False,
    store_env_var: str = ENV_STORE_URL,
) -> APIRouter:
    router = APIRouter()

    # Defined with `def`, not `async def`, on purpose. The probes below block on
    # sockets, and FastAPI runs a synchronous endpoint in its threadpool — so the
    # blocking is off the event loop. An `async def` here would stall every other
    # request and every WebSocket send for the probe timeout.
    @router.get("/status")
    def status(response: Response) -> dict[str, Any]:
        payload, healthy = build_status(
            service, dependencies, version_extra, reports_store, store_env_var
        )
        response.status_code = 200 if healthy else 503
        return payload

    return router

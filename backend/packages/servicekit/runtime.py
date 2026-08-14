"""Where a service binds, and on which port.

R34 in one function. Inside a container a service must bind every interface: a
literal loopback bind there makes it unreachable from sibling containers and from
a published port, which looks like a broken service and is actually a broken bind.
In single-process mode on a laptop, loopback is the correct default and binding
every interface would put a development server on the local network.

The distinction is made by looking for the container marker rather than by asking
the operator to set it right, because the failure it prevents is silent.
"""

from __future__ import annotations

import os
from pathlib import Path

# Only the gateway and the client are published to the host, and only on
# loopback. Everything else is reachable on the compose network alone.
SERVICE_PORTS = {
    "gateway": 8800,
    "kernel": 8801,
    "domain": 8802,
    "agents": 8803,
    "report": 8804,
    "web": 8790,
}

PUBLISHED_SERVICES = frozenset({"gateway", "web"})


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("COMPANY_OS_IN_CONTAINER") == "1"


def bind_host() -> str:
    """All interfaces in a container, loopback outside one. Override intentionally."""
    override = os.environ.get("COMPANY_OS_BIND_HOST")
    if override:
        return override
    return "0.0.0.0" if in_container() else "127.0.0.1"  # noqa: S104 - see docstring


def port_for(service: str) -> int:
    override = os.environ.get("COMPANY_OS_PORT")
    if override:
        return int(override)
    try:
        return SERVICE_PORTS[service]
    except KeyError:
        raise ValueError(f"no port assigned to service {service!r}") from None


def peer_url(service: str, path: str = "/status") -> str:
    """How one service addresses another.

    In compose the service name resolves on the network; on a laptop everything is
    on loopback. Both are overridable per peer, which is what lets a single-process
    run point at a containerised store or a containerised run point at a local one.
    """
    override = os.environ.get(f"COMPANY_OS_{service.upper()}_URL")
    if override:
        return override.rstrip("/") + path
    host = service if in_container() else "127.0.0.1"
    return f"http://{host}:{port_for(service)}{path}"


def uvicorn_target(service: str) -> str:
    return f"{service}.main:app"


def serve(service: str) -> None:
    """Run this service under uvicorn. One instance; see R1 for the kernel."""
    import uvicorn

    uvicorn.run(
        uvicorn_target(service),
        host=bind_host(),
        port=port_for(service),
        log_config=None,  # servicekit.logging owns the handlers
        access_log=False,
    )

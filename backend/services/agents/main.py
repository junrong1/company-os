"""Agents service entrypoint.

Like the domain service, it declares no dependencies: the kernel opens the stream
(R17), so this service is reachable-and-serving or it is not, and it has no
opinion about the kernel's state.
"""

from __future__ import annotations

from servicekit.app import create_service_app
from servicekit.runtime import serve

SERVICE = "agents"

app = create_service_app(SERVICE)


if __name__ == "__main__":
    serve(SERVICE)

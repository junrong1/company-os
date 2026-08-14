"""Domain service entrypoint.

No declared dependencies, and that is structural. The kernel dials *out* to this
service on a stream it opens itself (R17), so the domain service never holds the
kernel's address and never initiates a connection to it. Its health is therefore
just "am I serving" — a domain service that cannot reach the kernel is not a
domain service that is broken.
"""

from __future__ import annotations

from servicekit.app import create_service_app
from servicekit.runtime import serve

SERVICE = "domain"

app = create_service_app(SERVICE)


if __name__ == "__main__":
    serve(SERVICE)

"""Shared service scaffolding: structured logging, version reporting, status.

This package is deliberately not a service. Every service imports it; no service
imports another service, which is the boundary R4 draws and the import-boundary
test enforces.
"""

from servicekit.status import Dependency, build_status
from servicekit.versions import git_sha, resolve_versions

__all__ = ["Dependency", "build_status", "git_sha", "resolve_versions"]

"""R4 and R5, enforced instead of intended.

R4: no service imports another service's internals. R5: the kernel library is
importable and runnable headless with no service, transport or store dependency.

Both are checked two ways, because each way misses something the other catches. A
static scan sees an import that a given test run never executes; a runtime check
sees an import that only happens inside a function body. The pair is what makes
"simcore is pure" a fact rather than a claim in a docstring.

This suite is seeded at U1 while the packages are near-empty on purpose: it is
cheap now, and it fails the moment U3 or U4 reaches for a convenience import.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PACKAGES = BACKEND / "packages"
SERVICES = BACKEND / "services"

SERVICE_NAMES = frozenset({"kernel", "gateway", "domain", "agents", "report"})

# What R5 forbids inside simcore, by category, so a failure message can say which
# rule was broken rather than only which module appeared.
TRANSPORT_MODULES = frozenset(
    {"fastapi", "starlette", "uvicorn", "grpc", "grpc_tools", "httpx", "httpcore", "websockets"}
)
STORE_MODULES = frozenset({"sqlalchemy", "psycopg", "psycopg2", "sqlite3", "asyncpg"})

# `modelgw` (U8) is first-party, so neither category above would catch it — and it
# is where the only provider transport in the tree lives.
PROVIDER_MODULES = frozenset({"modelgw"})


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by a file, including inside functions."""
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module name; it cannot cross a boundary.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])

    return roots


# --- R5: simcore is pure --------------------------------------------------


@pytest.mark.parametrize("path", _python_files(PACKAGES / "simcore"), ids=lambda p: p.name)
def test_simcore_imports_no_service_transport_or_store(path: Path) -> None:
    roots = _imported_roots(path)

    assert not (roots & SERVICE_NAMES), (
        f"{path.name} imports a service ({sorted(roots & SERVICE_NAMES)}). "
        "R5: the kernel library must run headless."
    )
    assert not (roots & TRANSPORT_MODULES), (
        f"{path.name} imports transport ({sorted(roots & TRANSPORT_MODULES)}). "
        "R5: no transport dependency in the kernel library."
    )
    assert not (roots & STORE_MODULES), (
        f"{path.name} imports a store driver ({sorted(roots & STORE_MODULES)}). "
        "R5: the store belongs to the kernel service, not the kernel library."
    )


@pytest.mark.parametrize("path", _python_files(PACKAGES / "simcore"), ids=lambda p: p.name)
def test_simcore_does_not_import_the_model_gateway(path: Path) -> None:
    """R5 again, for the one package the two categories above cannot see.

    `modelgw` is first-party, so it is neither a transport module nor a store module
    by name — but it holds an HTTP client, a provider key and a network timeout. Inside
    the fold, any of the three would be a step whose output depended on what a
    provider said, which is the one thing strict replay cannot reproduce. The bench
    reaches the kernel through the pending-input contract (U10), never by import.
    """
    reached = _imported_roots(path) & PROVIDER_MODULES

    assert not reached, (
        f"{path.name} imports {sorted(reached)}. R5: a model call inside the fold is a "
        "step that cannot replay; the bench answers through the pending-input contract."
    )


def test_importing_simcore_pulls_in_nothing_forbidden() -> None:
    """The runtime half: what actually lands in sys.modules on import.

    Run in a subprocess so the result cannot be contaminated by the rest of the
    suite having already imported FastAPI.
    """
    # Importing bare `simcore` proves nothing: its __init__ is a docstring, so the
    # module graph that actually matters is never loaded. Every module has to be
    # imported explicitly, which is what makes this catch a convenience import added
    # inside one of them.
    probe = """
import importlib, pkgutil, sys
import simcore

for info in pkgutil.iter_modules(simcore.__path__):
    importlib.import_module(f"simcore.{info.name}")

forbidden = {"fastapi", "starlette", "uvicorn", "grpc", "grpc_tools", "sqlalchemy",
             "psycopg", "httpx", "modelgw", "kernel", "gateway", "domain", "agents", "report"}
present = sorted(forbidden & {name.split(".")[0] for name in sys.modules})
print(",".join(present))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={"PYTHONPATH": "packages:services", "PATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"importing simcore pulled in: {result.stdout.strip()}. R5 forbids it."
    )


# --- R4: services do not reach into each other ----------------------------


@pytest.mark.parametrize(
    "path", _python_files(SERVICES), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_no_service_imports_another_services_internals(path: Path) -> None:
    own = path.parent.name
    # Nested modules inside one service are still that service.
    while own not in SERVICE_NAMES and path.parent != SERVICES:
        path_parent = path.parent.parent
        own = path_parent.name
        if path_parent == SERVICES:
            break

    others = SERVICE_NAMES - {own}
    reached = _imported_roots(path) & others

    assert not reached, (
        f"{own} imports {sorted(reached)}. R4: services meet at a proto contract, "
        "not by importing each other."
    )


def test_report_state_reconstruction_resolves_to_the_kernel_library() -> None:
    """The report is a second consumer of the fold, and must not be a second fold.

    A divergent reimplementation in the report service would make the audit artifact
    disagree with the kernel about what happened *while passing its own tests* — and
    the product's central claim, that every report claim resolves to the event that
    produced it, would be false while appearing to hold.

    The rule: any report module that interprets event kinds must get its
    reconstruction from `simcore`, not from a dispatch table of its own. This is a
    forward guard — the report's fold arrives at U8 — so it is vacuous today and
    load-bearing the moment that module is written. It is here because U2 is when the
    contract it depends on exists.
    """
    offenders: list[str] = []

    for path in _python_files(SERVICES / "report"):
        source = path.read_text()
        if "EventKind" not in source:
            continue
        if "simcore" not in _imported_roots(path):
            offenders.append(str(path.relative_to(SERVICES)))

    assert not offenders, (
        "these report modules interpret event kinds without importing simcore, "
        f"which is a second fold: {offenders}"
    )


def test_shared_packages_do_not_import_services() -> None:
    """A shared package importing a service inverts the dependency.

    servicekit is imported by all five services; if it reached back into one of
    them, every service would transitively depend on that one.
    """
    offenders: list[str] = []

    for path in _python_files(PACKAGES):
        reached = _imported_roots(path) & SERVICE_NAMES
        if reached:
            offenders.append(f"{path.relative_to(PACKAGES)} -> {sorted(reached)}")

    assert not offenders, "shared packages must not import services: " + "; ".join(offenders)

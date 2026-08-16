"""The compose topology, asserted against `docker compose config`.

Reading the merged config rather than parsing the YAML is deliberate: it is what
compose will actually run, with anchors resolved and defaults applied, so a test
here cannot pass on a file compose would reject.

These tests need the `docker` CLI but not a running daemon — `config` merges and
validates locally. The scenarios that genuinely need the daemon and built images —
the cold boot that reaches a client, the second boot that reuses the volume, and
the second writer that names the lease holder — were run directly against the
daemon and are recorded in the docstrings of the tests that stand in for them.
Making them part of this suite would make the suite need Docker to be green, which
is the thing that stops contributors running it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_SERVICES = {"postgres", "backend", "web"}
PUBLISHING_SERVICES = {"backend", "web"}

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available"
)


def _config(profiles: str | None = None) -> dict:
    """Merge the compose file the way compose will.

    The default is *no* profile, because that is the one command the README leads
    with and the shape every test here describes. `COMPOSE_PROFILES` is cleared
    rather than left alone, so a contributor who still exports `demo` in their shell
    from the seven-service days does not get a different answer than CI.
    """
    env = {**os.environ}
    env.pop("COMPOSE_PROFILES", None)
    if profiles is not None:
        env["COMPOSE_PROFILES"] = profiles
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config() -> dict:
    return _config()


# --- shape ----------------------------------------------------------------


def test_three_services(config: dict) -> None:
    """Store, backend, client. The five backend services are one container.

    They are not one *module*: `services/kernel`, `services/gateway` and the rest
    are still separate trees that may not import each other's internals, and
    `tests/test_import_boundaries.py` still polices that. What collapsed is the
    deployment, because a service boundary that costs a container and a gRPC hop
    bought nothing on a machine with one operator.
    """
    assert set(config["services"]) == EXPECTED_SERVICES


def test_every_service_defines_a_healthcheck(config: dict) -> None:
    """A per-service definition of healthy, not one blanket claim."""
    missing = [name for name, spec in config["services"].items() if "healthcheck" not in spec]
    assert not missing, f"no healthcheck on: {missing}"


def test_every_service_has_resource_caps(config: dict) -> None:
    """Three containers on an 8 GB laptop is still a constraint worth declaring.

    The backend's cap is larger than any single service's was and smaller than the
    five together, which is the whole arithmetic of the collapse.
    """
    uncapped = [
        name
        for name, spec in config["services"].items()
        if not spec.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    ]
    assert not uncapped, f"no memory cap on: {uncapped}"


# --- the one command (M1) -------------------------------------------------


def test_no_service_is_gated_behind_a_profile(config: dict) -> None:
    """M1, as a property of the file rather than a promise in the README.

    `web` used to declare `profiles: ['demo']`, and that single line is why a bare
    `docker compose up` started six backend services and no client — which looks
    like a crashed container and is actually a service that was never selected. A
    profile anywhere in this file re-opens that failure, so the assertion is over
    every service rather than over `web`.
    """
    profiled = {name for name, spec in config["services"].items() if spec.get("profiles")}
    assert not profiled, f"a bare `docker compose up` would not start: {profiled}"


def test_a_stale_profile_in_the_environment_changes_nothing() -> None:
    """`COMPOSE_PROFILES=demo` was the documented command for a year.

    It has to keep resolving to the same three services rather than to an error or
    to a subset, because the shell that exports it belongs to the contributor least
    likely to reread the README.
    """
    assert set(_config(profiles="demo")["services"]) == EXPECTED_SERVICES
    assert set(_config(profiles="dev")["services"]) == EXPECTED_SERVICES


# --- exposure (R34) -------------------------------------------------------


def test_only_the_backend_and_client_publish_ports(config: dict) -> None:
    publishing = {name for name, spec in config["services"].items() if spec.get("ports")}
    assert publishing == PUBLISHING_SERVICES


def test_published_ports_bind_host_loopback_only(config: dict) -> None:
    """Published on 127.0.0.1, never on every host interface.

    The in-container bind is the opposite and is decided in servicekit.runtime: a
    loopback bind inside a container is unreachable from siblings and from the
    published port.
    """
    for name in PUBLISHING_SERVICES:
        for mapping in config["services"][name]["ports"]:
            assert mapping.get("host_ip") == "127.0.0.1", (
                f"{name} publishes on {mapping.get('host_ip') or 'all interfaces'}"
            )


def test_the_store_is_not_published(config: dict) -> None:
    assert not config["services"]["postgres"].get("ports")


# --- bringing it up twice -------------------------------------------------


def test_the_project_name_is_pinned(config: dict) -> None:
    """Two clones, one stack.

    `name:` fixes the project, so a second `docker compose up` — from this
    directory or from another checkout — reconciles the containers that exist
    instead of building a parallel stack against a second volume. Without it the
    project name is the directory name, and two clones would silently run two
    companies against two stores while sharing one pair of published ports.
    """
    assert config["name"] == "company-os"


def test_no_service_writes_to_a_host_path(config: dict) -> None:
    """So the published ports are the only host resource two stacks contend for.

    Every piece of state lives in a named volume, which is project-scoped. The one
    bind mount is the Postgres init directory, and it is read-only — a stack that
    wrote back into the checkout would make "bring it up twice" mean two processes
    editing one directory.
    """
    writable_binds = [
        (name, mount.get("source"))
        for name, spec in config["services"].items()
        for mount in spec.get("volumes", [])
        if mount.get("type") == "bind" and not mount.get("read_only")
    ]
    assert not writable_binds, f"writable host binds: {writable_binds}"


# --- single instance ------------------------------------------------------


def test_the_backend_has_a_fixed_container_name(config: dict) -> None:
    """The compose-layer stand-in for the second-writer scenario.

    Verified directly against the daemon with a throwaway service: with
    `container_name` set, `docker compose up --scale <svc>=2` prints

        WARNING: The "..." service is using the custom container name "...".
        Docker requires each container to have a unique name. Remove the custom
        name to scale the service

    and starts **zero** containers for that service, while an otherwise identical
    service without `container_name` starts two. So it does prevent a second tick
    loop — but it warns rather than exiting non-zero, which is why the real
    enforcement is the store-side writer lease and not this line.

    Also verified directly, and this is the half compose cannot express: a second
    backend container started by hand against the same store
    (`docker run ... python single_process.py`) exits non-zero with

        the writer lease is held by company-os-backend/<pid> ... Exactly one
        kernel may run against a store

    which names the holder and says when the lease becomes reclaimable. That is the
    path that matters, because it is also the path a contributor takes by accident
    when they run the launcher on the host while the stack is up.
    """
    assert config["services"]["backend"]["container_name"] == "company-os-backend"


def test_backend_restart_is_capped(config: dict) -> None:
    """A crash should stay visible rather than hide inside a restart loop."""
    assert config["services"]["backend"]["restart"] == "on-failure:3"


# --- ordering -------------------------------------------------------------


def test_the_backend_waits_for_a_healthy_store(config: dict) -> None:
    """Its first acts are schema creation, lease acquisition and a DDL check.

    Those are startup failures, not transients, which is the one place a
    healthy-condition dependency is the right tool.

    First boot against an empty volume was run directly: the backend creates the
    schema, writes the DDL version row, takes the lease and serves; the second boot
    against the same volume finds the row, matches it, and reuses the schema
    without re-creating anything. A *mismatched* row refuses and names the remedy,
    which is a wipe rather than a migration — `LogStore.check_ddl_version` carries
    that sentence, and the README's stranger path says so.
    """
    depends = config["services"]["backend"]["depends_on"]
    assert set(depends) == {"postgres"}
    assert depends["postgres"]["condition"] == "service_healthy"


def test_the_client_waits_only_for_the_backend_to_start(config: dict) -> None:
    """Started, never healthy, and now for two reasons rather than one.

    nginx resolves `backend` at config load, so an upstream that does not exist yet
    is a refusal to start rather than a 502 — the edge has to exist. But it stays a
    started-condition edge because the client is the surface you already have open:
    a `web` that went down with the backend would turn one outage into two and
    explain neither, where the page's own status fallback explains it.
    """
    depends = config["services"]["web"]["depends_on"]
    assert set(depends) == {"backend"}
    assert depends["backend"]["condition"] == "service_started"


# --- credentials ----------------------------------------------------------


def test_the_report_keeps_the_read_only_role(config: dict) -> None:
    """A credential outlives every future code path a test did not anticipate.

    The report now folds the log inside the process that appends to it, which is
    the reason to keep this rather than the reason to drop it: a SQLAlchemy engine
    is per-DSN, not per-process, so a second DSN is still a real boundary and it is
    the only one that survives U20 through U22 writing the report into the writer's
    process.
    """
    url = config["services"]["backend"]["environment"]["COMPANY_OS_REPORT_STORE_URL"]
    assert url.startswith("postgresql+psycopg://report_reader:")


def test_the_backend_connects_as_the_owner(config: dict) -> None:
    url = config["services"]["backend"]["environment"]["COMPANY_OS_STORE_URL"]
    assert "report_reader" not in url
    assert url.startswith("postgresql+psycopg://companyos:")


def test_the_two_credentials_address_one_store(config: dict) -> None:
    """Different roles, same database. Two stores would be two logs."""
    environment = config["services"]["backend"]["environment"]
    writer = environment["COMPANY_OS_STORE_URL"]
    reader = environment["COMPANY_OS_REPORT_STORE_URL"]
    assert writer.rsplit("@", 1)[1] == reader.rsplit("@", 1)[1]

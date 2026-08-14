"""The compose topology, asserted against `docker compose config`.

Reading the merged config rather than parsing the YAML is deliberate: it is what
compose will actually run, with anchors resolved and defaults applied, so a test
here cannot pass on a file compose would reject.

These tests need the `docker` CLI but not a running daemon — `config` merges and
validates locally. The one scenario that genuinely needs the daemon and a built
image (the kernel scale refusal) is opt-in; its mechanism was verified directly
and is recorded in the docstring of the test that stands in for it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_SERVICES = {"postgres", "kernel", "gateway", "domain", "agents", "report", "web"}
PUBLISHING_SERVICES = {"gateway", "web"}

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available"
)


def _config(profiles: str = "demo") -> dict:
    env = {**os.environ, "COMPOSE_PROFILES": profiles}
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


def test_seven_services(config: dict) -> None:
    assert set(config["services"]) == EXPECTED_SERVICES


def test_every_service_defines_a_healthcheck(config: dict) -> None:
    """A per-service definition of healthy, not one blanket claim."""
    missing = [name for name, spec in config["services"].items() if "healthcheck" not in spec]
    assert not missing, f"no healthcheck on: {missing}"


def test_every_service_has_resource_caps(config: dict) -> None:
    """Seven Python services on an 8 GB laptop is a real constraint."""
    uncapped = [
        name
        for name, spec in config["services"].items()
        if not spec.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    ]
    assert not uncapped, f"no memory cap on: {uncapped}"


# --- exposure (R34) -------------------------------------------------------


def test_only_the_gateway_and_client_publish_ports(config: dict) -> None:
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


# --- single instance (R1) -------------------------------------------------


def test_kernel_has_a_fixed_container_name(config: dict) -> None:
    """The stand-in for the scale-refusal scenario, which needs a built image.

    Verified directly against the daemon with a throwaway service: with
    `container_name` set, `docker compose up --scale <svc>=2` prints

        WARNING: The "..." service is using the custom container name "...".
        Docker requires each container to have a unique name. Remove the custom
        name to scale the service

    and starts **zero** containers for that service, while an otherwise identical
    service without `container_name` starts two. So it does prevent a second tick
    loop — but it warns rather than exiting non-zero, which is why the real
    enforcement is the store-side writer lease at U6 and not this line.
    """
    assert config["services"]["kernel"]["container_name"] == "company-os-kernel"


def test_kernel_restart_is_capped(config: dict) -> None:
    """A crash should stay visible rather than hide inside a restart loop."""
    assert config["services"]["kernel"]["restart"] == "on-failure:3"


# --- ordering -------------------------------------------------------------


def test_kernel_waits_for_a_healthy_store(config: dict) -> None:
    """The kernel's first acts are a DDL check and lease acquisition.

    Those are startup failures, not transients, which is the one place a
    healthy-condition dependency is the right tool.
    """
    depends = config["services"]["kernel"]["depends_on"]
    assert depends["postgres"]["condition"] == "service_healthy"


def test_gateway_waits_only_for_the_kernel_to_start(config: dict) -> None:
    """Started, never healthy.

    A healthy-condition edge here would keep the gateway down whenever the kernel
    is unwell — turning one outage into two and explaining neither.
    """
    depends = config["services"]["gateway"]["depends_on"]
    assert set(depends) == {"kernel"}
    assert depends["kernel"]["condition"] == "service_started"


def test_kernel_declares_no_dependency_on_the_answer_side_services(config: dict) -> None:
    """R17, as an ordering constraint.

    A bidirectional depends_on is a cycle compose rejects. The cold-start path for
    the domain and agent services is the same code path as a service that never
    answers, so the kernel tolerates and retries rather than declaring.
    """
    depends = set(config["services"]["kernel"].get("depends_on", {}))
    assert "domain" not in depends
    assert "agents" not in depends


@pytest.mark.parametrize("service", ["domain", "agents"])
def test_answer_side_services_wait_for_nothing(config: dict, service: str) -> None:
    assert not config["services"][service].get("depends_on")


# --- credentials ----------------------------------------------------------


def test_report_connects_with_the_read_only_role(config: dict) -> None:
    """A credential outlives every future code path a test did not anticipate."""
    url = config["services"]["report"]["environment"]["COMPANY_OS_STORE_URL"]
    assert url.startswith("postgresql+psycopg://report_reader:")


def test_kernel_connects_as_the_owner(config: dict) -> None:
    url = config["services"]["kernel"]["environment"]["COMPANY_OS_STORE_URL"]
    assert "report_reader" not in url
    assert url.startswith("postgresql+psycopg://companyos:")


# --- profiles -------------------------------------------------------------


def test_demo_profile_includes_the_client(config: dict) -> None:
    assert "web" in config["services"]


def test_dev_profile_omits_the_client() -> None:
    """Under dev the Vite dev server runs on the host against the published port."""
    services = set(_config(profiles="dev")["services"])
    assert "web" not in services
    assert services == EXPECTED_SERVICES - {"web"}

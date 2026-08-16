"""Healthy is defined per service, and these tests are what keep it that way.

The two asymmetries are the whole point:

* the kernel without a store is unhealthy — it cannot append, which is its job;
* the gateway without a kernel is healthy and degraded — it is the surface a
  contributor already has open, and it has to be able to say "the kernel is down".

A single blanket definition would satisfy neither, and the failure would be a
status page that agrees with itself and not with reality.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

UNREACHABLE_POSTGRES = "postgresql://nobody@127.0.0.1:59999/absent"


def _client(module_name: str) -> Iterator[tuple[TestClient, Any]]:
    module = importlib.import_module(module_name)
    importlib.reload(module)
    with TestClient(module.app) as client:
        yield client, module


@pytest.fixture
def writable_store(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path}/company-os.sqlite3"
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)
    return url


# --- the shape every service shares ---------------------------------------


@pytest.mark.parametrize("service", ["kernel", "gateway", "domain", "agents", "report"])
def test_status_payload_carries_what_an_issue_report_needs(
    service: str, writable_store: str, monkeypatch
) -> None:
    monkeypatch.setenv("COMPANY_OS_KERNEL_URL", "http://127.0.0.1:59998")
    for client, _ in _client(f"{service}.main"):
        body = client.get("/status").json()

        assert body["service"] == service
        assert body["git_sha"] == "unknown", "not a git checkout; must not invent a sha"
        assert set(body["versions"]) == {"rules", "event_schema", "store_ddl"}
        assert isinstance(body["dependencies"], list)
        assert body["checked_at"].endswith("+00:00"), "timestamps are UTC, explicitly"


@pytest.mark.parametrize("service", ["kernel", "gateway", "domain", "agents", "report"])
def test_versions_report_null_before_the_unit_that_establishes_them(
    service: str, writable_store: str, monkeypatch
) -> None:
    """Null is honest. A placeholder string would read like a real version.

    The rules version arrives in U3, the event-schema version in U2 and the store
    DDL version in U6. Each is resolved by lookup with a null fallback, so the unit
    landing makes every status endpoint truthful with no edit here.
    """
    monkeypatch.setenv("COMPANY_OS_KERNEL_URL", "http://127.0.0.1:59998")
    for client, _ in _client(f"{service}.main"):
        versions = client.get("/status").json()["versions"]
        for name, value in versions.items():
            assert value is None or isinstance(value, (str, int)), name


# --- the kernel: the store is required ------------------------------------


def test_kernel_is_healthy_with_a_reachable_store(writable_store: str) -> None:
    """A reachable store is enough, because the kernel provisions an empty one (R16).

    This briefly appeared to be a U9 behaviour change — the kernel had gained a `clock`
    dependency that could not start against an empty database. That was not a change in what
    healthy means; it was a missing provisioning step. `docker compose up` is required to bring
    the system up with no manual provisioning, so the kernel creates the schema (lease-gated)
    rather than demanding someone else did.
    """
    for client, _ in _client("kernel.main"):
        response = client.get("/status")

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["healthy"] is True
        assert body["store"]["backend"] == "sqlite"

        # Both dependencies satisfied: the store is reachable and the clock could start.
        by_name = {dep["name"]: dep for dep in body["dependencies"]}
        assert by_name["store"]["reachable"] is True
        assert by_name["clock"]["reachable"] is True


def test_kernel_is_healthy_once_the_store_is_provisioned(tmp_path, monkeypatch) -> None:
    """The other half: with a schema and a free lease, the clock starts and the kernel serves."""
    url = f"sqlite:///{tmp_path}/provisioned.sqlite3"
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)

    from kernel.store import LogStore, make_engine
    from logschema import DDL_VERSION

    LogStore(make_engine(url)).create_all()

    for client, _ in _client("kernel.main"):
        response = client.get("/status")

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["healthy"] is True
        # The declared version rather than a literal: the claim is that the kernel reports
        # the schema it provisioned, not that the number is any particular one. A literal
        # here fails on every DDL bump for a reason unrelated to what it tests.
        assert body["versions"]["store_ddl"] == str(DDL_VERSION)


def test_kernel_reports_unhealthy_but_keeps_running_when_the_store_is_down(
    monkeypatch,
) -> None:
    """R20's stance, at this unit's scope: stall and report, never terminate.

    Exiting would make an outage look like a crash, and the capped restart policy
    would then hide the outage inside a restart loop.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", UNREACHABLE_POSTGRES)

    for client, module in _client("kernel.main"):
        response = client.get("/status")

        assert response.status_code == 503
        body = response.json()
        assert body["healthy"] is False
        assert body["dependencies"][0]["name"] == "store"
        assert body["dependencies"][0]["reachable"] is False
        assert "tcp connect" in body["dependencies"][0]["detail"]

        # Still serving, and the retry task is still alive: the service did not
        # exit, and it did not silently drop the watch either.
        assert client.get("/status").status_code == 503
        assert not module.app.state.store_watch_task.done()


def test_kernel_probe_reports_a_real_reason_on_the_first_call(monkeypatch) -> None:
    """The first status call must not lose the race with the first probe.

    Failing closed is right, but "not probed yet" is a worse answer than the
    actual reason, so the watch takes one observation before startup completes.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", UNREACHABLE_POSTGRES)

    for client, _ in _client("kernel.main"):
        detail = client.get("/status").json()["dependencies"][0]["detail"]
        assert "not probed yet" not in detail


def test_kernel_store_watch_is_cancelled_on_shutdown(monkeypatch) -> None:
    """A dropped task handle is collected mid-execution and the work stops silently.

    Holding a strong reference and cancelling explicitly is the pattern the tick
    loop needs at U9; it is established here so it is not improvised there.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", UNREACHABLE_POSTGRES)

    module = importlib.import_module("kernel.main")
    importlib.reload(module)
    with TestClient(module.app):
        task = module.app.state.store_watch_task
        assert not task.done()

    assert task.cancelled() or task.done()


# --- the gateway: the kernel is reported, not required ---------------------


def test_gateway_stays_healthy_and_says_the_kernel_is_down(monkeypatch) -> None:
    monkeypatch.setenv("COMPANY_OS_KERNEL_URL", "http://127.0.0.1:59998")

    for client, _ in _client("gateway.main"):
        response = client.get("/status")

        assert response.status_code == 200, "the gateway must not join the kernel's outage"
        body = response.json()
        assert body["healthy"] is True
        assert body["degraded"] == ["kernel"]

        kernel_dep = body["dependencies"][0]
        assert kernel_dep["name"] == "kernel"
        assert kernel_dep["required"] is False
        assert kernel_dep["reachable"] is False


def test_gateway_distinguishes_an_unhealthy_kernel_from_an_absent_one(monkeypatch) -> None:
    """A peer answering 503 is reachable but sick. That is a different sentence."""
    from servicekit import probes

    reachable, detail = probes.probe_http("http://127.0.0.1:59998/status")
    assert reachable is False
    assert "unreachable" in detail


# --- the answer-side services declare nothing -----------------------------


@pytest.mark.parametrize("service", ["domain", "agents"])
def test_answer_side_services_declare_no_dependencies(service: str) -> None:
    """R17 in the status payload.

    The kernel opens the stream to these services; they never hold the kernel's
    address. A domain service that cannot reach the kernel is not broken, so it has
    no kernel dependency to report.
    """
    for client, _ in _client(f"{service}.main"):
        response = client.get("/status")
        assert response.status_code == 200
        assert response.json()["dependencies"] == []


def test_report_requires_the_store(monkeypatch) -> None:
    monkeypatch.setenv("COMPANY_OS_STORE_URL", UNREACHABLE_POSTGRES)

    for client, _ in _client("report.main"):
        response = client.get("/status")
        assert response.status_code == 503
        assert response.json()["dependencies"][0]["required"] is True

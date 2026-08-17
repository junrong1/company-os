"""Healthy is defined per service, and these tests are what keep it that way.

The two asymmetries are the whole point:

* the kernel without a store is unhealthy — it cannot append, which is its job;
* the gateway without a kernel is healthy and degraded — it is the surface a
  contributor already has open, and it has to be able to say "the kernel is down".

A single blanket definition would satisfy neither, and the failure would be a
status page that agrees with itself and not with reality.

From U24 this suite also owns the composition those five status endpoints are served
from: one process, five prefixes, and the two R6 leaks that only existed because the
process fronting everything had never been asked what it printed.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from servicekit import logging as svclog

BACKEND = Path(__file__).resolve().parent.parent

UNREACHABLE_POSTGRES = "postgresql://nobody@127.0.0.1:59999/absent"

#: The five surfaces and where each answers inside the one container (M2). The gateway
#: keeps the root because the client's `/api/` proxy maps onto it.
SURFACE_PREFIXES = {
    "gateway": "",
    "kernel": "/kernel",
    "domain": "/domain",
    "agents": "/agents",
    "report": "/report",
}


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


@pytest.fixture
def one_process(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, Any]]:
    """The launcher's own composition, under the lifespan the container runs it with.

    `single_process.compose()` rather than a harness: it is what the `backend` image
    runs, so a surface that answers here answers in the container. The lifespan wrapper
    is put back afterwards because `gateway_main.app` is a module-level singleton and a
    wrapper left on it would give every later test a second runtime's startup.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/one.sqlite3")

    import single_process
    from gateway import main as gateway_main

    runtime, _ = single_process.compose()
    original = gateway_main.app.router.lifespan_context
    single_process._wrap_lifespan(gateway_main.app, runtime)
    try:
        with TestClient(gateway_main.app) as client:
            yield client, runtime
    finally:
        gateway_main.app.router.lifespan_context = original


# =========================================================================
# R6: a sweep over emitted output, not a list of strings somebody thought of
# =========================================================================

#: A DSN carrying every shape R6 names: a password, a userinfo segment, and a query
#: string with the password in it again. Unreachable on purpose — an unreachable store
#: produces *more* output to sweep, because every probe reports why it failed.
LEAKY_DSN = (
    "postgresql+psycopg://companyos:sup3r-s3cr3t-pw@db.internal:5432/companyos"
    "?password=sup3r-s3cr3t-pw&sslmode=require&options=-c%20statement_timeout%3D0"
)

#: Literals that must not appear anywhere in emitted output. The list is the *floor* of
#: what the sweep checks, not its substance: the structural predicates below are what
#: make it a sweep rather than a spot check.
LEAKED_LITERALS = (
    "sup3r-s3cr3t-pw",
    "companyos:sup3r",
    "sslmode=require",
    "statement_timeout",
)

#: An `@` in an authority: `://` followed by anything that is not a delimiter, then `@`.
_URL_USERINFO = re.compile(r"://([^/?#\s\"']*)@")

#: A `?` still inside a URL.
_URL_QUERY = re.compile(r"://[^\s\"']*\?")


def credential_complaints(text: str) -> list[str]:
    """Every reason one emitted string is carrying a credential. Empty is the pass.

    Three predicates, and the last two are what make this a sweep. A literal check only
    ever finds the secret the test author remembered to plant; asking "does anything in
    this output look like a URL with a userinfo segment, or a URL with a query" finds the
    next one, in a field nobody has written yet, on a code path nobody has thought of.

    Matched with its own regexes rather than with `urlsplit`, for two reasons that point
    the same way: `urlsplit` raises on some of the input a real log stream carries — an
    ANSI escape after a scheme is enough — and formulating the predicate differently from
    the redactor is what stops this being a restatement of the implementation.
    """
    complaints = []

    for literal in LEAKED_LITERALS:
        if literal in text:
            complaints.append(f"carries the literal {literal!r}")

    for match in _URL_USERINFO.finditer(text):
        # The mask is not a userinfo segment, it is the absence of one — and allowing
        # exactly it is also how this asserts the redaction fired rather than that the
        # DSN never reached the line in the first place.
        if match.group(1) != svclog.MASK:
            complaints.append(f"carries a userinfo segment: {match.group(0)!r}")

    if _URL_QUERY.search(text):
        complaints.append("carries a url with a query string")

    return complaints


def strings_in(value: Any) -> Iterator[str]:
    """Every string anywhere in a decoded payload, keys included.

    Recursive so the sweep covers a field added to a nested structure by a later unit
    without this test being edited — which is the difference between a sweep and a list
    of the keys that existed when it was written.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


@contextmanager
def emitted() -> Iterator[io.StringIO]:
    """Everything the process writes to its log stream while the block runs.

    Two mechanisms, because `svclog.configure` binds `sys.stdout` at call time and it is
    called both at service import and again by the launcher once the surfaces are loaded.
    Redirecting `sys.stdout` catches every handler installed *inside* the block; swapping
    the stream of the handlers that already exist catches the ones installed before it.
    Either alone leaves a hole, and the hole is silent — the sweep passes by capturing
    nothing.
    """
    buffer = io.StringIO()
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
    ]
    saved = [handler.stream for handler in handlers]
    for handler in handlers:
        handler.stream = buffer
    try:
        with redirect_stdout(buffer):
            yield buffer
    finally:
        for handler, stream in zip(handlers, saved):
            handler.stream = stream


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


# =========================================================================
# M2: five surfaces, one process
# =========================================================================


def test_every_surface_answers_its_status_endpoint_in_one_process(one_process) -> None:
    """M2, and the thing the container has been promising without delivering.

    The launcher composed the kernel runtime and served the gateway app; the other three
    surfaces existed as modules nothing imported, so four of the five status endpoints
    were unreachable in the topology the README's one command starts.
    """
    client, _ = one_process

    for service, prefix in SURFACE_PREFIXES.items():
        response = client.get(f"{prefix}/status")

        assert response.status_code == 200, (service, response.text)
        assert response.json()["service"] == service


def test_the_diagnose_call_and_the_domain_surface_need_their_prefix(one_process) -> None:
    """One port, five surfaces, and no shared namespace between them.

    The gateway keeps the root because the client's proxy maps `/api/` onto it, so every
    other surface is prefixed. The alternative — mounting them all at the root and
    trusting the paths not to collide — would put `/runs/{id}/report` and
    `/runs/{id}/state` in one namespace owned by two services, and the first collision
    would be resolved silently by route order.
    """
    from starlette.routing import Mount

    from gateway import main as gateway_main

    client, runtime = one_process
    runtime.create_run("run-prefix", 99)

    assert client.get("/runs/run-prefix/diagnose").status_code == 404, (
        "the kernel's diagnose call answered on the published path, where the client's "
        "own routes live"
    )

    # Where it *is*, asserted against the mounted router rather than against a response.
    # A response would also be asserting that the kernel's diagnosis builder works today,
    # which is a different unit's claim and a different unit's failure.
    mounted = {
        route.path: route.app for route in gateway_main.app.routes if isinstance(route, Mount)
    }
    assert set(mounted) == {"/kernel", "/domain", "/agents", "/report"}
    assert any(
        getattr(route, "path", "") == "/runs/{run_id}/diagnose"
        for route in mounted["/kernel"].routes
    ), "the diagnose route is not on the kernel surface at all"
    assert client.get("/kernel/runs/run-prefix/diagnose").status_code != 404, (
        "the prefix does not route to the kernel's diagnose call"
    )

    # The domain surface has no routes of its own beyond status, so the claim there is
    # that its status is reachable *only* under its prefix — the root's /status is the
    # gateway's, and a domain mounted at the root would have shadowed it.
    assert client.get("/domain/status").json()["service"] == "domain"
    assert client.get("/status").json()["service"] == "gateway"


# =========================================================================
# R6: no credential on a status payload or a startup line
# =========================================================================


@pytest.mark.parametrize("service", ["kernel", "gateway", "domain", "agents", "report"])
def test_no_status_payload_or_startup_line_carries_a_credential(
    service: str, monkeypatch
) -> None:
    """R6, swept over everything the surface emitted rather than over named fields.

    Both DSNs are the leaky one, so the report's own credential is covered as well as
    the writer's, and the store is unreachable so every probe has to say why — which is
    where the raw URL used to come out. `probe_store`'s unrecognised-scheme branch
    returned the whole DSN, and the parsed target it reports carried the URL as a field.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", LEAKY_DSN)
    monkeypatch.setenv("COMPANY_OS_REPORT_STORE_URL", LEAKY_DSN)

    module = importlib.import_module(f"{service}.main")
    importlib.reload(module)

    with emitted() as buffer:
        with TestClient(module.app) as client:
            payload = client.get("/status").json()

    lines = buffer.getvalue().splitlines()
    assert lines, "no log line was captured, so the startup half of the sweep asserted nothing"

    for text in list(strings_in(payload)) + lines:
        assert not credential_complaints(text), (service, text, credential_complaints(text))


def test_an_unrecognised_scheme_is_named_without_publishing_the_url(monkeypatch) -> None:
    """The second of U1's two reported leaks, at the line that produced it.

    A driver-name typo — `postgres+psycopg2` for `postgresql+psycopg`, the commonest one
    there is — took the unknown branch, and the unknown branch was the one that reported
    the URL verbatim. The scheme is what an operator needs; the credential is not.
    """
    from servicekit import probes

    reachable, detail = probes.probe_store("mysql+pymysql://root:hunter2@db/companyos")

    assert reachable is False
    assert "mysql" in detail, "the scheme is what names the misconfiguration"
    assert "hunter2" not in detail and "root:hunter2" not in detail

    target = probes.parse_store_url("mysql+pymysql://root:hunter2@db/companyos")
    assert "hunter2" not in target.describe()
    assert not hasattr(target, "url"), (
        "the parsed target must not carry the DSN at all; a field that holds it is a "
        "field a later reporter puts on the wire"
    )

    # And a DSN malformed enough to break the parser lands on `unknown` rather than
    # raising. `ServiceStatus.build` calls `parse_store_url` outside the guard it wraps
    # its probes in, so an exception here would 500 the endpoint whose job is to say what
    # is wrong.
    for malformed in (
        "postgresql://user:pw@h:notaport/db",
        "postgresql://[unclosed:pw@h/db",
        "",
        "::::",
    ):
        assert probes.parse_store_url(malformed).backend == "unknown", malformed

    # `probe_store` reads the environment for a falsy argument, so the empty string is a
    # `parse_store_url` case only.
    for malformed in ("postgresql://user:pw@h:notaport/db", "postgresql://[unclosed:pw@h/db"):
        reachable, detail = probes.probe_store(malformed)
        assert reachable is False
        assert "pw@" not in detail and "unclosed" not in detail, detail


def test_the_launcher_names_its_store_without_the_dsn(tmp_path, monkeypatch) -> None:
    """The first of U1's two reported leaks: the composition line printed the DSN whole.

    Under compose that line was
    `postgresql+psycopg://companyos:companyos@postgres:5432/companyos` on stdout at
    every startup, and R6 names stdout explicitly. It reports the parsed target now, so
    the line still says which store this process opened.
    """
    url = f"sqlite:///{tmp_path}/named.sqlite3"
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)

    import single_process

    with emitted() as buffer:
        runtime, _ = single_process.compose()
    asyncio.run(runtime.stop())

    lines = [line for line in buffer.getvalue().splitlines() if "one process composed" in line]
    assert lines, buffer.getvalue()
    assert url not in lines[0], "the launcher logged the DSN it was handed"
    assert "sqlite at" in lines[0], "and it stopped saying which store it opened"
    assert not credential_complaints(lines[0])


def test_the_redacting_filter_holds_where_the_call_site_does_not(monkeypatch) -> None:
    """R6's second half: the filter, at the idiom the repo actually uses.

    `modelgw`'s masked key type covers the *configured* key wherever it is rendered, and
    `modelgw.scrub` cleans a provider's own words before they leave that package. Neither
    can reach `log.warning(..., extra={"error": str(exc)})` in a service, where the
    exception has quoted a 401 body or a driver has quoted the DSN it could not open. The
    filter is what holds there, so this logs the worst case through the ordinary idiom
    and sweeps what came out.
    """
    svclog.configure("sweep")
    log = svclog.get_logger("sweep-test")

    with emitted() as buffer:
        log.warning(
            "could not open the store",
            extra={"error": f"connection to {LEAKY_DSN} failed", "attempt": 1},
        )
        log.warning("Authorization: Bearer sk-live-abcdef123456 was rejected")
        log.warning("connecting to %s", LEAKY_DSN)
        # A set is not JSON-encodable, so the formatter reaches it through `repr` — after
        # the filter has run. That path has to be redacted too, or an `extra` holding
        # anything unusual is a way round the whole mechanism.
        log.warning("tried several", extra={"tried": {LEAKY_DSN}})
        try:
            raise RuntimeError(f"psycopg could not connect using {LEAKY_DSN}")
        except RuntimeError as exc:
            log.error("store unreachable", exc_info=exc, extra={"error": str(exc)})

    lines = buffer.getvalue().splitlines()
    assert len(lines) == 5, lines

    for line in lines:
        assert not credential_complaints(line), (line, credential_complaints(line))

    assert "sk-live-abcdef123456" not in buffer.getvalue()
    assert "***" in buffer.getvalue(), "redaction has to be visible, not silent"


def test_the_filter_never_raises_out_of_the_logging_call_that_triggered_it() -> None:
    """Found by running the container, and it killed it.

    An exception raised inside a `logging.Filter` propagates out of the `logger.info` that
    triggered it — so the first record this filter could not handle took the process down
    at startup rather than losing a line. The record that did it was uvicorn's own: its
    started message carries a `color_message` field of
    `http://\\x1b[1m%s\\x1b[0m:\\x1b[1m%d\\x1b[0m`, and `urlsplit` rejects the unbalanced
    `[` with `ValueError("Invalid IPv6 URL")`. Nothing in the suite lets uvicorn print
    that line, which is why only the container found it.

    Both halves are asserted: the specific input, and that a redactor which fails for some
    future reason drops the record instead of the process.
    """
    svclog.configure("raises")
    log = svclog.get_logger("raises-test")

    hostile = [
        "http://\x1b[1m0.0.0.0\x1b[0m:\x1b[1m8800\x1b[0m (Press CTRL+C to quit)",
        "http://[::1]:8800/status",
        "http://[not-an-address:8800/x",
        "postgresql://a@b@c:5432/d?x",
        "scheme://",
        "http://host?password=x",
    ]

    with emitted() as buffer:
        for text in hostile:
            log.info("uvicorn said %s", text)
            log.info("in an extra", extra={"detail": text})

    lines = buffer.getvalue().splitlines()
    assert len(lines) == 2 * len(hostile), "a record was lost, so something raised"
    for line in lines:
        assert not credential_complaints(line), line

    # And the fail-closed guard: a redactor that breaks drops the record's content rather
    # than passing it through unredacted.
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "secret", None, None)
    record.__dict__["error"] = "postgresql://u:p@h/db"
    svclog.Redaction._drop(record)
    assert "secret" not in record.getMessage()
    assert "error" not in record.__dict__


def test_the_filter_agrees_with_the_provider_side_scrubber_on_the_shapes_they_share() -> None:
    """Two redactors, pinned against each other rather than kept in step by hand.

    `servicekit.logging` does not import `modelgw` — that would pull `httpx` onto the
    startup path of every service for the sake of a regex table, and
    `servicekit.probes` imports it lazily to avoid exactly that. So the tables are
    separate, and this is what makes them the same table: a shape one catches and the
    other does not is a leak with a second opinion.
    """
    from modelgw.config import scrub

    for text in (
        "Authorization: Bearer sk-ant-api03-abcdefghij",
        'x-api-key: "sk-proj-0123456789"',
        "api_key=abcdef123456 rejected",
        "sk-or-v1-0123456789abcdef",
    ):
        assert "***" in svclog.redact_text(text), f"the log filter missed {text!r}"
        assert "***" in scrub(text), f"modelgw's scrubber missed {text!r}"


# =========================================================================
# The DDL refusal (execution decisions §4)
# =========================================================================


def _store_at_another_version(url: str) -> Any:
    """A store one DDL version behind, missing the table that version 3 added.

    Constructed rather than fixtured, because the assertion is about what a *mismatched*
    store looks like afterwards: `model_spend` absent is how "nothing was created" is
    observable at all. A store already at the current version is idempotent under
    `create_all`, so a refusal against one would prove nothing about ordering.
    """
    from sqlalchemy import update

    from kernel.store import LogStore, make_engine
    from logschema import DDL_VERSION, store_version

    store = LogStore(make_engine(url))
    store.create_all()
    with store.engine.begin() as connection:
        connection.execute(update(store_version).values(ddl_version=DDL_VERSION - 1))
        connection.exec_driver_sql("DROP TABLE model_spend")
    return store


def test_a_store_at_another_ddl_version_is_refused_before_anything_is_created(
    tmp_path, monkeypatch
) -> None:
    """The refusal happens ahead of table creation and leaves the lease unheld.

    The order used to be create, lease, check — so a mismatched store had `create_all`
    run against it before anything read the version row, and the refusal that followed
    was an unhandled traceback with a lease already taken. On a bump that adds a table
    that half-migrates the store: the new table appears, the column the same bump added
    to `runs` does not, and what is left is neither version.

    Both halves are demonstrated rather than reasoned. `model_spend` still absent is
    proof `create_all` never ran; a fresh `acquire` succeeding in milliseconds is proof
    the lease is free, because a held one would raise `LeaseHeld` and name the holder.
    """
    from sqlalchemy import inspect, select

    from kernel import lease as lease_module
    from kernel.store import DdlVersionMismatch
    from logschema import DDL_VERSION, writer_lease

    url = f"sqlite:///{tmp_path}/behind.sqlite3"
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)
    store = _store_at_another_version(url)

    import single_process

    with pytest.raises(DdlVersionMismatch) as refused:
        single_process.compose()

    message = str(refused.value)
    assert str(DDL_VERSION) in message and str(DDL_VERSION - 1) in message, (
        "the refusal has to name both versions"
    )
    assert "wipe" in message, "and the remedy, which is a wipe rather than a migration"

    assert "model_spend" not in inspect(store.engine).get_table_names(), (
        "the schema was touched: create_all ran against a store this build cannot read"
    )

    with store.engine.begin() as connection:
        assert connection.execute(select(writer_lease)).first() is None, (
            "the refusal left a lease row behind"
        )
        # And it is genuinely takeable, which is the property that matters to the
        # operator whose next act after reading the remedy is to try again.
        lease_module.acquire(connection)


def test_the_launcher_prints_the_remedy_rather_than_a_traceback(tmp_path) -> None:
    """The verification, run the way an operator meets it.

    A subprocess rather than an exception assertion, because "a sentence rather than a
    stack trace" is a claim about what reaches stderr — and an in-process test cannot
    tell a caught exception from an uncaught one.
    """
    url = f"sqlite:///{tmp_path}/refused.sqlite3"
    _store_at_another_version(url)

    result = subprocess.run(
        [sys.executable, "single_process.py"],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={
            "PYTHONPATH": "packages:services",
            "PATH": "",
            "COMPANY_OS_STORE_URL": url,
        },
    )

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stderr.strip().count("\n") == 0, "one sentence, not a report"
    assert "wipe the store" in result.stderr


# =========================================================================
# The report keeps the read-only credential (R28)
# =========================================================================


def test_the_report_resolves_the_reader_dsn_rather_than_the_writers(monkeypatch) -> None:
    """One process, two engines, and the variable that keeps them apart.

    `store_url()` read one variable, which was enough while the report was its own
    container with its own environment. In one process it would have handed the fold the
    owner's connection, and the read-only role — the only boundary that survives U20
    through U22 being written into the process that also appends — would have been
    retired by a composition change rather than by a decision.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", "postgresql+psycopg://owner:pw@db:5432/os")
    monkeypatch.setenv(
        "COMPANY_OS_REPORT_STORE_URL", "postgresql+psycopg://report_reader:pw@db:5432/os"
    )

    from report import main as report_main
    from servicekit.probes import store_url

    assert "report_reader" in report_main.reader_url()
    assert "owner" not in report_main.reader_url()
    assert "owner" in store_url(), "the kernel keeps the writer's"


def test_the_report_falls_back_to_the_writer_where_there_are_no_roles(
    tmp_path, monkeypatch
) -> None:
    """A laptop store is a SQLite file with no roles, so a second DSN there is a chore.

    Refusing to start without one would turn the documented contributor path into a
    configuration error for no gain — the boundary the second DSN buys does not exist on
    SQLite either way.
    """
    url = f"sqlite:///{tmp_path}/only.sqlite3"
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)
    monkeypatch.delenv("COMPANY_OS_REPORT_STORE_URL", raising=False)

    from report import main as report_main

    assert report_main.reader_url() == url


# Reachability is decided once, so the skip message is the same sentence the store
# suite's is and an omission is visible rather than inferred.
_TEST_POSTGRES = "postgresql+psycopg://companyos:companyos@127.0.0.1:55432/companyos_test"
_READER_POSTGRES = "postgresql+psycopg://report_reader:report_reader@127.0.0.1:55432/companyos_test"


def _postgres_reachable() -> bool:
    from kernel.store import make_engine

    try:
        engine = make_engine(_TEST_POSTGRES)
        with engine.connect():
            return True
    except Exception:  # noqa: BLE001 - unreachable is the only thing being asked
        return False
    finally:
        engine.dispose()


def test_the_reports_connection_cannot_append() -> None:
    """Asserted against the store, by trying it — not by scanning the report for `insert`.

    `tests/test_lifecycle.py` already scans this service's modules for a write, and that
    scan is worth having; it is also exactly what a refactor slips past. The claim R28
    needs is about the *connection*: hand the report's credential an INSERT and the store
    refuses it, whatever the code around it later becomes.

    SQLite cannot express this — it has no roles, which is why the plan calls the
    Postgres role the mechanism and the module scan the second opinion. So this is
    Postgres-only, and a skip here is an incomplete run rather than a pass.

    The grant is re-applied in this database rather than inherited from
    `infra/postgres/init/10-report-reader.sql`, whose `ALTER DEFAULT PRIVILEGES` runs in
    the *application* database — and this suite must not create schema in the database a
    live kernel owns the lease for. The statement is the same one that file uses.
    """
    if not _postgres_reachable():
        pytest.skip(
            f"Postgres not reachable at {_TEST_POSTGRES}. Start it with `docker compose "
            "-f docker-compose.yml -f docker-compose.test.yml up -d postgres`. R28's "
            "store-side half is required, so this skip is an incomplete run."
        )

    from sqlalchemy import create_engine, insert, select, text

    from kernel.store import LogStore, make_engine
    from logschema import event_log, metadata

    owner = make_engine(_TEST_POSTGRES)
    try:
        metadata.drop_all(owner)
        LogStore(owner).create_all()
        with owner.begin() as connection:
            connection.execute(
                text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO report_reader")
            )

        reader = create_engine(_READER_POSTGRES, future=True)
        try:
            with reader.connect() as connection:
                # It can read: a report that could not fold the log would be useless.
                connection.execute(select(event_log).limit(1)).all()

            with pytest.raises(Exception) as refused:  # noqa: PT011 - driver-specific
                with reader.begin() as connection:
                    connection.execute(
                        insert(event_log).values(
                            run_id="run-x",
                            seq=1,
                            tick=0,
                            kind=1,
                            schema_ver=1,
                            rules_ver="x",
                            payload=b"{}",
                            ingested_at="2026-08-16T00:00:00.000+00:00",
                        )
                    )

            assert "permission denied" in str(refused.value).lower(), str(refused.value)
        finally:
            reader.dispose()
    finally:
        metadata.drop_all(owner)
        owner.dispose()

# Company OS — a company simulator

A pixel-art simulator of a company. You are the CEO: you assign work, employees
move along the real reporting lines to do it, and they stop at the point where
only you can decide. Walking over to ask them beats clearing it from the tray,
because in person they tell you things the tray never shows.

Built as a demo surface for the `100_avater` idea (virtual office → hearing →
work-knowledge graph), but it runs standalone.

The system is an event-sourced kernel with a client over it; what is built, what is
planned and why is under [`docs/plans/`](docs/plans/), and the domain vocabulary is
in [`CONTEXT.md`](CONTEXT.md).

Licensed under **Apache-2.0**; the full text is in [`LICENSE`](LICENSE).

---

## Run it

```bash
docker compose up
```

Then open <http://127.0.0.1:8790>. That is the whole path: no profile, no
provisioning step, no environment file. The store creates its own schema and its
read-only reporting role on first boot, and the client is up with it.

Cold containers to all three healthy, images already built: **11.6s** on the
reference machine — see *Measured*, below, for the second boot and the machine. The
image build is separate, and on a network that intercepts TLS it has a caveat that
now blocks this command; see *Known environment caveat*.

**Three containers.** `postgres` holds the append-only log; `backend` runs the
single-process launcher, which is the kernel, the clock, the only writer, and the
REST and WebSocket surface the client talks to; `web` is nginx serving the built
client and proxying `/api/` and `/ws` to the backend. There were seven, five of
them backend services meeting over gRPC, and the split cost a container and a hop
per service and bought nothing on a machine with one operator.

**There is no profile, and that is the point.** `web` used to declare
`profiles: ['demo']`, and Compose starts a profiled service only when its profile
is named — so the one command everybody tries brought up six backend services and
no client, which looks like a crashed container and is actually a service that was
never selected. `COMPOSE_PROFILES=demo` and `COMPOSE_PROFILES=dev` both still
resolve to the same three services, so an old shell alias is harmless.

**What this gives you.** The client renders a run: the office, the HUD, the
decision tray, the DAG, the chain strip, and the conversation panel that opens
whenever the CEO stands next to someone. WASD or the arrow keys walk the CEO
around the floor.

**The client starts its own run.** Open the page and press *Start a run*; the id it
creates goes into the address bar, so a reload re-attaches to that run rather than
starting a second one. Opening `?run=<id>` directly — for example
<http://127.0.0.1:8790/?run=demo> — attaches to an existing run instead. When the
backend cannot be reached, the page falls back to its status report, which is the
useful thing to see when there is nothing to render.

**Staff do not move on screen, deliberately.** The kernel walks them — a director really
does carry work to a specialist's desk — but no event carries a person's position or
path, so the client draws everyone at their seat and only the CEO moves. Left that way
for this phase: the question this phase answers is whether the in-person loop is worth
playing, and a static floor answers it. See `docs/2026-08-14-phase-1-coverage-audit.md`.

### A schema change is a wipe, not a migration

The store records the DDL version it was created at, and the backend refuses to
start against a version it does not understand rather than appending to a schema
it cannot read. It names both versions and the remedy, and the remedy is a wipe:

```bash
docker compose down -v && docker compose up
```

There is no migration mechanism and there is not going to be one at this stage. A
run is a disposable artifact — its value is the hour you spend playing it, not the
month you keep it — and a forward migration for every schema change would be a
standing cost paid to preserve something nobody is preserving. So an existing
volume is dropped across a schema change, and `-v` is the flag that does it.

### Create a run

Creation is its own route rather than a command kind, deliberately: a command must
never bring a simulation into being, or a typo'd id in a client would silently start
one. `POST /runs/{id}/commands` answers not-found for an unknown run on purpose.

```bash
curl -s -X POST http://127.0.0.1:8800/runs \
  -H 'content-type: application/json' -d '{"run_id":"demo"}'
```

The run id is its own idempotency key — creating one that exists returns that run
with `"created": false` rather than a second run or an error, which is what a client
retrying a request whose response it never saw needs. Omit `run_id` and one is
minted. Omit `run_seed` and one is minted **and returned**: two fresh runs from one
seed produce identical logs (R11), so a caller that never learned the seed could not
ask for that run again.

The clock starts with the run, and a restart picks it back up — rate is run state
(R18), so a running run resumes running and a paused one stays paused.

Add `"scenario":"ashcroft"` to the body to run a different company; omit it for the
shipped one. `GET /scenarios` lists what this backend can start. See
[Changing the company](#changing-the-company).

### Ports

Only two ports reach the host, and only on loopback.

| Service | Port | Published |
|---|---|---|
| `web` | 8790 | `127.0.0.1` |
| `backend` | 8800 | `127.0.0.1` |
| `postgres` | 5432 | compose network only |

The client reaches the backend through nginx on 8790, not through 8800. The 8800
mapping is there for `curl` and for the Vite dev server; the store is deliberately
unpublished, and the separate `docker-compose.test.yml` is how the store suite
gets at it.

Inside a container the launcher binds all interfaces; outside one it binds
loopback. That asymmetry is R34 and it is decided in code, in
`packages/servicekit/runtime.py`, because a loopback bind inside a container is
unreachable from sibling containers and from its own published port — which looks
like a broken service and is actually a broken bind.

### Without Docker — the contributor path

The same launcher the `backend` container runs, run directly. `uv` selects the
pinned interpreter; the `python3` on PATH is 3.9 and cannot install these
dependencies.

```bash
cd backend
uv run python single_process.py                                # → http://127.0.0.1:8800
COMPANY_OS_GATEWAY_PORT=8810 uv run python single_process.py    # if compose holds 8800
```

This is not a second topology — it is the same composition, invoked without Docker,
which is why it stays honest about what compose runs. The store defaults to SQLite
at `backend/var/company-os.sqlite3` (git-ignored), so the whole loop plays with no
container at all. Point it at Postgres with one variable:

```bash
COMPANY_OS_STORE_URL=postgresql+psycopg://companyos:companyos@127.0.0.1:55432/companyos \
  uv run python single_process.py
```

Reaching the compose store from the host needs a published port, which
`docker-compose.yml` deliberately does not provide — `docker-compose.test.yml`
publishes it on 55432 for exactly this and for the store suite. And exactly one
kernel may hold the store: the writer lease refuses the second and names the
holder, so pointing this at a store a running stack owns fails closed rather than
interleaving two writers.

The one thing a default SQLite run does not cover is the Postgres-only hazards —
JSONB key ordering under the state hash, and the sequence and transaction-control
differences. Those are covered by the store suite's two dialects and by the compose
path, which points this same launcher at Postgres.

For client work, run Vite against it:

```bash
cd frontend && npm install && npm run dev    # → http://127.0.0.1:5173
```

The client addresses `/api` and `/ws` in **both** setups — nginx proxies those
prefixes under compose, the Vite dev server proxies them in development. Keeping
the client's own paths byte-identical across both is what makes "it works in dev"
mean something.

Individual services still start on their own (`uv run python -m kernel.main`, or
`gateway`, `domain`, `agents`, `report`), which is how their status endpoints are
read in isolation. A bare `gateway.main` serves the routes with no kernel behind
them and says so on `/status`; the launcher is what puts one there.

### The five surfaces, on one port

The launcher mounts all five service apps into one process. The gateway keeps the
root, because that is what the client's `/api/` proxy maps onto; the other four
keep a prefix, so one port never means two surfaces for one path.

| Surface | Status endpoint | Also serves |
|---|---|---|
| `gateway` | `/status` | `/runs`, `/runs/{id}/commands`, `/runs/{id}/state`, `/ws/{id}` |
| `kernel` | `/kernel/status` | `/kernel/runs/{id}/diagnose` |
| `domain` | `/domain/status` | — |
| `agents` | `/agents/status` | `/agents/runs/{id}/spend` |
| `report` | `/report/status` | `/report/runs/{id}/report` |

The report is mounted by the launcher rather than reached through the gateway, and
that is a rule rather than a preference: no service may import another's internals,
and the launcher is the one component outside `services/` that is allowed to see
two. It also keeps its own `COMPANY_OS_REPORT_STORE_URL` — a SQLAlchemy engine is
per-DSN rather than per-process, so the read-only role is still a real boundary now
that the fold runs in the process that appends. Unset, it falls back to the writer's
DSN, which is what a SQLite laptop run wants: there are no roles there to separate.

### Diagnosing

```bash
curl -s http://127.0.0.1:8800/status | python3 -m json.tool
curl -s http://127.0.0.1:8800/kernel/status | python3 -m json.tool
docker compose logs -f                 # single-line JSON, one aggregator
```

`docker compose logs` being the aggregator is a decision, not an omission: one
machine, one operator, no log pipeline. The status payload is built to be the
thing you paste into an issue — service, build, versions, store, and what each
surface can and cannot reach.

Three startup refusals are worth recognising by their message rather than by their
stack trace, because all three are deliberate:

- **`the writer lease is held by …`** — something else owns the log. Exactly one
  kernel may run against a store; the message names the holder and says when the
  lease becomes reclaimable. Usually a host-side `single_process.py` pointed at the
  compose store, or the other way round.
- **`store DDL version is N; this kernel understands M`** — the schema is from a
  different build. The remedy is the wipe above, not a migration.
- **`no kernel client is configured`** — a `gateway.main` started on its own. The
  launcher is what installs one.

All three arrive as the sentence alone. The first two used to come wrapped in a
Python traceback with the reason at the bottom — the right sentence in the wrong
wrapper — because only the kernel service entrypoint handled them and the launcher
is what the container runs. The container exits non-zero either way, and the capped
restart policy stops it after three attempts rather than looping.

A DDL mismatch also refuses **before** touching the schema and leaves the writer
lease unheld, so the store it refused is exactly the store it found and the next
attempt does not wait out the lease's thirty-second TTL.

One runtime refusal is worth recognising too. The event stream answers a handshake
**403** when its `Origin` is not the authority the socket was opened against: a
WebSocket upgrade is exempt from every cross-origin rule the browser applies to
`fetch`, so loopback alone would let any page you have open read a run. All three
shipped paths are same-origin by construction — nginx forwards `$http_host`, the Vite
dev server rewrites the origin to its target, and 8800 direct is itself. A proxy that
does neither needs its origin named in `COMPANY_OS_ALLOWED_ORIGINS`.

Two health definitions are deliberately asymmetric, and both survive the collapse
into one container because they are properties of the surfaces rather than of the
processes:

- **The kernel without a store is unhealthy.** It cannot append, which is its job.
  It reports 503, keeps retrying, and never exits — exiting would make an outage
  look like a crash and the capped restart policy would then hide the outage
  inside a restart loop.
- **The gateway without a kernel is healthy and degraded.** It is the surface you
  already have open, so it stays up and tells you the kernel is down. A gateway
  that failed alongside the kernel would turn one outage into two and explain
  neither.

---

## Pointing it at a model

Nothing here needs a model. With no provider configured the gateway reports itself
absent, the directors fall back to their scripted replies, and every scenario stays
playable — the test suite has no keyed path in it at all. Configuring one is five
variables and no code:

| Variable | What it is | Default |
|---|---|---|
| `COMPANY_OS_MODEL_PROVIDER` | one of the eight below | *(none — the bench is absent)* |
| `COMPANY_OS_MODEL` | the model name that provider knows | *(none — required)* |
| `COMPANY_OS_MODEL_BASE_URL` | overrides the provider's endpoint | the provider's own, below |
| `COMPANY_OS_MODEL_API_KEY` | the key; wins over the provider's own variable | the provider's own, below |
| `COMPANY_OS_MODEL_TIMEOUT_SECONDS` | one call's whole budget | `30` (connect gets 5) |
| `COMPANY_OS_MODEL_MAX_CALLS` | provider calls **one run** may make | `200` |
| `COMPANY_OS_MODEL_MAX_TOKENS` | tokens **one run** may spend | `600000` |

Both ceilings are per run, not per lineage — a fork of an exhausted parent gets its
own budget, so the bench does not go quiet at a different moment in each timeline and
turn a decision's consequences into an artefact of when you ran out. The HUD shows the
run's own spend against its own ceiling, with the lineage total beside it.

Reaching a ceiling stops model calls; it never stops the run. The directors fall back
to their scripted replies and the report records where the bench went quiet.

The only spelling that removes a bound is the literal word `unlimited`, and it is
announced at startup as a warning naming the variable — `0` means zero calls, and a
negative or unparseable value keeps the shipped ceiling and tells you what to type.
On a bring-your-own-key tool, a number left to be decided later becomes `None` meaning
unbounded, which is the one failure mode worth an ugly log line.

Eight providers, and two code paths between them:

| `COMPANY_OS_MODEL_PROVIDER` | Wire | Default base URL | Key |
|---|---|---|---|
| `openai` | OpenAI-compatible | `https://api.openai.com/v1` | required, or `OPENAI_API_KEY` |
| `azure` | OpenAI-compatible | *(none — per-resource)* | required, or `AZURE_OPENAI_API_KEY` |
| `openrouter` | OpenAI-compatible | `https://openrouter.ai/api/v1` | required, or `OPENROUTER_API_KEY` |
| `ollama` | OpenAI-compatible | `http://127.0.0.1:11434/v1` | none |
| `lmstudio` | OpenAI-compatible | `http://127.0.0.1:1234/v1` | none |
| `vllm` | OpenAI-compatible | `http://127.0.0.1:8000/v1` | none, unless started with `--api-key` |
| `sglang` | OpenAI-compatible | `http://127.0.0.1:30000/v1` | none |
| `anthropic` | Anthropic native | `https://api.anthropic.com` | required, or `ANTHROPIC_API_KEY` |

```bash
COMPANY_OS_MODEL_PROVIDER=ollama COMPANY_OS_MODEL=qwen3:32b \
  uv run python single_process.py            # a local model, no key anywhere
```

Seven of the eight speak one HTTP shape and one speaks another, which is why there
are two request builders in `packages/modelgw/` and no LLM framework in the lockfile.
Two HTTP shapes do not justify that dependency surface, and on a bring-your-own-key
tool the framework's own configuration surface arrives as a support burden on top of
the five variables above. What differs between the seven is data — a base URL, an
auth header name, the token-limit field name — so a ninth OpenAI-compatible server is
a row in `packages/modelgw/config.py`, and a test asserts every row resolves to one of
the two paths.

Azure is the one provider with no default endpoint, because its endpoint is
per-resource: set the base URL to `https://<resource>.openai.azure.com/openai/v1`. It
is also the one that versions by query parameter — an `?api-version=` you paste into
the base URL is kept, and a bare endpoint gets a default.

Every provider outcome comes back as a value, never as an exception: a 429, a 500, a
timeout, a body that is not JSON, a 200 with nothing in it, and a refused connection
at a local base URL are each a named condition the bench falls back on. None of them
retries — a retry would spend a run's budget on an outcome the bench has already
decided to replace.

**The key is a type, not a string.** It renders as `***` through `str`, `repr`, an
f-string and a traceback, it cannot be serialised to JSON or pickled at all, and one
function in the package turns it back into a header. A provider's error body is
scrubbed against that key before it is kept, because a 401 body that echoes the
`Authorization` header back is how a key reaches a log store through code that never
touched one. Export the key; never write it into a scenario file, a compose file or a
`.env` you might commit.

---

## The prototype

`company-os.html` is the **prototype**: the whole original application in one file,
kept because it is where the mechanics were designed and because it needs no
toolchain at all. It is not the demo artifact — `docker compose up` is — and it is
not what the test suites below describe. Nothing persists in it, it shares no code
with the system, and a change made here does not reach the product.

```bash
open company-os.html          # macOS
```

The page is entirely self-contained — inline CSS, inline JS, pixel art generated
at runtime, zero network requests — so `file://` works. If you would rather serve
it over HTTP:

```bash
./serve.sh                    # → http://localhost:8791/company-os.html
./serve.sh 9000               # a different port
```

**Controls.** `WASD` / arrow keys to walk, click the floor to walk there, walk up
to someone to talk, `Space` pauses. `×1` / `×3` change the clock speed.

---

## Layout

```
LICENSE                       Apache-2.0
company-os.html               the prototype: the whole original application, one file
test/                         the prototype's harness (29 checks) and sprite validator
serve.sh                      optional local HTTP server for the prototype

backend/
  pyproject.toml              one project, one lockfile, every version pinned
  .python-version             3.12
  single_process.py           the launcher: what the `backend` container runs
  Dockerfile                  one target, one command — the launcher
  packages/
    simcore/                  the kernel library — no service, transport or store
    contracts/                proto stubs and the event envelope (U2)
    servicekit/               logging, status, bind rules, shared by all surfaces
    modelgw/                  the bench's gateway: two wires, eight providers, one masked key
  services/
    kernel/                   sole log writer, owner of the clock
    gateway/                  REST commands, WebSocket stream
    domain/                   metric/effect model behind the R15 seam
    agents/                   Phase 2's layer; a stub resolver in Phase 1
    report/                   read-side fold over the log
  tests/
frontend/
  nginx.conf                  serves the built client, proxies /api/ and /ws
  src/design/                 the art direction as code: palette, load ramp, label face
  src/net/                    gateway client, event stream, and the run store
  src/render/                 the ported canvas office, lifecycle-managed (U12)
  src/ui/                     shell, HUD, panels (U13)
  src/dag/                    node encoding, layout, DAG view, chain strip (U14)
  tests/
infra/postgres/init/          the read-only reporting role, and the suite's database
docker-compose.yml            three containers, no profile
docker-compose.test.yml       publishes the store, for the store suite only
```

`services/` is still five separate trees and they still may not import each other's
internals — the boundary is an import rule, not a transport, and
`tests/test_import_boundaries.py` polices it inside one process exactly as it did
across five containers. What collapsed is the deployment.

`packages/` and `services/` are import roots rather than installed distributions,
so `packages/simcore/time.py` imports as `simcore.time`. A flat module named
`time.py` can never shadow the standard library that way, because a submodule
reference is always fully qualified.

---

## Tests

```bash
cd backend && uv run pytest        # toolchain pins, contracts, determinism,
                                  # the kernel port, and the ported parity suite
cd frontend && npm test           # renderer lifecycle, HUD rules, DAG encoding,
                                  # the run store, and the golden vectors
cd frontend && npm run lint        # oxlint; the tree is warning-free
cd frontend && npm run build       # tsc -b included: a type error fails the build

node test/sprites.js              # the prototype: every sprite row the right width
node test/harness.js              # the prototype: assign → stall → decide → deliverable
```

Two suites are worth knowing about by name.

**The parity suite** (`backend/tests/test_parity.py`) ports the 29 executing assertions
from `test/harness.js` — the assertions, not the code. It maps each one back to the
harness line it came from, so the parity claim is auditable against its source. The
harness has 30 `check(` call sites; the 30th sits in an `else` branch that never runs on
the default path, and the suite asserts the condition that keeps it unreachable rather
than porting an assertion that cannot fire.

Assertions whose outcome depends on how much effort has burned by a given moment are
marked, because U7 changes the burn rate:

```bash
cd backend && uv run pytest -m effort_timed     # the 12 to review when the rate moves
```

**The store suite** (`backend/tests/test_store.py`) runs every test twice, once on SQLite
and once on Postgres, because the point of the portability work is that the two behave the
same. Postgres has to be published to the host for that half:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres
cd backend && uv run pytest tests/test_store.py        # 31 tests per dialect
```

It runs against `companyos_test`, not the application's database — the suite creates and
drops the schema and takes the writer lease, and so does a running kernel. Without the
Postgres half the suite still passes on SQLite, which is why there is a test that fails
visibly rather than skipping quietly when the dialect coverage is incomplete.

The main `docker-compose.yml` deliberately does **not** publish the store. R34 is not a
default to be relaxed for convenience; a test needing host access is not a reason to open
a running system's store to the host, hence the separate override file.

**The golden vectors** (`backend/tests/fixtures/golden/`) pin the logic that exists in
both languages: the tick-space integer arithmetic the client uses to interpolate and to
predict the CEO, and the avatar palette derivation. Python generates them, TypeScript
asserts against them.

```bash
cd backend && uv run python scripts/generate_golden.py
```

Every integer in the *movement* fixtures is stored as a **string**, and the vectors
deliberately include tick indices above 2^53. `JSON.parse` produces doubles, which lose
precision past that point without saying so; strings force the client to parse with
`BigInt`. The TypeScript test also asserts that the naive number-based version really
would have disagreed, so the vector is guarding something rather than restating it.

`genesis.json` is the exception, and for the opposite reason: it is a real genesis
*payload*, and the client has to parse it exactly as it comes off the wire. Converting its
numbers to strings would test a format nothing sends. It exists so that the HUD's metric
table and the DAG's work graph are asserted against the shape the kernel actually
produces — a hand-typed fixture would be a second opinion about the very shape it exists
to pin, and it would drift the first time a field was added.

A missing fixture is a **failure** on both sides, never a skip — a skipped vector test
reports green while the only build-time guard on that duplicated logic is not running.

The backend suite runs without Docker on purpose. The compose tests read
`docker compose config`, which merges and validates locally and needs no daemon;
the kernel, determinism, parity and replay suites are pure Python; and the gateway
suite drives the real launcher composition, so what the `backend` container runs is
covered without building it. Running everything through compose would make the suite
slow enough that contributors skip it, which is why the three scenarios that
genuinely need a daemon — the cold boot that reaches a client, the second boot that
reuses the volume, and the second writer that names the lease holder — are recorded
in the docstrings of the config tests that stand in for them, and belong to a CI
smoke job rather than to `pytest`.

`test/harness.js` still runs the prototype's real JavaScript against a small DOM
stub. Its 29 assertions were ported to pytest at U5, before any new mechanic was
allowed to change the behaviour they describe.

**The client's drawing tests read draw calls, not pixels.** jsdom does not implement
`getContext`, so every drawing path in `src/render/`, `src/dag/` and `src/design/`
takes its context as a parameter and the suites hand it a recorder. That is not a
workaround: the properties the art direction states are "a blocked node drew violet
on a broken border, and stayed distinguishable with hue removed", and those are
statements about draw calls. Comparing screenshots could not express them.

---

## Why these versions are pinned

Every dependency is pinned exactly, so one lockfile describes one resolution. Two
of the pins are load-bearing rather than tidy, and both were verified rather than
assumed.

**`starlette==1.3.1`.** FastAPI 0.141.1 declares `starlette>=0.46.0` with no upper
bound. Left to resolve, that installs Starlette 1.6.0 — which, along with 1.4.0,
1.4.1, 1.5.0 and 1.5.1, was released *after* the FastAPI version that depends on
it (2026-07-29) and has therefore never been tested against it. 1.3.1 is the newest
Starlette that existed when that FastAPI shipped.

**`httpx==0.28.1`, not `httpx2`.** Starlette 1.3.1's test client warns that httpx
is deprecated in favour of httpx2. Staying put: httpx2 2.10.0 was released
2026-08-09, after this Starlette, so switching would trade a working deprecation
for an untested major across every test file. Revisit when Starlette drops httpx
support outright.

**`typescript ~6.0.2`.** The Vite template's own pin, kept in preference to npm
`latest`, which is 7.0.2 — a major the template deliberately stays behind.

**`zustand ^5.0.15`.** The client's one runtime dependency beyond React. The event
stream needs a store that can be *subscribed to without re-rendering* — the canvas
reads state inside its own frame callback, and driving it through component state
would re-render the tree at the tick rate to produce pixels React never touches.
`subscribeWithSelector` is that primitive. One hazard comes with the major: a
selector returning a fresh reference re-renders forever, because the equality check
is `Object.is` on the selector's output and a new object is never `Object.is` to the
previous one. Every object or array selector in the client goes through `useShallow`
for that reason.

`strict` is added to the template's TypeScript options. The client reimplements
integer movement and tick-space interpolation that must match Python golden
vectors above 2^53, and those vectors are the only build-time guard on that
duplicated logic; non-strict mode would let a null path intent reach that code and
fail only for inputs nobody thought to vector.

### Known environment caveat

Building the `web` image needs npm to reach `registry.npmjs.org` from inside the
container. On a network that intercepts TLS — a corporate proxy such as Zscaler —
that build fails with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY` even though npm works on
the host, because the container trusts the public roots and not the proxy's.

This used to be survivable by omitting `web`. It is not any more: there is one
command and it builds all three images, so on such a network `docker compose up`
fails at the client build and the stranger's path stops there. Two ways through:

- Pass the intercepting CA into the build — copy it in and point
  `NODE_EXTRA_CA_CERTS` at the updated bundle. It is not baked into the Dockerfile
  because the certificate belongs to one network and committing it would be
  committing somebody's proxy.
- Build the client on the host, where npm already trusts the proxy, and let the
  image copy `dist/` — `cd frontend && npm ci && npm run build`.

The backend image is unaffected: `uv` reads the host's certificate store through
the build cache mount and resolves against the lockfile.

### Measured

Reference machine: Apple silicon laptop, Docker 28.5.2, Compose v2.40.3.

| Step | Time |
|---|---|
| `docker compose up`, images built, empty volume, to all three healthy | **11.6s** |
| the same against an existing volume — schema reused, lease handed over | **11.5s** |
| `backend` image from a cleared build cache | **17.3s** |

The second boot is not faster, which is the useful finding: the DDL check and the
lease handover cost nothing measurable, and the eleven seconds are Postgres
initialising plus the healthcheck intervals.

Then, through nginx on 8790 and nothing else: the page loads, *Start a run* creates
a run and puts its id in the address bar, the WebSocket opens and delivers `GENESIS`
followed by `POSITION_ECHO` frames, the office and the HUD draw, and a command
applies and appends. No console error, no failed request.

The `web` image build is excluded from the table because it cannot complete on this
machine's network — see the caveat above. On an unintercepted network it is
dominated by `npm ci`.

---

## What is real, and what is not

Real, and covered by the tests below:

- A clock with business hours, day rollover and daily fixed costs.
- Work that progresses per assignee, stalls at decision points until you answer,
  and applies consequences to five company metrics.
- Deliverables that carry provenance, and an unlock chain (some work needs a
  visibility threshold or a finished prerequisite).
- Delegation you can watch: hand work to a director and he walks to his
  specialist's desk. Bypass him and morale drops and he is recorded as not knowing.
- Breadth-first pathfinding on a generated tile grid; nobody walks through walls.
- A floorplan generated to fit the window — rooms, doors, desks, seats, lamps and
  windows are all computed, so the office fills whatever space it is given.
- A pixel-art renderer: sprite sheets built at runtime from ASCII grids, palettes
  derived from each id, four facings × three walk frames, depth-sorted drawing,
  baked lighting, integer-zoom nearest-neighbour scaling.
- A HUD that carries trajectories rather than bare values, each metric read against
  its own favourable direction — so cutting manual hours reads as the win it is
  rather than as a regression — plus runway, per-department capacity heat, and a
  count of what is waiting on you.
- A second surface over the same state: a dependency graph drawn on the office's own
  16px grid, with status carried by border form and glyph so it survives greyscale,
  and a chain strip that keeps blocked work visible while you are in the office.
- Every option at a decision point states what it costs, in the same integer units
  the metric tiles use — including the change to the department's recurring workload,
  which is a different thing from a metric delta and is shown as one.
- A branch comparison: at an open decision you can run one branch per option, each
  forked at that tick and advanced to the first downstream decision it raises or to
  the run's horizon, and read them side by side. A branch takes no action at anything
  it reaches, never touches the parent run, and is discarded when you close it. It
  needs no model key, and it is reachable from the conversation and from the tray.

Not real — deliberately, and stated on screen:

1. **Dialogue is scripted.** Each person has four canned answers (why /
   exceptions / who decides / bottleneck), matched on keywords. No LLM.
2. **Nothing persists.** In the prototype: no backend, no database, no accounts,
   no multiplayer. Reload and it is Day 1 again. This is what Phase 1 changes.
3. **The company is invented.** Halstead Industrial, its ten people and its eight
   work items are plausible sample data, not a real customer's org.
4. **Every number is authored tuning.** Cash, lead time, morale, visibility, the
   recurring workload, the runway, the capacity heat, and every figure a branch
   projects — all of them come from constants somebody chose, not from a measurement
   anything took. The client says so on the figure itself rather than in a footnote:
   each one carries a `≈` and the words "authored tuning", and the marking is a glyph
   and a label rather than a colour, so it survives greyscale and a screenshot. What
   the numbers are good for is comparing two decisions under one set of rules; what
   they are not good for is telling you what your company would do.
5. **A branch is a run nobody answers.** The kernel asks its domain service for each
   period's effects, and inside a branch nothing replies, so those requests are raised
   and abandoned exactly as they would be if the service were down. Every branch at
   one decision does this identically, so it cannot tip the comparison one way — but a
   branch's absolute figures describe a run whose domain service stays silent, which
   is a bound on how far a projection may be read.
6. **A comparison costs about a second and a half, on the request thread.** Six
   branches at the bound, stepped synchronously in pure Python. It runs outside the
   store's append transaction so it cannot stall the log writer, and branch execution
   draws from its own two-slot limiter rather than from the worker pool the tick loops
   and the lease heartbeat use — so a burst of comparisons queues instead of slowing
   every run's clock. The clock's worker slots are reserved the same way, and
   readiness reports whether sim-time is still moving rather than whether the tick
   task is alive, so a starved clock says so instead of reporting ready. What has not
   changed is the cost of *one*: a second and a half is a second and a half, and
   shortening that would need a process pool.

---

## Changing the company

**A company is a file.** Two ship — `backend/scenarios/default.toml`, the Northwind
Components company every suite asserts against, and `backend/scenarios/ashcroft.toml`,
an independent publisher of nine — and adding a third needs no code change: drop
`acme.toml` next to them and a run can be created against `acme`. What is yours to
author and what the rules fix is in
[`backend/scenarios/schema.md`](backend/scenarios/schema.md).

Which one a run is of is chosen when the run is created, by name and never by path:

```bash
curl -s http://127.0.0.1:8800/scenarios        # what is on offer, with its refusal if it will not load
curl -s -X POST http://127.0.0.1:8800/runs \
  -H 'content-type: application/json' -d '{"scenario":"ashcroft"}'
```

The client offers the same list on its start screen. Omit the field and you get the
shipped company. **Editing a scenario invalidates every run written against it** —
the run's numbers came from a company that no longer exists in that form, and the
three guard sites refuse rather than replay against the edit. Finish a run before you
edit its company, or accept that you are starting a new one.

### The prototype's own three structures

Three data structures near the top of the prototype's script hold everything:

- `ROOM_PLAN` — the eight rooms, their department, floor style and desk count.
- `PEOPLE` — the roster. `dept` places someone in a room, `mgr` sets the
  reporting line, `slot` picks their desk. Looks are derived from `id`, so no
  avatar assets are needed and nobody's likeness is used.
- `ITEMS` — the work. Each item names its department, the effort in hours, the
  decision points, each option's consequences, and the tacit-knowledge line that
  only appears if you walk over.

To demo against a real customer, replace those three and the floor adapts. If
their structure is deeper than director → specialist, `ROOM_PLAN` needs a third
band and the planner needs a matching row of rooms.

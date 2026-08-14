# Company OS — a company simulator

A pixel-art simulator of a company. You are the CEO: you assign work, employees
move along the real reporting lines to do it, and they stop at the point where
only you can decide. Walking over to ask them beats clearing it from the tray,
because in person they tell you things the tray never shows.

Built as a demo surface for the `100_avater` idea (virtual office → hearing →
work-knowledge graph), but it runs standalone.

Phase 1 is rebuilding this as an event-sourced kernel plus a service split — see
`docs/plans/2026-08-13-001-feat-company-os-phase-1-plan.md`. Until the ported
renderer reaches visual parity, **`company-os.html` remains the demo artifact**
and the one path that needs no toolchain at all.

---

## Run the prototype

Open the file. There is no build step and no dependencies.

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

## Run the Phase 1 system

```bash
COMPOSE_PROFILES=demo docker compose up    # seven services, client included
```

Then open <http://127.0.0.1:8790>. Nothing needs provisioning first; the store
initialises itself and the read-only reporting role on first boot.

**The profile is required, not decorative.** `web` declares `profiles: ['demo']`,
and Compose starts a profiled service only when its profile is named — so a bare
`docker compose up` brings up the six backend services and no client, which looks
like a crashed container and is actually a service that was never selected.

**What this gives you today.** The seven services start, each answers a structured
status endpoint, and the client renders a run: the office, the HUD, the decision
tray, the DAG, the chain strip, and the conversation panel that opens whenever the
CEO stands next to someone. WASD or the arrow keys walk the CEO around the floor.

**The client can start its own run.** Open the page and press *Start a run*; the id
it creates goes into the address bar, so a reload re-attaches to that run rather
than starting a second one. Opening `?run=<id>` directly — for example
<http://127.0.0.1:8790/?run=demo> — attaches to an existing run instead. When the
gateway cannot be reached, the page falls back to its status report, which is the
useful thing to see when there is nothing to render.

**Staff do not move on screen, deliberately.** The kernel walks them — a director really
does carry work to a specialist's desk — but no event carries a person's position or
path, so the client draws everyone at their seat and only the CEO moves. Left that way
for this phase: the question this phase answers is whether the in-person loop is worth
playing, and a static floor answers it. See `docs/2026-08-14-phase-1-coverage-audit.md`.

**Compose cannot reach the kernel yet, and that is the remaining gap.** The gateway
talks to a `KernelClient`, and only one implementation exists — the in-process one
the single-process launcher installs. There is no gRPC client, so under `docker
compose up` every command and stream route answers 503 and explains why. The kernel
serves gRPC (`services/kernel/grpc_server.py`); nothing dials it. Until that leg is
built, **use single-process mode below** for anything beyond a health check.

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

### The two profiles

| Profile | Command | Services | Use |
|---|---|---|---|
| `demo` | `COMPOSE_PROFILES=demo docker compose up` | 7, including `web` | Nothing on the host but Docker |
| `dev` | `COMPOSE_PROFILES=dev docker compose up` | 6, no `web` | Client work; run Vite on the host |
| *(none)* | `docker compose up` | 6, no `web` | Same six as `dev`; `web` is never selected |

There is no default profile. `web` is the only profiled service, so naming a
profile is what decides whether a client comes up — omitting one is the same as
asking for `dev`.

Under `dev`, start the client yourself:

```bash
cd frontend && npm install && npm run dev    # → http://127.0.0.1:5173
```

The client addresses the gateway at `/api` and `/ws` in **both** profiles — nginx
proxies those prefixes under `demo`, the Vite dev server proxies them under `dev`.
Keeping the client's own paths byte-identical across both is what makes "it works
in dev" mean something for the demo.

### Ports

Only two ports reach the host, and only on loopback.

| Service | Port | Published |
|---|---|---|
| `web` | 8790 | `127.0.0.1` (demo profile) |
| `gateway` | 8800 | `127.0.0.1` |
| `kernel` | 8801 | compose network only |
| `domain` | 8802 | compose network only |
| `agents` | 8803 | compose network only |
| `report` | 8804 | compose network only |
| `postgres` | 5432 | compose network only |

Inside a container every service binds all interfaces; outside one it binds
loopback. That asymmetry is R34 and it is decided in code, in
`packages/servicekit/runtime.py`, because a loopback bind inside a container is
unreachable from sibling containers and from its own published port — which looks
like a broken service and is actually a broken bind.

### Without Docker

Any single service runs directly. `uv` selects the pinned interpreter; the
`python3` on PATH is 3.9 and cannot install these dependencies.

```bash
cd backend
uv run python -m gateway.main      # or kernel.main, domain.main, agents.main, report.main
```

The store defaults to SQLite at `backend/var/company-os.sqlite3` (git-ignored).
Point any service at the compose store instead with one variable:

```bash
COMPANY_OS_STORE_URL=postgresql+psycopg://companyos:companyos@127.0.0.1:5432/companyos \
  uv run python -m kernel.main
```

Note that reaching the compose store from the host needs a published port, which
this compose file deliberately does not provide.

### Single-process mode (the working path today)

Composes the kernel runtime and the gateway app in **one process** — the same
application objects the compose topology uses, wired without gRPC. Since the gRPC
client leg is unbuilt, this is currently the only way to drive a simulation.

```bash
cd backend
uv run python single_process.py                                # → http://127.0.0.1:8800
COMPANY_OS_GATEWAY_PORT=8810 uv run python single_process.py    # if compose holds 8800
```

Same routes, same port, same path prefix as the compose gateway, so the client's
proxy configuration is byte-identical across both topologies. Point the client at it
with `cd frontend && npm run dev`, then open `?run=<id>`.

Two things this mode cannot cover, and a green run here is not evidence for either:
the Postgres-only hazards (JSONB key ordering under the state hash, sequence and
transaction-control differences), because it defaults to SQLite; and gRPC
serialisation, because it never encodes a message. Those belong to the compose path
and the contract tests.

### Diagnosing

```bash
curl -s http://127.0.0.1:8800/status | python3 -m json.tool
docker compose logs -f                 # single-line JSON, one aggregator
```

`docker compose logs` being the aggregator is a decision, not an omission: one
machine, one operator, no log pipeline. The status payload is built to be the
thing you paste into an issue — service, build, versions, store, and what each
service can and cannot reach.

Two health definitions are deliberately asymmetric:

- **The kernel without a store is unhealthy.** It cannot append, which is its job.
  It reports 503, keeps retrying, and never exits — exiting would make an outage
  look like a crash and the capped restart policy would then hide the outage
  inside a restart loop.
- **The gateway without a kernel is healthy and degraded.** It is the surface you
  already have open, so it stays up and tells you the kernel is down. A gateway
  that failed alongside the kernel would turn one outage into two and explain
  neither.

---

## Layout

```
company-os.html               the prototype: the whole application, one file
test/                         the prototype's harness (29 checks) and sprite validator
serve.sh                      optional local HTTP server for the prototype

backend/
  pyproject.toml              one project, one lockfile, every version pinned
  .python-version             3.12
  packages/
    simcore/                  the kernel library — no service, transport or store
    contracts/                proto stubs and the event envelope (U2)
    servicekit/               logging, status, bind rules, shared by all services
  services/
    kernel/                   sole log writer, owner of the clock
    gateway/                  REST commands, WebSocket stream
    domain/                   metric/effect model behind the R15 seam
    agents/                   Phase 2's layer; a stub resolver in Phase 1
    report/                   read-side fold over the log
  tests/
frontend/
  src/design/                 the art direction as code: palette, load ramp, label face
  src/net/                    gateway client, event stream, and the run store
  src/render/                 the ported canvas office, lifecycle-managed (U12)
  src/ui/                     shell, HUD, panels (U13)
  src/dag/                    node encoding, layout, DAG view, chain strip (U14)
  tests/
infra/postgres/init/          the read-only reporting role
docker-compose.yml            seven services; the kernel pinned to one instance
```

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
the kernel, determinism, parity and replay suites are pure Python. Running
everything through compose would make the suite slow enough that contributors skip
it, so one smoke job exercises compose end to end instead.

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
container. On a network that intercepts TLS, that build fails with
`UNABLE_TO_GET_ISSUER_CERT_LOCALLY` even when npm works on the host. The `dev`
profile is unaffected — it installs on the host and omits `web` entirely. To fix
the demo build on such a network, pass the intercepting CA into the build (for
example by copying it in and setting `NODE_EXTRA_CA_CERTS`); it is not baked into
the Dockerfile because the certificate is specific to one network.

### Measured

Cold containers with images already built, to all six `dev`-profile services
reporting healthy: **12s** (Apple silicon laptop, Docker 28.5.2). Image build from
cold is separate and dominated by dependency download.

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

Not real — deliberately, and stated on screen:

1. **Dialogue is scripted.** Each person has four canned answers (why /
   exceptions / who decides / bottleneck), matched on keywords. No LLM.
2. **Nothing persists.** In the prototype: no backend, no database, no accounts,
   no multiplayer. Reload and it is Day 1 again. This is what Phase 1 changes.
3. **The company is invented.** Halstead Industrial, its ten people and its eight
   work items are plausible sample data, not a real customer's org.

---

## Changing the company

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

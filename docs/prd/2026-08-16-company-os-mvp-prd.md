---
title: "Company OS — MVP PRD"
type: prd
status: active
date: 2026-08-16
origin: "TokenBI MVP PRD (deprecated skeleton, reproduced in Appendix A)"
glossary: CONTEXT.md
---

# Company OS — MVP PRD

## Summary

Company OS is an open-source company simulator. You are the CEO: you walk the floor,
talk to the people who report to you, hand them work, and the run stops at the points
only you can settle. Four directors are model-backed experts who brief a decision and
are required to object to it, and who never rank the options. Any decision you have
already taken can be **forked** into a persistent parallel **Timeline** and played
forward, and the whole **Universe** of timelines folds into one exported HTML **Report**
that says where the company was overloaded and what is worth automating.

The MVP ships to GitHub. Its audience is people who find the repository and a live
ten-minute demo for investors, and its measure is whether either of them wants to run
it again. Everything below is scoped to that and to nothing else — there is no hosted
service, no real-company data, and no customer.

This document sits above the three requirement documents already in `docs/brainstorms/`.
It names the product, the user and the MVP boundary; it does not restate mechanics those
documents already specify. Requirements here are numbered **M1–M67** so they never
collide with the `R`-numbered requirements those documents use.

---

## Problem Frame

Two phases have shipped. The simulation works and the product does not yet exist.

**What is built.** An event-sourced kernel where advancement is a pure function from
state and inputs to a new state plus an ordered list of events, and persisted state is
the fold of an append-only log. Eight commands: assign, reassign, return to backlog,
resolve a checkpoint, move the CEO, request a hire, ask a person a question, compare
options. A roster of ten people across four reporting lines, eight authored work items
carrying nine checkpoints, five metrics, a recurring departmental draw that regenerates
every business day, a capacity ceiling that degrades rather than blocks, hiring with a
lag, a workflow DAG with a collapsed chain strip, a HUD carrying trajectories, runway,
capacity heat and decision pressure, a conversation that opens on proximity, and — as of
this week — each option's authored consequence, a per-option comparison branch, and the
authored-tuning marking on every rendered figure. It is covered by a parity suite, a
replay suite, a store suite and golden vectors on the client.

**What is not.** Everything the skeleton PRD called the product. There is no agent: the
agents service ships a stub whose entire behaviour is to decline every request, and no
model call exists anywhere in the system. There is no memory, no personality beyond an
authored dialogue table, and no concept of one person seeing another's work. There is no
persistent fork — the comparison branch runs in memory and is discarded, and the store's
fork machinery derives a child id from parent and sequence alone, so two forks at one
tick collide, and the child arrives paused with nothing to advance it. The report is
folded from the log by a service the client never calls. And the documented
`docker compose up` path answers 503 to every route, because the gateway talks to a
kernel client and the gRPC leg was never built.

**What the skeleton got right, and where it drifted.** The skeleton PRD (Appendix A) is
the source of this document, and its instincts hold: the simulation is the selling
point, replaying a decision into a different future is the beat that sells it, and
overload is the evidence that makes the whole thing mean something. Where it drifted was
vocabulary and scale. It called forking "replay", which is the name this codebase
already uses for the deterministic re-fold that makes forking trustworthy in the first
place. It asked for every NPC to be an agent with its own memory, environment,
personality and parallel work, which is roughly fourteen concurrent model contexts
against a simulation that advances ten thousand times per run. And its 2C users — the
Amazon seller who wants to know what to sell — need external market data that no part of
this system can ground a figure against.

**The asymmetry is the whole shape of this MVP.** The simulation half is largely done and
well tested. The agent half is entirely unbuilt. So this document spends its requirements
where the product is missing and its non-goals where the temptation is to rebuild what
already works.

---

## Who this is for

**The repository is the product surface.** The measure is stars, which is to say: does a
stranger who lands on the README understand what this is in eight seconds, get it running
in one command, and see something in the first minute that they have not seen before.
Everything in the MVP either serves that or is cut.

**The second audience is an investor watching a live ten-minute demo.** Live, not
recorded — the claim is that outcomes are not scripted, and a recording cannot make that
claim. The demo runs the same artifact from the same one command, and ends with the
exported report.

These two audiences want the same build. The founder-facing product, the real-company
mirror, and the e-commerce vertical the skeleton named are all downstream of it and none
of them is in scope. What the MVP has to earn is the right to be continued.

---

## Key Decisions

**One backend container, and the service split waits.** The six-service compose stack is
an architecture for a day that has not arrived. On a laptop it is six containers of
ceremony around a process boundary nothing crosses, and it currently does not work at
all, because the client leg for that boundary was never built. So the MVP ships the
single-process launcher in one container next to the client, and `docker compose up`
becomes true.

*Rejected alternative: build the gRPC leg.* It is roughly one unit of work — one channel,
one client implementation, and `use_kernel` already takes it — and it would preserve the
split. It is rejected because nothing in the MVP needs the split, and the day it does
(hosting, more than one kernel) is the day the leg should be built against a real
requirement rather than a compose file's shape. This is deferral with a reason, not
oversight, and it is written here so a contributor reads it that way.

**The bench is four directors, and the schema is for everyone.** The skeleton wants every
NPC to be an agent. This narrows model invocation to the four directors and leaves the
six specialists scripted, which is the decision `docs/brainstorms/2026-08-14-agent-team-requirements.md`
already argued: ten personas is a content problem, four department experts is a mechanism.

What comes forward from the skeleton unchanged is the *schema*. Every person carries a
department, a title, a responsibility, and the tools, MCP servers and skills they are
described as having — authored for all ten, whether or not a model answers for them.
That costs almost nothing, it is what the report reads when it says which work a person
could plausibly hand to software, and it means lighting up more of the roster later is
configuration rather than a redesign.

The cost is accepted and named: in this MVP those tools do not execute. They are
description. A director says what it would use and the report counts it; nothing is
invoked.

**Agents are invoked when spoken to, never per tick.** A run is about ten thousand ticks.
Per-tick model calls fail on latency and on cost, and Phase 1 rejected them for that
reason. Parallel work and cross-department dependency — the two things the skeleton
wanted agents for — are already mechanics in the kernel: multiple items advance
simultaneously, items carry prerequisites and unlocks, and two of the eight require a
cross-department meeting at twenty percent progress. So what the agent layer adds is not
parallelism. It is the framing, the objection, and the one thing the deterministic graph
cannot produce: a director who wants something another director knows.

**Authorization is the co-work mechanic.** The skeleton lists IAM as its own feature —
the user can see all working memory, and memory can be shared if the user approves. This
merges those two bullets into a single mechanic and makes it the answer to co-work. A
director that needs another line's knowledge asks for it; the CEO authorizes or refuses;
both are recorded; a refusal leaves the work blocked or degraded. That turns a compliance
checkbox into a beat you can play, and it is the part of this product that neither AI Town
nor Gather has.

*Rejected alternative: free agent-to-agent messaging over a shared bus.* Cheaper to build
and much worse, because it quietly deletes the human from the human-in-the-loop, which is
the premise the entire product rests on.

**The CEO never reads raw memory.** A director's memory is an event slice internally and a
rolling summary plus the events that mattered to the CEO. The selection of what mattered
is derived from the log; only the prose over it comes from a model. That keeps the panel
readable with no model configured, and it keeps the summary auditable — you can always
ask which events a sentence was written over.

**Retrieval is a query, not a search.** Memory lives in the store the log already lives
in, with vector support available alongside it. But the path an agent reads figures
through is structured: give me the events touching this line since day four, the current
draw for this department, the note on the deliverable that unlocked this item. The
citation contract requires a figure to name an event, and an event is a sequence number,
not a text chunk. Embedding-based recall also needs an embedding model, which would break
the keyless path. And the scale does not ask for it: a measured run emits roughly
forty-five events. Vector recall is the right answer at ten thousand and the wrong one
here — it is provisioned, not depended on.

**Whatever an agent saw is logged with what it said.** Agent statements already enter the
log as input events, which is what keeps replay exact. This extends the rule to the
retrieved context, because a re-ranked search at replay time would hand the agent a
different world and the log would not say so.

**Model responses are cached on the situation that produced them.** Forking and agents
interact badly by default: fork at day six, decide differently, and the day-nine briefing
comes back worded differently in each world for no reason — so the diff shows decision
consequence and model variance mixed together, and the one sentence the whole feature
exists to support ("this is what your different decision caused") becomes false. So a
response is cached on tick, person and request, and the cache is shared by a run and every
fork of it. Identical situations return identical advice; genuinely new situations call
the model.

*Rejected alternative: accept the variance and label it.* Honest, and it gives up the
claim. *Rejected alternative: temperature zero.* Reduces variance without providing
determinism, which is the property being bought.

**Forking is how a decision is revisited; history is never changed.** The skeleton asks
for the CEO to be able to interrupt and revise. Revision by mutation is incompatible with
an append-only log and would cost the product its audit story. Revision by forking costs
nothing, because the log is already what a fork copies. So "revise" and "replay a
decision" and "a different future" are all one mechanic, and the parent run is untouched.

**The diff is the payoff, not the switch.** Switching between saved worlds is a save-file
feature. Two timelines side by side at the same sim-day, metric by metric, with the
decision that separated them named — that is the shot that sells the product, and it is
what the report is built around.

**The report covers the Universe, and it prescribes.** A run's report is not one
timeline's story; it is the whole tree. And it does not stop at diagnosis. It says which
work is worth automating and what that pays back, because the arithmetic already exists —
automating work lowers a department's draw, a draw is staffed work that carries into the
daily burn, so an automation decision shortens the burn rather than only moving a metric.

What keeps that honest is that no prescription originates in a model. Proposals are drawn
from the authored catalog, each cites the events that motivate it, every figure carries
the authored-tuning marking, and the report says plainly that it describes an invented
company. A model writes the prose around cited figures and nothing else.

*Rejected alternative: let a model propose freely from the run.* Better prose, and it
would let a model invent a payback figure for a company that does not exist — which is the
one failure that makes the whole artifact indefensible in front of the audience it is
built for.

**A keyless clone is fully playable.** Most people who star a repository never configure
anything. So with no model configured the bench is absent, directors answer from the
scripted replies, and every other feature — comparison, forking, the timeline diff, the
report — works unchanged. The bench is what a key buys, not what the product requires.

**Scenarios are content, not structure.** People and work items move out of code and into
scenario files, so someone can author a different company and open a pull request with it.
The floor, the eight rooms and the four reporting lines stay fixed, because they are load
bearing in ways a scenario author should not have to know about: room order is stable
across grid sizes by construction, seats resolve by department, and the parity suite
asserts against the shipped floor.

*Rejected alternative: structural scenarios too.* It is the version people will ask for
and it is a real refactor — the floor planner, seat assignment, room ordering and the
forty-two-assertion parity suite all sit on the current shape. Deferred with the reason
written down, which is the form that gets a contributor to open the pull request instead
of filing an issue asking why it is hard-coded.

**Staff movement comes back off the deferred list.** Phase 2 deferred it by decision, and
the decision was right for a phase whose output was a verdict on the loop. It is wrong for
a phase whose output is a repository: the README's central claim is that employees move
along the real reporting lines, and right now every person is drawn frozen at their desk.
A director walking across the floor to his specialist's desk is the hero animation of a
project like this. The client half already exists and is golden-tested; what is missing is
an event carrying a person, a path and a start tick.

---

## Requirements

### Deploy and first run

- **M1.** `docker compose up`, with no profile and no prior provisioning, brings up a
  playable client.
- **M2.** The backend is one container running the single-process launcher — kernel,
  gateway, domain, agents and report in one process.
- **M3.** The store is the only stateful dependency and provisions itself on first boot.
- **M4.** A run needs no model key, no external account and no manual step before it is
  playable.
- **M5.** The client creates its own run, and the run id goes into the address bar so a
  reload re-attaches rather than starting a second one.
- **M6.** A new run opens with one director already holding an unsettled checkpoint, so
  the first thing on screen is a person waiting rather than an empty floor.
- **M7.** Two first-run hints, each shown once and never again: how to walk, and that
  standing next to someone opens a conversation.
- **M8.** The repository is licensed Apache-2.0.

### Scenarios

- **M9.** People and items load from scenario files at genesis rather than being compiled
  in.
- **M10.** The bundled default scenario is the shipped company, and it is what the parity
  and replay suites assert against.
- **M11.** A scenario authors who sits in the company and what work exists. The floor, the
  eight rooms and the four reporting lines are fixed.
- **M12.** A scenario naming an unknown department, an unknown person, or an unresolvable
  prerequisite is refused at load with a reason. It is never partially loaded.
- **M13.** Adding a scenario requires no code change.

### The bench

- **M14.** Four directors are model-backed. Specialists remain scripted, and the tacit
  line and the ask intents are unchanged.
- **M15.** Every person carries department, title, responsibility, and the tools, MCP
  servers and skills they are described as having — authored for all ten regardless of
  whether a model answers for them.
- **M16.** Those tools, MCP servers and skills are descriptive in this MVP. They shape
  what a director says it could do and what the report counts; nothing is executed.
- **M17.** A briefing or an objection is raised when the CEO opens a checkpoint in person.
  Never per tick.
- **M18.** A director never ranks options, never names a preferred one, and never asserts
  one is better. A statement that does is rejected before the CEO sees it.
- **M19.** No figure without its source. Every number in a statement carries the event,
  branch result or authored content it came from, and resolves back to it.
- **M20.** With no model configured the bench is absent, directors answer from the
  scripted replies, and comparison, forking, the timeline diff and the report all work
  unchanged.
- **M21.** A call that errors, times out or is rate-limited, and a statement rejected under
  M18 or M19, fall back to that director's scripted reply for that turn.
- **M22.** A briefing and an objection move no metric.

### The model gateway

- **M23.** One package normalizes providers, over two code paths: an OpenAI-compatible
  base URL, and Anthropic native.
- **M24.** Configurable and documented: OpenAI, Azure OpenAI, OpenRouter, Anthropic,
  Ollama, LM Studio, and any OpenAI-compatible URL — which covers vLLM and SGLang.
- **M25.** Provider, model and base URL are configuration, never code.
- **M26.** No general-purpose LLM framework enters the dependency tree.
- **M27.** Every run carries a hard ceiling on model calls and on tokens. Reaching it stops
  model calls and falls back to scripted replies; it never stops the run.
- **M28.** The call and token count against that ceiling is visible on screen during a run.
- **M29.** Keys, auth headers and raw provider request or response envelopes never enter
  the log, the report, or a scenario file.
- **M30.** Continuous integration exercises the keyless path and a mock adapter. The
  provider list is a documented configuration table, not a test matrix.

### Determinism

- **M31.** A model statement enters the log as an input event, so replaying that log
  reproduces the run exactly.
- **M32.** The context retrieved for a statement is logged with the request that used it,
  so a replay reproduces what the director saw and not only what it said.
- **M33.** Model responses are cached on the situation that produced them — tick, person,
  request — and the cache is shared by a run and every fork of it.
- **M34.** Two timelines that differ only in a decision differ only because of that
  decision. Model variance is never presented as consequence.
- **M35.** A comparison branch is never written to the store.

### Memory and Authorization

- **M36.** Each director carries a memory scoped to their reporting line.
- **M37.** The CEO can read every director's memory as a rolling summary plus the events
  that mattered. Raw memory is never the CEO's reading surface.
- **M38.** The selection of which events mattered is derived from the log. Only the prose
  over that selection comes from a model, so the summary is readable with no model
  configured.
- **M39.** A director may not read another director's memory without Authorization.
- **M40.** A director needing another line's knowledge raises a request for it. The CEO
  authorizes or refuses, and both are recorded.
- **M41.** A refusal leaves the work blocked or degraded. It never silently succeeds.
- **M42.** An Authorization applies to the request that asked for it. It is not a standing
  permission, and there is no settings surface that grants one.
- **M43.** Every Authorization and every refusal appears in the report as part of what the
  CEO did.

### Forks, Timelines and the Universe

- **M44.** Any past checkpoint in a run can be forked, producing a new timeline that begins
  at that tick with a different option applied.
- **M45.** A fork is a run: it persists, survives a restart, and is played forward exactly
  as its parent is.
- **M46.** A fork can itself be forked, to any depth.
- **M47.** Two forks taken at the same tick are distinct runs. Ids never collide and
  sequences never overlap.
- **M48.** Forking never changes the parent. No event is ever deleted or rewritten.
- **M49.** The Universe is presented as a tree, and the player switches between timelines
  from it.
- **M50.** Two timelines can be diffed at the same sim-day: metric by metric, with the
  decision that separated them named.
- **M51.** Comparison stays distinct from forking and is unchanged — an in-place preview at
  an open checkpoint, discarded when it closes.
- **M52.** Every figure in a diff carries the authored-tuning marking.

### The Report

- **M53.** A report covers the whole Universe, not one timeline.
- **M54.** It exports as one standalone HTML file that opens with no server and no network
  request.
- **M55.** Every claim resolves to the event that produced it.
- **M56.** It states where the company was overloaded, by department and over time.
- **M57.** It proposes what is worth automating, drawn from the authored catalog, each
  proposal citing the events that motivate it and the payback it implies.
- **M58.** Prose in the report is written over cited figures only. No figure originates in
  a model.
- **M59.** Every figure carries the authored-tuning marking, and the report says plainly
  that it describes an invented company.
- **M60.** It carries a link and a QR code to the repository.
- **M61.** The report is reachable from the client.

### Closing the known holes

- **M62.** Staff movement is transmitted: an event carries the person, the resolved path
  and the start tick, and the client interpolates it. A director walking to a specialist's
  desk is visible on screen.
- **M63.** The command path is synchronised against the tick loop.
- **M64.** A comparison cannot occupy the worker pool every run's clock depends on.
- **M65.** The determinism suites cover a run and its forks, not only a run.

### Launch

- **M66.** The README leads with an eight-second hero: walk, conversation, decision, then a
  cut to the timeline tree with two futures side by side.
- **M67.** The README's first command is the one that works, and continuous integration
  verifies it.

---

## Non-goals

Each of these is a decision, not an omission. They are listed so a reader of the public
repository knows which are deliberate.

**The gRPC leg and the six-service split.** Nothing in the MVP crosses that boundary. It
should be built the day hosting or a second kernel asks for it, against that requirement.

**Hosted or multi-user deployment.** Local only. Hosting turns the client-owned CEO
position from a correctness guard into a real trust boundary, and brings accounts, key
custody and cost control with it. The same container behind one URL is the obvious next
step; it is not this step.

**Validating the in-person claim.** `resolve_checkpoint` trusts the caller's `in_person`
flag. Single-player and local means there is no adversary, and the residual risk is a bug
rather than an attack. It becomes required the day the product is hosted.

**Structural scenarios.** Departments and rooms stay fixed. See Key Decisions for the
cost.

**Real-company data and external sources.** Every figure in this system is authored
tuning, and the citation contract can only bind a figure to an internal source. External
lookup arrives with the market that needs it, and being wrong on an invented company is
free.

**Autonomous per-tick agent action.** Directors act when spoken to and when a checkpoint is
opened. Parallel work and cross-department dependency are already kernel mechanics and do
not need a model to produce them.

**A general-purpose LLM framework.** Two HTTP shapes do not justify the dependency
surface, and bring-your-own-key users would meet its configuration surface as a support
burden.

**Vector search as the primary retrieval path.** Provisioned alongside the log, not
depended on. See Key Decisions.

**Executing an agent's tools, MCP servers or skills.** Descriptive in this MVP.

**An in-app scenario editor.** Scenario files and a pull request are the authoring flow.

---

## Risks

**The agent half is the whole unbuilt half.** Every requirement from M14 to M43 is new
construction against a service that today only declines. This is the part of the plan most
likely to be underestimated, and the keyless path (M20) is what keeps a slip from being
fatal — the product remains shippable with the bench absent.

**The prescription is the credibility surface.** M57 is the requirement most likely to be
implemented as "ask the model what to automate," which would be faster and would make the
report indefensible. M58 exists to stop that and should be treated as load-bearing rather
than as hygiene.

**Determinism is easy to lose quietly.** M31 through M34 fail silently: nothing crashes
when a fork's diff starts reflecting model variance, and the feature keeps demoing well
while its central claim stops being true. M65 exists because the existing determinism
suites cover a run and would not notice.

**"Time is not the main problem" cuts both ways.** With scope prioritised over schedule
and one or two engineers, the risk moves from cutting features to never converging. The
public date — next month — is what should force sequencing, and the honest sequence puts
deploy and the known holes first, the bench second, and forking third, so that at any
point there is something worth showing.

**pgvector is not in the current image.** The compose stack runs an Alpine Postgres with
no vector support. Enabling it means a different base image, which is small work and a
real change to the one command M1 promises.

---

## Appendix A — the origin skeleton

Reproduced verbatim from `TokenBI MVP PRD.pdf`, which this document replaces. The product
name TokenBI is deprecated; the product is Company OS. The PDF is deleted from the
repository so the two names cannot be confused, and its content is preserved here.

> **MVP PRD Lite Version — MVP**
>
> 1. Main Function
>    - a. Playable Sandbox (User can control the ceo character move, talk and assign task to NPC)
>    - b. NPC is AI Agent (has different functions like Marketing, Sales, IT, etc.)
>    - c. NPC Schema easy version (role): Department, Title, Responsibility, Available tools / mcp / skills
>    - d. NPC Agent Team features: Own Memory; Own working environment like sub agent; Own Personality; Can share memory by auth; co work; parallel working
>    - e. IAM: user can see all the working memory, decision and so on; the memory can be shared if the user approval if one agent need other agent's info
>    - f. Human in the loop: User can make the key decision; user can interrupt and revise
>    - g. Simulation: the main selling point is simulation, this describe as user control the ceo character talk with different npc and assign task, make different decision with lead to different result; Replay, to make different decision mirror different world like doctor strange do in the avengers movies; Simulation has live metrics to show; Transparent working trace to read; After all the simulations, have a detailed report with visualization, summary and so on
>    - h. workload, workflow visualization during simulation: we can see during the simulation which department or employee overloaded; the overloaded will be the evidence for the phase 2 ai transformation decision story
> 2. Main 2C User
>    - a. Amazon Shop seller who do not know what to sale what prompt strategy i can use to improve the profit
>    - b. Start up company user, wants to know if i strat my product in specific error whats our future
>    - c. To play the simulation for fun
>
> Reference product, paper or idea: gather.town, matraix.ai, github.com/666ghj/BettaFish, github.com/a16z-infra/ai-town

**How the skeleton maps onto this document.** 1a is built. 1b and 1d narrow to M14–M22 —
four directors rather than every NPC, reactive rather than autonomous. 1c becomes M15 and
M16, authored for everyone and descriptive rather than executable. 1d's memory sharing and
1e merge into Authorization, M36–M43. 1f's key decisions are built; its "interrupt and
revise" becomes forking, M44–M52. 1g's replay becomes forking under its own name, and its
report becomes M53–M61. 1h is built and becomes the report's diagnosis, M56. Section 2's
users are out of scope: the MVP's audience is the repository and a live demo, and 2a in
particular needs external market data the citation contract cannot bind.

# Company OS

A company simulator in which the player is the CEO: they walk the floor, talk to the
people who report to them, assign work, and settle the points where work stops and
waits for them. This glossary is the language the product, its documents and its code
are written in.

## The run and its time

**Run**:
One simulation, from genesis to its horizon or to insolvency. Its state is the fold of
an append-only log of events.
_Avoid_: game, session, instance

**Genesis**:
The first event of a Run, which fixes everything the Run cannot later change — the
floor, the seats, the roster, the work catalog, the metric definitions and the seed.
_Avoid_: init, setup, bootstrap

**Tick**:
One sim-minute, and the quantum by which a Run advances. Every input is applied at a
tick boundary.
_Avoid_: frame, step, cycle

**Horizon**:
The tick at which a Run ends. Chosen at Genesis.
_Avoid_: deadline, end date, time limit

**Seed**:
The value that makes two Runs of one Scenario produce identical logs.

**Replay**:
The deterministic re-fold of a log to the state it produced. An engineering guarantee
about the kernel, never something the player does — revisiting a decision is a Fork.
_Avoid_: rewind, playback, undo

## Work and decisions

**Item**:
A unit of authored work. It belongs to a Department, wants a particular person, costs
effort, may require other Items or a level of Visibility, and may unlock others.
_Avoid_: task, ticket, job, story

**Checkpoint**:
The point in an Item's progress where work stops and waits for the CEO. It is an
information, approval or decision point.
_Avoid_: blocker, gate, milestone

**Option**:
One way to settle a Checkpoint, carrying what it does to the metrics and the sentence
it leaves on the record.
_Avoid_: choice, answer, action

**Tacit line**:
The sentence a person says only when a Checkpoint is settled in person. It is the
reason walking over beats clearing the queue.
_Avoid_: hint, flavour text, easter egg

**Tray**:
The queue of Checkpoints waiting on the CEO, and the cheap route to settling one.
Settling from the Tray costs morale and surfaces no Tacit line.
_Avoid_: inbox, notifications

**Backlog**:
Work that exists and is assigned to nobody. Distinct from the Tray, which holds
decisions rather than work.

**Deliverable**:
What a completed Item produces, carrying the record of which Options produced it.
_Avoid_: output, artifact, result

## People

**CEO**:
The player. The only actor the player moves, and the only one who may settle a
Checkpoint.

**Director**:
One of the four people who head a Reporting line. Directors are the people the agent
layer speaks through.
_Avoid_: manager, lead, head, boss

**Specialist**:
Anyone who is neither a Director nor the CEO. Specialists answer from an authored
script.
_Avoid_: NPC, employee, worker, resource

**Reporting line**:
The set of people reporting to one Director. Assignment, delegation and capacity all
follow the Reporting line, never the room someone sits in.
_Avoid_: team, org unit, squad

**Department**:
The room a person sits in, and the unit a recurring workload is drawn against. A
person's Department and their Reporting line may differ, and in the bundled Scenario
they deliberately do.

## Timelines

**Fork**:
A new Run branched from a past tick of another Run and played forward. Forking is how
a decision is revisited; history is never changed.
_Avoid_: undo, revert, rewind, branch, save state

**Timeline**:
A Run seen as one branch of the tree that Forking produces. What the player switches
between and compares.
_Avoid_: world, save, run (a Run is the thing; a Timeline is its place in the tree)

**Universe**:
The whole tree of Timelines descending from one Genesis. What the Report covers.
_Avoid_: campaign, project, workspace

**Compare**:
The preview at an open Checkpoint that shows where each Option travels, and is
discarded when the Checkpoint closes. It is not a Fork — nothing is kept and nothing
is played.
_Avoid_: simulate, what-if, preview branch

## The agent layer

**Bench**:
The Directors a model answers for in a given Run. With no model configured the Bench
is absent and the Run is still fully playable.
_Avoid_: agents, the AI, the LLM

**Briefing**:
A Director's account of an open Checkpoint: prose, plus a table of figures each
carrying the source it came from.
_Avoid_: advice, recommendation, analysis, insight

**Objection**:
A required statement, from the person holding a decision and from each Department it
affects, naming what they expect to go wrong. A Director never ranks Options and never
names a preferred one.
_Avoid_: warning, feedback, opinion, concern

**Memory**:
What a Director carries forward about their Reporting line. The CEO reads it as a
rolling summary and the events that mattered, never raw.
_Avoid_: context, history, knowledge base

**Authorization**:
The CEO's act of letting one Director read another's Memory. It is recorded, and it is
how work that needs another Department's knowledge gets unblocked.
_Avoid_: permission, grant, access, approval (an approval is a kind of Checkpoint)

## The economy

**Draw**:
A Department's recurring workload, which regenerates every business day and consumes
capacity whether or not the CEO does anything. It is not a metric.
_Avoid_: baseline, overhead, BAU

**Load**:
What a Reporting line is carrying against what it can carry. Exceeding it degrades
throughput and morale; it never blocks an assignment.
_Avoid_: utilization, capacity (Capacity is the ceiling, Load is the level)

**Visibility**:
How much of the company the CEO can actually see. It gates which work becomes
available.

**Runway**:
How long the company can continue at its current burn.

**Authored tuning**:
The marking every figure the product shows must carry, because every metric movement
in the simulation is a constant somebody chose rather than a modelled result.
_Avoid_: estimate, projection, forecast, prediction

## What is authored, and what comes out

**Scenario**:
The authored people and work that make one company. A Run is a Scenario played.
_Avoid_: template, preset, config, level

**Report**:
The standalone document a Universe folds into: what happened in each Timeline, where
the company was overloaded, and what is worth automating. Every claim in it names the
event that produced it.
_Avoid_: summary, results, analytics, dashboard

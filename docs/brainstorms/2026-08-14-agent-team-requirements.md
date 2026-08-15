---
date: 2026-08-14
topic: agent-team
---

# Company OS — The agent team

## Summary

A bench of four department-expert agents, seated in the directors' chairs, that brief a decision, cite every figure to a source, and are required to argue against the option the CEO is leaning toward. Each option at a checkpoint shows its authored consequence, and any open checkpoint can be forked one branch per option and advanced to the next decision or the horizon, so the CEO sees the shape of each option — with no further decisions taken — before committing, every number marked as authored tuning. The bench is judged on the existing invented company; the real-company mirror waits for a design partner.

---

## Problem Frame

Standing in front of a specialist at a checkpoint, the CEO's whole basis for deciding is a prompt, three option labels, and three authored sentences. `Option` in `backend/packages/simcore/items.py:41` carries four fields — label, detail, effect, note. `DecisionOption` in `frontend/src/ui/conversation-model.ts:223` carries two. The metric deltas stop at the wire and never reach the screen. Walking over adds one tacit line.

So the decision surface has no expertise behind it and no consequence in front of it. A founder who does not know marketing has nobody to consult, and no option shows where it lands. The ask box does not close the gap: four canned answers per person, keyed on `why / exception / axis / bottleneck`, and it is spent after four.

The harder case is the one a chat window cannot serve. Two options both look reasonable, the return is unknown, and something must still be chosen. Today that falls to instinct with no data at all.

The platform doc records the ambition — every person an LLM agent with memory scopes — but describes the layer mechanically and never says what the person playing gets from it. The seam is built and idle. `backend/services/agents/stub.py` ships declining every request. `backend/packages/simcore/pending.py` carries sim-tick deadlines, per-item and per-run caps, and answer validation that rejects rather than clamps. The gRPC `Resolution` message carries ids, permitted options and a rules version — nothing an agent could reason with.

One inherited fact bounds everything below. Every metric delta in the simulation is a tuning constant, and the platform doc's R41 requires each surface showing one to say so. The audit found that marking in two code comments and on no rendered surface.

---

## Key Decisions

**The bench is four department experts, and they are the directors.** The roadmap says every person becomes an agent. This narrows it: `dir_sales`, `dir_admin`, `dir_cs` and `dir_hr` become agent-backed, and specialists stay scripted. Ten personas is a content problem; four department experts is a mechanism. It also gives the walk something to buy. The comparison's arithmetic is free from either route, so what standing next to a director earns is the framing, the objections and the tacit line — not the numbers. And it leaves the tacit-line mechanic exactly as it was tested.

**No figure without its source.** An agent may never state a number it did not source. Framing and interpretation are the agent's job; numbers come from a logged event, a branch result, or authored option content. Every figure travels with the source it came from and resolves back to it, which is R53's report contract extended to live advice. This is the answer to fabrication in both markets, and it is the rule that lets external-lookup sourcing arrive later as a new source kind rather than a redesign.

Enforcing it requires the statement to arrive structured rather than as prose. `validate_agent_answer` decides exactly one thing — whether a chosen string is in a permitted tuple — and no check of that shape can ask whether every numeral in a paragraph has a source. So an agent statement is prose plus a typed figure table, and the prose names a figure only by its slot. Rejection is then a membership test rather than a semantic judgment.

**The agent objects; it never ranks.** The person holding a decision and each affected department state an objection or state that they have none. This is the challenge-and-response model from aviation and surgery, not the advisory model. A recommendation would convert a hand-typed constant into a colleague's considered judgment, which is the most persuasive available form of an arbitrary number. The override is what gets logged, and it gives the report its most defensible line: what you were warned about, and what you chose anyway.

The rule needs the same enforcement the citation rule gets, or it is only an instruction. Naming a preferred option, ordering the options, or asserting one is better is rejected on the terms an uncited figure is rejected on.

The round is raised when the CEO opens the decision, not when they commit. Model output reaches the log only through the pending-input contract, which applies an answer no earlier than the next tick and abandons a request at its deadline, while command validation and application are deliberately coupled in one step. Raising at open means the statements are already logged when the commit arrives, so no two-phase commit is invented for a kernel that does not have one.

**Dissent never blocks, and dismissal is recorded.** An objection can be dismissed unread. Recording the dismissal is the mitigation against dissent becoming noise the CEO clicks past — the same soft-ceiling philosophy platform R22 applies to over-assignment.

**The deltas reach the screen first.** The Problem Frame's gap is two fields wide: `Option` carries `effect` and `note`, and `DecisionOption` carries neither. Carrying them through and marking them per R27 puts each option's authored consequence on the checkpoint at a fraction of a branch engine's cost, and `StoppedCard` already routes the tacit line down that same path. This ships as the baseline the comparison has to beat. What the comparison adds over it is the option's shape over time — where the metrics travel between now and the measured tick, and what the option opens or closes — which a static delta table cannot show.

**The forecast shows full trajectories, marked as authored.** Fork at the decision tick, apply each option, advance the branch, and report each metric's trajectory rather than a single endpoint. *Rejected alternative: commitment shape only* — runway, what the option unlocks or forecloses, and whether it stays reversible. That version is epistemically safer because none of it rests on the deltas, and it was rejected because it does not resolve the tie the CEO needs resolved. The cost is accepted and it is large: R41's marking becomes the only thing standing between the CEO and a confident chart computed from constants.

**R41's marking ships in this phase.** It is currently on no rendered surface. Every trajectory, delta, runway figure and derived value this phase adds says it is authored tuning, and the marking travels with an agent's prose about a number, not only with the chart. This is the safety mechanism of the feature rather than hygiene around it.

The marking is a dedicated token — a persistent glyph and label, never a hue. Amber is reserved for a person waiting on the CEO and nothing else may use it, and the remaining colour is already spent on the department stripe and the load bar. A pervasive new marking is exactly the requirement an implementer would satisfy with warning-yellow, which would dilute the one signal the design system protects.

**A briefing and an objection move no metric.** Only decisions and the existing ask intents move metrics. An expert reasons about what is already visible; it does not reveal what is not. Paying Visibility for a briefing would turn four directors into an unlock farm and trivialise the gate that platform R8 exists to hold.

**Compared branches reuse the parent's agent inputs.** Agent statements enter the log as input events per platform R3, so replay stays exact — but two fresh runs from one seed no longer agree, which `pending.py` already distinguishes as replay determinism versus seed determinism. A comparison whose branches drew fresh model answers would show the CEO model variance dressed as decision consequence. So branches reuse the parent's logged inputs, and an input the parent has none for is drawn once and shared across every branch of that comparison.

**The fork comes forward from Phase 4, narrowed.** Phase 4 owns fork-from-day-N as a strategy-comparison interface between long-lived branches. This phase takes the narrow case: fork at a checkpoint, one short-lived branch per option, discarded when the comparison closes. `fork_run` exists at `backend/services/kernel/store.py:387`.

**A branch settles nothing, and says so.** No CEO is inside a branch, R17 forbids the expert from choosing, and the shipped resolver declines — so every checkpoint a branch reaches after the fork tick stalls its item and stops that assignee. Measured on this repo: a fresh run driven to its 10,800-tick horizon with no commands leaves all eight items in backlog and moves only cash, from 4800 to 4100. A branch run blindly to the horizon would therefore report the same stalled company for every option. So a branch advances to the first downstream checkpoint it raises, or to the horizon if it raises none; every figure it reports names the tick it was measured at; and it is presented as this option with no further decisions taken, never as where the metrics land. Downstream checkpoints are never auto-resolved inside a branch, because settling them is the CEO's act.

**The numbers are free; the framing is not.** A comparison is reachable from a conversation or from the tray, because rationing the branch arithmetic would ration the decision support this phase exists to provide. The expert's framing and the objections require standing there. That splits along the line the product already draws — arithmetic is cheap, what a person knows is not — and it gives the walk a second thing to earn beyond the tacit line.

**The scripted replies stay as the fallback, not only the no-key path.** Reaching a running simulation without provisioning external services is a Phase 1 success criterion. With no model key the bench is absent, directors answer from `VOICE`, and the run is fully playable rather than degraded. The same posture covers every way a model answer fails to arrive usable — a call that errors, times out or is rate-limited, and a statement rejected under R11 — because a strict citation guard whose only failure mode is a surfaced error would turn the most common model behaviour into a dead end at the one surface this phase exists to improve.

The comparison is not gated on a key. Its arithmetic is the kernel's own over authored constants, so a keyless run can compare options even though it has no bench. Gating it would narrow the clone-and-run criterion for nothing and contradict this document's own reason for putting the numbers in the tray.

**Sources are internal this phase.** On invented data an external lookup has nothing to ground against — Halstead has no real invoice volume to check. So the three source kinds are the log, the branch and authored content. External-lookup sourcing arrives with the market that needs it.

**The mirror is the destination, not this phase.** The product's identity is a mirror of a real company, pointing a real founder at where automation actually pays. This phase builds the substrate on invented data, where being wrong is free.

What it proves is narrower than the mechanism, and worth stating so the mirror phase inherits a visible risk rather than an absorbed one. This phase proves the citation, objection and marking contracts hold. Whether expert framing actually improves a founder's decision is not testable here: on invented data an expert's advice can be checked for citation but not for accuracy, and a branch predicts the author. That test arrives with the mirror.

---

## Actors

- A1. **CEO** — the human player. Consults an expert, runs a comparison, hears objections, decides or overrides.
- A2. **Department expert** — a director, agent-backed. Briefs within its own department, cites every figure, raises objections. Never ranks options.
- A3. **Specialist** — existing simulated staff. Holds work, raises checkpoints, carries the tacit line. Not agent-backed in this phase.
- A4. **Kernel** — advances the clock, raises pending requests, folds logged answers, runs branches.

---

## Requirements

**The bench**

A department here is a reporting line, not a room. `PersonSpec.dept` is the room someone sits in and `mgr` is who they report to, and the two disagree on purpose — Priya sits in Accounting and reports to the Administration director. Two work items are authored against the accounting room and no director sits in it, so routing by room would leave two of the nine checkpoints with no expert to brief them.

- R1. Each of the four load-bearing departments has exactly one expert agent, and it is that department's director.
- R2. A briefing opens by standing within talk range of a director, under the same proximity rule that opens any conversation.
- R3. Specialists remain scripted. The tacit line and the four ask intents are unchanged.
- R4. An expert's scope is its reporting line, so every person and every work item maps to exactly one expert. An expert answers within that scope and declines outside it by naming the expert that owns the question.
- R5. An expert is invoked at a decision point, a briefing, or an objection. Never per tick.
- R6. A briefing already given persists for the life of the run and is re-readable both on the expert and, when it was given against an open checkpoint, inside that checkpoint's conversation — so the framing is present where the decision commits.
- R7. A briefing and an objection move no metric.
- R8. With no model key configured the bench is absent, directors answer from the scripted replies, and the run is fully playable. The branch comparison stays available, because its arithmetic needs no model.
- R31. A model call that errors, times out or is rate-limited, and a statement rejected under R11, fall back to that director's scripted reply for that turn rather than surfacing a rejection or blocking the interaction.

**Citation**

- R9. An agent statement is prose plus a typed figure table. Each entry carries its value, its source kind — a logged event, a branch result, or authored option content — its source id, and R27's marking. The prose names a figure only by its slot.
- R10. A cited figure resolves to its source, and a logged-event source is identified and locatable in the log. Playing that event back in the office arrives with platform R53's client surface, which stays deferred.
- R11. A statement is rejected before it reaches the CEO when a slot does not resolve, when its prose carries an unbound numeral, or when it names a preferred option, orders the options, or asserts one is better. The rejection carries a reason per platform R40.
- R12. Agent output is bounded and validated on the same terms as a resolution: rejected, never clamped.
- R13. Each agent statement enters the log as an input event per platform R3, carrying its logical prompt text, its model identifier, and its cited source ids. API keys, auth headers, and raw provider request or response envelopes never enter the log.

**Objection**

- R14. The objection round is raised when the CEO opens a decision in person, not when they commit it, so every statement is logged before the commit command arrives. The person holding the decision states an objection or states that it has none.
- R15. Each department the branch materially affects states an objection or states that it has none. One round is raised as a single pending request carrying every party, so a round costs one outstanding request against the per-item cap rather than one per department.
- R32. A party whose statement is unanswered at its request deadline is recorded as unavailable to object, never as having none. A commit is never blocked waiting for one.
- R16. An objection names what it expects to go wrong and cites its source. An objection that can cite nothing is not raised.
- R17. An agent never ranks options and never recommends one, and R11 rejects a statement that does.
- R18. An objection can be dismissed unread, and the dismissal is recorded.
- R19. A commit over a raised objection records which objections were raised and what was chosen anyway.
- R20. The report fold carries the override record per decision, and marks a tray-resolved decision as taken with no objection heard.
- R33. The override record renders on the decision surface this phase builds, so what the CEO was warned about is readable during the run rather than only in the deferred report client.

**Branch comparison**

- R21. At an open checkpoint the CEO may run one branch per option, forked at that tick and advanced to the first downstream checkpoint it raises, or to the run's horizon if it raises none. The comparison is reachable from a conversation and from the tray, with or without a model key.
- R22. A branch reports each metric's trajectory, runway, and which work the option unlocked or foreclosed, every figure naming the tick it was measured at. A branch takes no action at any checkpoint or unassigned item it reaches, and is presented as this option with no further decisions taken — never as where the metrics land.
- R23. Branches reuse the parent run's logged agent inputs. An input the parent has none for is drawn once and shared across every branch of that comparison.
- R24. A branch is identified by its parent run, its fork sequence and its option index, so branches of one comparison do not collide. It never mutates the parent run. Discarded means terminated and excluded from run resumption and readiness reporting; the event log refuses deletion, so a branch's rows persist.
- R25. A comparison neither advances nor rewinds the parent run's clock. Its result carries its fork tick and is refused as stale once the parent has moved that checkpoint's item — resolved, completed, reassigned, or its assignee lost.
- R26. The branch count per comparison is bounded, and the bound is part of the rules version.
- R34. Closing a comparison returns the CEO to that checkpoint's own option list, in whichever surface it was opened from, to commit. A comparison informs a choice and never commits one.

**Marking**

- R27. Every trajectory, delta, runway figure and derived value this phase renders is marked as authored tuning, on every surface including the report, per R41 and R61.
- R28. No surface presents an agent's framing of a number without that number's marking travelling with it.
- R35. Each option at a checkpoint shows its authored effect and note, marked per R27, by carrying through the fields the server-side option already holds.
- R36. The marking is a dedicated token — a persistent glyph and label — and never a hue, so it cannot be satisfied with the amber reserved for a person waiting on the CEO.

**Determinism and testing**

- R29. Re-folding a log containing agent statements reproduces the run exactly, without opening the model stream.
- R30. A run with the bench absent passes every assertion that passed before this phase.

---

## Key Flows

- F1. **Brief a decision**
  - **Trigger:** A checkpoint is open in a department.
  - **Actors:** A1, A2, A4
  - **Steps:** The CEO walks to the director whose reporting line owns the item; the expert frames the decision in that line's terms; every figure it states carries a source the CEO can follow. The briefing stays re-readable inside that checkpoint's conversation.
  - **Outcome:** The CEO understands a decision in a field they do not know, can check each number's origin, and still has the framing in front of them when they commit.
  - **Covered by:** R2, R4, R6, R9, R10, R28

- F2. **Compare the options**
  - **Trigger:** The CEO is at an open checkpoint and does not know which option is better.
  - **Actors:** A1, A4
  - **Steps:** From the conversation or the tray, with or without a model key, one branch runs per option from that tick, reusing the parent's agent inputs, each advancing to the first downstream checkpoint it raises or to the horizon; each branch returns its trajectories, its runway, and what it unlocked or foreclosed, every figure naming its measured tick. Closing the comparison returns the CEO to that checkpoint's option list.
  - **Outcome:** The CEO sees the shape of each option with no further decisions taken, marked as authored tuning throughout, and the parent run is untouched.
  - **Covered by:** R21, R22, R23, R24, R25, R27, R34

- F3. **Be argued with**
  - **Trigger:** The CEO opens a decision in person.
  - **Actors:** A1, A2, A3
  - **Steps:** One round is raised carrying the holder and each affected department; each states an objection or none, and a party silent at its deadline is recorded as unavailable to object; the CEO then reconsiders or commits anyway.
  - **Outcome:** The decision carries what was raised against it, readable during the run as well as in the report.
  - **Covered by:** R14, R15, R16, R17, R18, R19, R20, R32, R33

- F4. **Play without a key**
  - **Trigger:** No model key is configured.
  - **Actors:** A1, A3, A4
  - **Steps:** Directors answer from the scripted replies; no briefing and no objection, but each option's authored consequence still renders and the branch comparison still runs.
  - **Outcome:** The run plays end to end with no bench, and still reaches every part of this phase that needs no model.
  - **Covered by:** R8, R30, R31, R35

---

## Acceptance Examples

- AE1. **Covers R11.** Given an agent statement containing a figure with no source, when it is validated, then it is rejected with a surfaced reason and never reaches the CEO.
- AE2. **Covers R10.** Given a figure an expert cited to a logged event, when the CEO follows it, then that event is identified and located in the log.
- AE3. **Covers R4.** Given the Sales expert asked about hiring lag, when it answers, then it declines and names the People expert.
- AE4. **Covers R17.** Given three options and an open checkpoint, when the CEO asks the expert which to pick, then it declines to rank and restates the objections instead.
- AE5. **Covers R16.** Given a department with nothing it can cite against an option, when the decision is about to commit, then that department raises no objection rather than an unsourced one.
- AE6. **Covers R18, R19.** Given a raised objection dismissed unread, when the decision commits, then the dismissal and the objection are both recorded against that decision.
- AE7. **Covers R20.** Given one decision taken over an objection and another taken with none raised, when the report is folded, then the first names what was overridden and the second does not.
- AE8. **Covers R7.** Given an expert briefed twice on the same decision, when the briefings complete, then no metric has moved.
- AE9. **Covers R21, R24, R25.** Given a checkpoint with three options, when the CEO runs a comparison and closes it, then three branches ran from the same tick and the parent run's state and clock are unchanged.
- AE10. **Covers R23.** Given the same comparison run twice from the same parent tick, when both complete, then the branch results are identical.
- AE11. **Covers R27, R28.** Given a branch trajectory rendered on any surface, when the CEO reads it, then the authored-tuning marking is present; and when an expert quotes a figure from it in prose, the marking travels with the prose.
- AE12. **Covers R29.** Given a persisted log containing agent statements, when it is re-folded, then the resulting state is identical and the model stream is never opened.
- AE13. **Covers R8, R30.** Given no model key configured, when a run is played to its horizon, then it completes and every assertion that passed before this phase still passes.
- AE14. **Covers R14, R20, R21.** Given a checkpoint settled from the tray, when the report is folded, then the decision is marked as taken with no objection heard; and the CEO could still have run the comparison from that tray.
- AE15. **Covers R21, R22.** Given an early checkpoint with a later checkpoint downstream of it, when a branch runs, then it stops at that downstream checkpoint, every figure it reports names the tick it was measured at, and no downstream checkpoint was resolved inside the branch.
- AE16. **Covers R11, R17.** Given an agent statement that names a preferred option, when it is validated, then it is rejected with a reason and never reaches the CEO.
- AE17. **Covers R31.** Given a configured model call that times out, when the CEO opens a briefing, then that director answers from the scripted reply and no rejection is surfaced.
- AE18. **Covers R32.** Given an objection round in which one party never answers, when its deadline passes, then that party is recorded as unavailable to object rather than as having none, and the commit is not blocked.
- AE19. **Covers R25.** Given a comparison computed at one tick, when the parent run resolves that checkpoint's item before the CEO acts on the result, then the result is refused as stale.
- AE20. **Covers R35, R27.** Given a checkpoint with three options, when the conversation renders them, then each shows its authored effect and note carrying the authored-tuning marking.
- AE21. **Covers R4.** Given a work item authored against the accounting room, when the CEO seeks a briefing on it, then the Administration expert owns it, because that is the assignee's reporting line.
- AE22. **Covers R33.** Given a decision committed over a raised objection, when the CEO returns to that decision during the run, then the objection and the override are both readable without opening the report.
- AE23. **Covers R8, R35.** Given no model key configured, when the CEO opens a checkpoint, then each option's authored effect renders and a comparison can be run, with no expert present.

---

## Success Criteria

- A decision can be taken having consulted the expert's framing, the branch comparison and the objections, with every number on screen traceable to a source.
- The rejection path fires on a malformed agent statement, so "no figure without its source" is enforced rather than asserted.
- The same comparison rerun from the same parent tick returns identical branches.
- The override record lets the report answer "what were you warned about" for every decision taken.
- A run with no key configured passes every test that passed before this phase and still reaches each option's authored consequence and the comparison, so the no-key path is a supported topology rather than a stripped one.
- Every decision taken with the bench present records which expert-cited figures the CEO was shown before choosing, so influence is traceable inside one run rather than by comparing two runs whose checkpoint supply diverges.
- At least one recorded decision shows the expert's framing leading to a worse outcome at the measured tick than the option the CEO first leaned toward, so a bench that steers badly is observable rather than indistinguishable from one that helps.
- The office stays load-bearing on the right axis: the numbers are reachable from the tray, and the framing, the objections and the tacit line are not.
- Adding external-lookup sourcing requires only a new source kind, with no change to the citation contract.

---

## Scope Boundaries

### Deferred for later

- The mirror intake — eliciting a real org's hours, volumes and undocumented reality, and seeding each expert's memory from it. Waits for a design partner.
- Everything that follows from real employee data: the append-only log against an individual's deletion right, and what an exportable report may say about a named person. Both are already recorded as open in the platform doc and stay open.
- The self-serve company composer, where a user assembles their own departments for a decision they face. That market also needs external tools and real domain data.
- External-lookup sourcing. This phase's source kinds are the log, the branch and authored content.
- AI spend as a third company resource allocated across departments. Recorded because it is the one genuinely new mechanic this dialogue surfaced and it fits the transformation thesis directly; not scoped here.
- The three-tier memory scopes with information crossing only through events. The bench needs per-expert memory; scope-crossing is what the mirror requires.
- Specialists as agents.
- Per-tick agent invocation. Never on the table, for latency and cost.
- The report's client surface, and with it platform R53's replay-to-position, which has no implementation on either side. R20 lands in the report fold, which is server-side and testable, and R33 puts the override record on the decision surface so the objection bet is judgeable without waiting for the report.
- The gateway-to-kernel gRPC leg the coverage audit records as gap 2. Single-process stays the run path.
- Staff movement and platform R44's position validation, the remaining inherited Phase 1 holes.

Two things are explicitly **not** deferred, because this phase depends on them:

- **R41's marking**, which ships here — this phase is what makes it load-bearing.
- **The kernel-to-agents leg**, which is a different hole from the gateway leg above and is unnamed in the audit. Nothing constructs a channel anywhere under `backend/`, and the agents service registers no resolver servicer, so today no model answer has any path into the kernel — the daily domain consult is raised and abandoned at its deadline. Without this leg every requirement in this phase is unreachable.

### Outside this product's identity

- An agent that decides for the CEO. The shipped stub declines by design and this phase keeps that posture: experts brief, cite and object. A company that runs itself while the founder watches is the adjacent product, and it removes the decision this product exists to support.
- An AI advisory chat. If the expertise were reachable without crossing the floor, the office would be decoration and the walking would never have been the mechanic under judgment.
- A general-purpose agent framework. The bench serves this simulation.

---

## Dependencies / Assumptions

- The prior phase's verdict is a precondition, not an assumption. Its stated purpose was a go/no-go from an end-to-end played run, and no such run has happened, so the ask box running dry is a hypothesis rather than an observation. The bench's justification does not rest on it — the missing expertise at the decision surface is verifiable in the code — but a run costs about five wall-clock minutes at ×1, and this phase's scope should be committed only once that verdict is recorded.
- R2's proximity gate, R14's in-person condition and R20's tray marking all derive from the client-asserted in-person flag. `resolve_checkpoint` takes it as a boolean and trusts it, and platform R44's validation stays deferred, so the report's most defensible line and the office criterion are client-side guarantees this phase does not enforce server-side.
- A branch's arithmetic runs on authored constants, so a branch predicts the author. R27 and R28 are the whole mitigation until the mirror phase replaces constants with measurements.
- An expert's domain knowledge is the model's own. On invented data it cannot be checked for accuracy, only for citation.
- Model cost per run is unknown. Invocation at decisions, briefings and objections only is the mitigation, and a 20-sim-day run carries nine authored checkpoints, so the ceiling is low today. Phase 3's real work volume raises it.
- The bench sits in the directors' chairs, which assumes four directors on four load-bearing departments. A hire that roots a fifth reporting line would need a fifth expert.
- The log now contains model prompts and outputs. Under the mirror phase it would also contain real people's elicited words, which is where the deferred retention question lands.
- The mirror's central claim — pointing a real founder at where automation actually pays — stays unproven for another phase. That is the accepted cost of proving the mechanism on free ground first.

---

## Outstanding Questions

### Deferred to planning

- What the agent request carries beyond today's ids, permitted options and rules version.
- How each expert's prompt is constructed, and how department state is summarised into it.
- Where branches execute, and whether their results are cached for a re-read within one comparison.
- What threshold makes a department "materially affected" by a branch under R15.
- How the objection surface renders relative to the existing conversation panel.
- How a model-version change interacts with the rules version.
- How "a figure" is defined for R11 and R28 — any numeral, any quantity with a unit, or only values drawn from a metric or a delta. Percentages and dates a model computes from cited figures need a ruling before the validator can be written.
- Which expert briefs a checkpoint on an item whose brief spans two reporting lines. R4's single-scope rule and R15's affected-department rule pull in opposite directions there.
- Where branches execute, given that the only mechanism advancing a persisted run is the wall-clock tick loop and it shares a single store writer with every live run.

### From 2026-08-14 review

- **Ship the commitment shape and sequence the trajectory behind the domain model?** — Key Decisions (P1, product-lens, confidence 75)

  The document forbids an agent from ranking options because a recommendation converts a hand-typed constant into considered judgment, then renders a trajectory computed from those same constants with a marking as the only guard. The stronger safeguard sits on the weaker persuasion vector. Shipping only the commitment shape — runway, what the option opens or closes, whether it stays reversible — would hold one epistemic standard across the phase, at the cost of not resolving the tie the phase exists to resolve. The full-trajectory choice was made deliberately, so this is recorded rather than reversed.

- **Does R23's shared agent input need persisting at all?** — R23, AE10 (P1, adversarial and feasibility, confidence 75)

  AE10 requires a rerun of the same comparison to return identical branches, but R23's shared input is drawn during the comparison and R24 discards the branches, so nothing holds the drawn value and AE10 is unreachable as written. Appending it to the parent's log keyed to the comparison's fork sequence would fix that. Two reviewers separately doubt the rule is needed at all: `rng.draw` is a pure function of run seed, tick, purpose and entity with no generator state, so branches already reproduce the parent's stochastic draws, and an unattended branch invokes no agent. Whether R23 protects against anything comes before how to persist its input. Related: reusing one statement across branches can put R23 in tension with R10, since a figure cited to an event in one branch has no such event in a sibling's log.

- **What order do the four subsystems land in?** — Requirements (P1, scope-guardian, confidence 75)

  The bench, the citation contract, the objection mechanism and the branch comparison form one delivery unit with no internal sequence, so a slip in any one puts all thirty-six requirements at risk rather than a scoped subset. The document specifies a fallback for its own core dependency in R8 and R31 but gives no equivalent signal across the subsystems. The comparison is the natural first split, since F2 needs no expert — and it is also the least ready of the four.

---

## Sources / Research

| Location | What it holds |
|---|---|
| `backend/packages/simcore/items.py:41` | `Option` — label, detail, effect, note. The deltas that exist and are never shown |
| `frontend/src/ui/conversation-model.ts:223` | `DecisionOption` — label and detail only. Where the deltas stop |
| `backend/services/agents/stub.py` | The shipped resolver, declining by default; its out-of-bounds mode is the rejection behaviour R12 depends on |
| `backend/packages/simcore/pending.py` | Sim-tick deadlines, per-item and per-run caps, answer validation, and the replay-versus-seed determinism distinction |
| `backend/packages/contracts/grpc/agents_pb2.py` | The `Resolution` and `Decision` messages, and the resolver kinds including `AGENT` |
| `backend/packages/simcore/people.py:51` | The four directors, one per load-bearing department |
| `backend/packages/simcore/people.py:278` | `VOICE` — the scripted replies, four per person, named as where the producer changes |
| `backend/services/kernel/store.py:387` | `fork_run`, which R21 narrows to a per-option comparison. Its child id derives from parent and sequence, which R24's identity rule exists to disambiguate |
| `backend/packages/simcore/step.py` | `_block`, which stalls an item and its assignee at a checkpoint — why a branch settles nothing after its fork tick |
| `backend/services/kernel/loop.py` | The wall-clock tick loop, the only mechanism that advances a persisted run |
| `docs/design/art-direction.html` | The reserved amber, and the colour already spent on the department stripe and the load bar |
| `backend/services/report/fold.py` | The report fold, which already carries the rules version |
| `docs/2026-08-14-phase-1-coverage-audit.md` | The inherited holes, and R41's marking being on no rendered surface |
| `docs/brainstorms/2026-08-12-company-os-platform-requirements.md` | Origin requirements R3, R8, R37, R40, R41, R52, R53, R61, and the roadmap this phase is drawn from |
| `docs/brainstorms/2026-08-14-playable-loop-requirements.md` | The proximity rule, the in-person pricing, and the ask box this phase leaves alone |

# Authoring a company

A scenario file is a company: who works there, who reports to whom, what work is in flight, and
what each person says when the CEO walks over and asks. It is TOML, it lives in this directory,
and adding one needs no code change — drop `acme.toml` next to `default.toml` and a run can be
created against `acme`.

`default.toml` is the shipped company and the best thing to read alongside this document. Every
rule below is one you can see obeyed there. `ashcroft.toml` is the second, and it is here to be
the proof rather than a variation: nothing in the code knows it exists.

## Choosing one

By name, at run creation, and never by path:

```bash
curl -s http://127.0.0.1:8800/scenarios
curl -s -X POST http://127.0.0.1:8800/runs \
  -H 'content-type: application/json' -d '{"scenario":"ashcroft"}'
```

The client's start screen lists the same thing — title, summary and size — so a file dropped in
this directory is on offer the next time that page loads. A name is checked against a closed
character set *before the filesystem is touched*, so it cannot contain a path separator, a dot or
a drive letter; a file that will not load is listed with its refusal rather than hidden, because
the person reading that list is usually the person who just wrote the file. Omitting the name is
how you ask for `default`.

## What is yours to author, and what the rules fix

The floor is not yours. The generator lays out **eight rooms** in two bands around a corridor,
and it is the same eight for every scenario:

| room | seats | what it is |
|---|---|---|
| `exec` | 0 | the executive office; a run starts on the corridor tile outside its door |
| `sales` | 3 | an open-plan office |
| `accounting` | 1 | an office |
| `meeting` | 0 | where cross-department work goes for its check-in |
| `hr` | 2 | an office |
| `support` | 2 | an office |
| `admin` | 2 | an office |
| `lounge` | 0 | furniture |

The **four reporting lines** are not yours either: `sales`, `admin`, `support` and `hr`. A
scenario staffs those four. It cannot add a fifth, drop one, or rename one, and it cannot add a
room — `[[room]]` is not a key this format has, so a file that tries is refused.

What *is* yours is everything that makes a company a particular company: the people, their
titles and desks and reporting lines, the responsibilities and tools they are described as
having, what they say, the work that exists, where each piece of work stops for a decision, what
the CEO's options are at that stop, and what each option costs.

## The two questions a desk answers

A person has a `room` and a `manager`, and those are two different questions on purpose.

`room` is where they sit — which desk their sprite occupies, and therefore who the CEO happens to
walk past. `manager` is whose reporting line they are in — who can delegate to them, whose
capacity their work draws on, and whose department a decision's consequences land in.

On the shipped roster they disagree for exactly one person. Priya Raman sits in `accounting` and
reports to the Administration director. That is not an oversight to tidy up; it is the reason the
two fields exist. A real org chart has people whose desk and whose manager are different answers,
and a simulation that collapsed them would make the whole of "walk over and talk to someone" a
function of the org chart rather than of the floor.

## Order matters, and the hash says so

People are a list, not a table keyed by id, and the order is part of what the scenario *is*.

Two people may claim the same `seat_slot` in the same room. When they do, the earlier one in the
file gets the desk and the later one takes the next free slot. So reordering two people can move
someone's desk — which means roster order has to be covered by the scenario's identity, and it
is. Reordering two `[[person]]` blocks changes the content hash even when nothing else about the
file changed.

Item order matters for a quieter reason: it is the order the work graph reaches the client in.

Nothing else about the file's *shape* is significant. Comments, blank lines, the order of keys
inside a table, whether you write `effect = { visibility = 6 }` on one line or as an
`[item.effect]` block — none of it changes the hash. The hash is taken over the validated
structure with every default filled in, so a field you omitted is in the hash with its shipped
value.

## A scenario's identity, and why editing one ends a run

When a run is created, the genesis event records the scenario's id, the hash of its content, and
the version of the hashing itself. Three places check that record against the file on disk: a
fold from the start of the log, a snapshot restore, and a fold resumed from a snapshot.

If the file has changed, they refuse, and the refusal names what moved — which person, which
item, or that the roster order changed. It is not a warning: the run's numbers were produced by a
company that no longer exists in that form, and replaying its log against the edited company
would reproduce neither.

Each of the three says which one of them noticed, because otherwise all three produce the same
sentence and you cannot tell what you were doing when it fired:

```
Refused at the from-zero fold, replaying genesis
Refused at a snapshot restore
Refused at a fold resumed from a snapshot, which skips genesis
```

The first two hold a recorded id and hash and are given the roster and catalog the genesis event
carries, so they can name the person or the item that moved. The third holds the whole company the
state was built with, so it can also name a **reordering** — which two hashes cannot express, and
which changes who wins a contested desk.

**So editing a scenario invalidates every existing run written against it.** That is the deal,
and it is deliberate: runs here are local and disposable, and a migration path for authored
content would be a large machine for a small benefit. In practice it means finishing a run before
you edit its company, or accepting that you are starting a new one. The refusal says as much and
names the file to restore if you want the old run back.

A run created *before* scenario files existed records no identity at all. It is refused the same
way, by the same guard, with its own remedy — "start a new run" — and the kernel logs the refusal
per run and starts anyway. One unreplayable run does not stop the clock for the others.

## The shape of the file

Top level:

```toml
schema  = 1                    # the format version this file is written against
id      = "default"            # must match the filename without .toml
title   = "Northwind Components"
summary = "..."                # optional, one line, shown where a scenario is chosen
```

Then four `[[department]]`, at least four `[[person]]`, at least one `[[item]]`, and any number of
`[[seeded_assignment]]`.

### `[[department]]` — one per reporting line

```toml
[[department]]
id = "admin"                   # one of sales, admin, support, hr
director = "dir_admin"         # a person, who must sit in this department's room
draw_hours_per_month = 120
```

`draw_hours_per_month` is the line's recurring baseline workload: the work that exists whether or
not the CEO assigns anything. It is in the same hours-per-month unit the Manual work metric
displays, and the sum of the four draws *is* that metric's starting value — so the number a
decision claims to save and the number on the HUD cannot drift apart.

Size a draw against what its own decisions can remove. A line whose authored decisions can take
86 hours a month off it wants a draw comfortably above 86, or Manual work bottoms out and stops
responding at exactly the moment the automation lands.

A department's director must sit in the department's room. A line is *named* by that room — a new
hire into a line is seated there — so the two cannot be allowed to disagree.

### `[[person]]`

```toml
[[person]]
id = "stf_ap"                  # lowercase, digits, underscores
name = "Priya Raman"
initials = "PR"                # what the sprite and the conversation header show
title = "Accounts Payable"
room = "accounting"            # where they sit
manager = "dir_admin"          # whose line they are in; omit for a director
rank = "staff"                 # "staff", or "director" for the head of a line
seat_slot = 0                  # which desk in the room, counting from 0
responsibility = "Reconciles every invoice against its paper copy, and releases the payment run."
tools = ["Ledger", "Invoice scanner", "Payment run"]
mcp_servers = ["ledger", "document-store"]
skills = ["invoice-matching", "payment-release", "supplier-reconciliation"]
deflection = "That is above my desk. What I can tell you is ..."

[person.voice]
why = "Invoices arrive on paper and as PDF, so I reconcile both before I post anything. ..."
exception = "The last three days of the month I release payment before the paper arrives. ..."
axis = "Anything over $10K needs the manager approval. Nobody has revisited that line in ..."
bottleneck = "Matching. Seven minutes an invoice, four hundred invoices a month."
```

`rank` and the `[[department]]` list have to agree: a person is `"director"` exactly when a
department names them as its director. A director has no `manager`. Everyone else's `manager`
names one of the four directors — assignment, delegation and capacity all follow the reporting
line, so a line has to end somewhere.

`seat_slot` counts desks in the room from zero, and it has to be inside the room's seat count.
A room cannot hold more people than it has desks; the roster has to fit the floor, because the
floor is fixed.

### The four questions

`[person.voice]` holds one line per question, and all four are required. They are the four things
worth asking anyone:

| slot | the question | what it costs |
|---|---|---|
| `why` | why the work is done this way | reveals undocumented knowledge — pays Visibility, once per person |
| `exception` | what they do that the process does not describe | the same |
| `axis` | what is theirs to decide, and where their authority ends | the same |
| `bottleneck` | where the time actually goes | a number they would have given you anyway — pays nothing |

`deflection` is what they say when the CEO's typed question matched none of the four. One per
person rather than one shared line: a miss is common, and a generic "not in the script" would be
most of what a player hears from the mechanic. Said in their own voice, a miss still points at
what they *can* answer.

Write all five in the person's own register. This is the content a model-backed director later
speaks over, and it is what a scripted specialist says for the whole life of the product.

### `[[item]]` — the work

```toml
[[item]]
id = "wi_ap_auto"
title = "Automate invoice matching"
brief = "Four of the twelve mapped steps eat 60% of the time. ..."
room = "accounting"            # where the work is understood to sit
want = "stf_ap"                # who does it; their line carries its load
effort_hours = 24
friction = "Running OCR accuracy tests. The quote came back."
output_title = "Invoice matching automation plan"
output_kind = "Automation candidate"
unlocks = []                   # items this one opens
visit_meeting = false          # cross-department work stops for a meeting at 20%
final = false                  # at most one item is true: the work the run is aimed at
effect = { visibility = 4 }    # applied when the item completes
requires = { visibility = 24, items = ["wi_ap_map"] }
```

`want` names the person the work is for. Their **reporting line** — not their room — is the
department whose capacity the work draws on and whose recurring draw a decision moves. That is
derived rather than authored, so it cannot be authored inconsistently with the person.

`requires` gates the item: an optional Visibility threshold, and an optional list of items that
have to finish first. `unlocks` is the same edge seen from the other end, and the two have to
agree — the kernel gates on `requires` while the client's dependency graph draws `unlocks`, so an
edge in one and not the other is either a line on screen that gates nothing or a gate with no
line. Prerequisites must not form a cycle.

`effect` is applied when the item completes, and it may only name metrics. The recurring-draw key
belongs on an option, not here.

Two figures in authored copy are generated rather than typed. `{hours}`, `{hours_word}` and
`{Hours_word}` in a `brief` or a `prompt` are filled from the largest draw reduction the
checkpoint's own options offer, so the sentence and the number cannot drift apart.

### `[[item.checkpoint]]` — where the work stops

```toml
[[item.checkpoint]]
at_percent = 60                # percent of the item's effort
kind = "approval"              # "info", "approval" or "decision"
label = "Approval"
prompt = "The OCR matching quote is in: $120K up front, ... I need your approval."
tacit = "...70% of the matching is the same three suppliers. That is not in the quote."
```

A checkpoint is a point where work stops and waits for the CEO. `at_percent` is percent of the
item's effort — never a fraction, because the comparison that decides whether someone stops for
you is done in integers. Checkpoints ascend and cannot repeat a percentage.

`tacit` is the line that surfaces **only** if the CEO takes the decision in person. It is the
whole argument of the product, so it is content rather than flavour: write something that changes
what the obvious answer is. Priya's line about releasing payment before the paper arrives, or the
line about referrals skipping the requirements — each one makes the option that reads best on
paper the wrong one. It never reaches the client until a resolution has earned it.

Nine checkpoints across eight items is what the shipped company offers, and that is the real
bound on a useful run: baseline load regenerates forever, so a horizon longer than the decision
supply is burn with nothing left to decide.

### `[[item.checkpoint.option]]` — what the CEO can choose

```toml
[[item.checkpoint.option]]
label = "Approve a limited rollout"
detail = "Top ten suppliers only. A third of the cost, a third of the benefit."
note = "Limited rollout to the top ten suppliers"
effect = { cash = -40, draw = -15, morale = 1 }
```

Between two and six options. Fewer than two is a notification wearing a decision's clothes; more
than six is a decision the comparison surface has to refuse to open, because it runs one branch
per option and is bounded there.

`note` is the sentence the deliverable's provenance keeps — what survives the run and appears in
the report. `label` is what the CEO clicks, and two options on one checkpoint cannot share one:
an answer arriving from a director names the option by its label.

`effect` is integer deltas. The metrics are `cash` (in $K), `manualHours`, `leadTime` (in days),
`morale` and `visibility`. `draw` is not a metric — it is a change to the owning department's
recurring monthly draw, in hours per month, and `manualHours` is derived from the sum of the
draws. Use `draw` for automation that removes recurring work; the metric follows.

Give every option a real cost. A checkpoint whose options are ranked by one number is arithmetic,
not a decision.

### `[[seeded_assignment]]` — what the company was already doing

```toml
[[seeded_assignment]]
item = "wi_hiring"
person = "dir_hr"
done_percent = 45
```

Work already in flight when the CEO walks in, so the first thing on screen is a person holding an
unsettled decision rather than an empty floor.

`done_percent` is percent of the item's own effort — the same unit checkpoints are authored in,
which is the point. Author a seed *at* a checkpoint's own percent and the opening stop is
structural: the first tick raises that checkpoint, so the beam is lit on the first frame the
client draws. The shipped seed sits at 45% of `wi_hiring`, which is `wi_hiring`'s own first
checkpoint.

A seeded item cannot be one that is gated behind a prerequisite or a Visibility threshold: seeded
work is work the company was already doing, so it has to be work the company could have started.

A seed also cannot put anybody away from their desk, and there is no way to ask for one that would:
a seed names an item and a person, and a person starts at the desk their `room` and `seat_slot`
give them. Genesis carries no movement event — the fold rebuilds day zero by *calling* the same
function rather than by replaying events, so a walk produced there would regenerate nowhere and
strict replay would diverge on it. That is why the seed is two ids and a percent and not a position.

## Tools, MCP servers and skills are description

Nothing in this system executes anything a scenario names. `tools`, `mcp_servers` and `skills`
say what a person is *understood* to have: they shape what a director claims it could do when it
briefs the CEO, and they are what the report counts when it says what is worth automating. There
is no dispatch table behind them, and clicking one in the client does nothing by design.

They travel to the client on the genesis event, alongside each person's `responsibility`, name,
title, room and reporting line — so a conversation surface can show who it is talking to and what
they are understood to work with, and an exported run stays readable without the kernel running.
Exactly two modules in the backend read these fields: the loader that validates them and the
projection that puts them on genesis. A test asserts there is no third, which is what makes
"description" structural rather than a promise.

Authored text is also never an instruction. When a director is model-backed, everything from this
file enters the prompt as delimited data, and the guards on what comes back apply regardless of
what the text asked for. The loader is the first half of that: every string is length-capped and
refuses control and format characters, so a scenario cannot smuggle a line break, a right-to-left
override or a zero-width joiner into a prompt — text that changes what a reviewer sees without
changing what a model reads.

## Refusals

A scenario loads whole or not at all. There is no partial load: validation runs to the end of the
file, collects every problem it can find, and then either builds the company or refuses with all
of them. Nothing is constructed until it passes.

Each line of a refusal names the file, the line, and the entry:

```
scenario 'default' is refused and nothing was loaded — a scenario loads whole or not at all,
so no partial company exists.
  scenarios/default.toml:136: person[4] 'stf_ap': manager is 'dir_nobody', who does not head
  one of the four reporting lines (dir_admin, dir_cs, dir_hr, dir_sales). ...
```

The things most often got wrong:

- **An unknown key.** The key set is closed, per table. This is not pedantry: scenario files
  arrive by pull request, and a loader that ignored keys it did not recognise would let an
  `api_key` or a `base_url` sit in one — present, reviewed, and read by nothing until something
  started reading it.
- **A room or a department that does not exist.** Eight rooms, four lines, both fixed.
- **`rank` disagreeing with the `[[department]]` list.**
- **A prerequisite naming an item the file does not declare**, or `unlocks` and `requires`
  describing an edge only one of them knows about.
- **A room holding more people than it has desks.**
- **Text over its cap, or carrying a control character.**
- **An `id` that does not match the filename.** The id is what a run records and reloads by.

## Adding a scenario

1. Copy `default.toml` to `<name>.toml`, and set `id` to `<name>`. The name is lowercase letters,
   digits, dashes and underscores — it is resolved inside this directory and never as a path.
2. Change the company. Keep the four departments and the eight rooms.
3. Load it: `cd backend && uv run python -c "from simcore import scenario;
   print(scenario.load('<name>').title)"`. A refusal tells you the line.
4. Create a run against it. Existing runs on other scenarios are unaffected — a run's scenario is
   recorded per run, and one process ticks runs on different companies at once.

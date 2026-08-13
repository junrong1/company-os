# Company OS — a company simulator

A pixel-art simulator of a company. You are the CEO: you assign work, employees
move along the real reporting lines to do it, and they stop at the point where
only you can decide. Walking over to ask them beats clearing it from the tray,
because in person they tell you things the tray never shows.

Built as a demo surface for the `100_avater` idea (virtual office → hearing →
work-knowledge graph), but it runs standalone.

---

## Run it

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

Not real — deliberately, and stated on screen:

1. **Dialogue is scripted.** Each person has four canned answers (why /
   exceptions / who decides / bottleneck), matched on keywords. No LLM.
2. **Nothing persists.** No backend, no database, no accounts, no multiplayer.
   Reload and it is Day 1 again.
3. **The company is invented.** Halstead Industrial, its ten people and its eight
   work items are plausible sample data, not a real customer's org.

---

## Layout

```
company-os.html      the whole application, one self-contained file
test/harness.js      29 checks driving the real simulation code
test/sprites.js      validates every pixel-art grid
serve.sh             optional local HTTP server
```

Inside `company-os.html`:

| Lines | Contents |
|---|---|
| 4–814 | `<style>` — design tokens, layout, components |
| 815–951 | HTML — page skeleton and the intro dialog |
| 952– | `<script>` — numbered sections, see below |

The script is divided into numbered sections: floor planner, people, work items,
state, metrics, assignment, simulation, pixel art, drawing, panels, input, loop.

---

## Tests

```bash
node test/sprites.js     # every sprite row the right width, palette clean
node test/harness.js     # the full loop: assign → stall → decide → deliverable
```

`harness.js` runs the page's real JavaScript against a small DOM stub, so it
tests the shipped code rather than a copy. It asserts the things that are easy to
break by accident: that work does **not** progress while a decision is pending,
that an in-person decision records tacit knowledge and a tray decision does not,
that deliverables carry provenance, that every desk is reachable from the spawn
point, and that no two people are assigned the same chair.

Both suites exit non-zero on failure, so they work in a pre-commit hook or CI.

---

## Changing the company

Three data structures near the top of the script hold everything:

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

## Making it a product

In rough order of value:

1. Replace the scripted `VOICE` table with the `hearing` API so employees
   actually converse.
2. Persist the run. Simulation state is one plain object (`S`), so save/load is
   a small endpoint.
3. Import a real org chart instead of the sample roster.
# company-os

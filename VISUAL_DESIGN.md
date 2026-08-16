# Visual Design: Company OS

**Concept-Derived Visual Tags**: #render-crisp-pixel-clusters, #geometry-compact-big-head, #composition-people-first-daylight

## 1. Visual Concept

**Daylight startup, living people.** Company OS is a bright contemporary workplace simulation, not a dark command console. The office world and its people lead the composition; metrics explain their situation without replacing them.

The visual system combines a warm, inhabited pixel world with disciplined product chrome. Original character and environment art may use the warmth and readability of modern life-sim games as a quality bar, but must not reproduce third-party characters, costumes, props, rooms, or layouts.

## 2. Color Palette

| Role | Color | Hex | Usage |
| :--- | :--- | :--- | :--- |
| Product ground | Ivory / moon white | `#fffef8` / `#eef7f2` | Main surfaces, daylight floor light, quiet character backdrops |
| Primary action | Sky blue | `#1677b3` | Interface action, selection, focus — chrome only |
| Player | Fresh teal | `#57c3c2` | CEO clothing signal, primary cool focus — office only |
| Healthy work | Bamboo green | `#1ba784` | Accounting identity, positive progress, healthy state |
| Sales | Flower blue | `#2376b7` | Sales room and clothing accents |
| Administration | Whale gray | `#475164` | Administration structure and clothing accents |
| Support | Warm clay | `#aa6a4c` | Support room and clothing accents |
| People | Muted violet | `#74759b` | People-team room and clothing accents |
| Waiting decision | Attention amber | `#f2c46b` | Reserved exclusively for the one person waiting on the CEO |
| Outline | Dove blue | `#253147` | Character outlines and strong structural edges |

Attention amber is semantic, not decorative. Do not use it for ordinary clothes, floors, status bars, furniture, authored-tuning markers, or ambient highlights. It also never appears alone: against ivory it measures 1.6:1, so it is always drawn inside a dove-blue edge that carries the contrast its fill cannot.

**The cool accent divides by surface.** The Product Contract gives sky blue to "primary action and player emphasis" and this document gives the CEO fresh teal; both are right about their own surface. Fresh teal measures 2.1:1 against ivory and cannot carry a label, and a sky-blue CEO would stand beside the sales room in flower blue and read as a sales hire. So the interface's strongest cool thing is the action and the office's is the player, and neither surface carries two.

## 3. Character Rendering Specifications

- Production map canvas: transparent `48×64` PNG.
- Body grammar: compact adult at approximately 3.5 heads tall, readable head and hair mass, short limbs, planted feet, arms close to the torso.
- View: slightly elevated three-quarter game-map view. Directional animation must preserve apparent head size, outfit silhouette, and role accent.
- Pixel grammar: crisp clusters, deliberate staircase diagonals, no filtered scaling, no sub-pixel blur, no noisy single-pixel confetti.
- Shading: two or three value groups per material. Reserve the darkest dove-blue family for readable separation, not a heavy all-around black sticker outline.
- Identity: distinguish people through face/skin value, hair shape, outfit silhouette, and at most one restrained accessory. Department color is a supporting signal, not a uniform.
- Runtime variation: each named identity has A/B/C appearance candidates. A new playthrough may choose one candidate from the existing run seed; that choice stays stable within the run and has no simulation effect.
- Background coworkers: use the same proportions, outline, camera angle, and palette behavior with less authored detail.

## 4. Background & Environment

Use four depth bands:

1. Daylight shell: pale exterior ground, windows, restrained structural walls.
2. Work zones: light floors with department color carried by rugs, partitions, trim, and small furniture details rather than large saturated slabs.
3. People and interactive objects: highest local edge contrast and clearest silhouettes.
4. Product chrome and feedback: clean overlays that never darken the full office into a vignette.

Plants, rugs, art, warm timber, workstations, and collaboration areas make the office feel inhabited. Avoid fortress walls, dungeon corridors, medieval props, torch-like lighting, and undifferentiated dark floor masses.

## 5. Feedback Effects

| Event | Visual response | Tag reference |
| :--- | :--- | :--- |
| A person needs a decision | Narrow reserved-amber attention beam or pulse anchored to that person | #composition-people-first-daylight |
| Player movement | Two-frame planted walk with a subtle vertical weight shift; return to matching directional idle | #render-crisp-pixel-clusters |
| Conversation becomes available | Short cool-teal proximity ring or speech motion, without amber | #geometry-compact-big-head |
| Work completes | Restrained bamboo-green lift and a few clustered pixels near the affected person or station | #render-crisp-pixel-clusters |
| Load worsens | Existing non-amber load ramp shifts toward low-saturation strain and then red | #composition-people-first-daylight |

## 6. Relationship with Visual Tags

- `#render-crisp-pixel-clusters` sets the native-resolution drawing and scaling rules for characters, props, and feedback.
- `#geometry-compact-big-head` keeps faces and hair readable without turning adult employees into baby-like chibi figures.
- `#composition-people-first-daylight` makes people the first scan target and keeps dashboards, architecture, and state overlays subordinate.

## 7. AI-Generated Look Suppression Rules

### 7.1 Visual Hierarchy Rules

- Protagonist: The CEO is the strongest cool focal point through fresh teal, movement, and stable screen presence.
- Threat: There is no combat threat. Operational strain uses the load ramp; the single unresolved decision uses reserved amber.
- Reward: Completed work uses bamboo green and small local motion near the person or station responsible.
- 2-second recognition check: A new viewer must find the player, the one waiting person, and the active department without reading a label.

### 7.2 Limits on Familiar Template Symbols

- Adopted familiar elements (max 2): Pixel speech bubble for available conversation; compact attention beam for the one waiting decision.
- Replaced unique element: Generic dashboard alert badges are replaced by a person-anchored amber signal inside the office world.

### 7.3 UI-Independent Feedback

| Event | Non-UI visual response | Intensity (Low/Med/High) |
| :---- | :--------------------- | :----------------------- |
| Score | Completed work produces a bamboo-green lift at the responsible person or station | Low |
| Damage | Worsening operational strain shifts the affected zone through the non-amber load ramp | Med |
| Near miss | An approaching decision deadline tightens the existing person-anchored pulse without adding a second signal | Med |

### 7.4 Composition and Gaze Guidance

- Initial focal point: The player and nearby people inside the daylight office.
- Visual flow: Player → waiting person → their department → supporting company pulse.
- Anti-center-clutter implementation: Keep the walkable center readable; place dense furniture, metrics, and decorative clusters at room edges and in bounded zones.

## 8. Asset Handoff

- Casting board: `docs/assets/company-os-visual-redesign/roster-casting-board.png`
- Candidate sprites: `docs/assets/company-os-visual-redesign/roster-characters/<person-id>-<a|b|c>.png`
- MVP identities: `you`, `dir_sales`, `stf_order`, `stf_field`, `dir_admin`, `stf_ap`, `stf_buyer`, `dir_cs`, `stf_cs`, `dir_hr`, `stf_rec`.
- The candidate art is the casting board and the review target, not the shipped frames. What ships is a **layered rig**: one authored body of three views by four frames, wearing hair, an outfit and at most one accessory, resolved against six colours read out of that person's candidate by `frontend/scripts/palette-from-candidate.mjs`.
- Art is authored as PNG in `frontend/art/` and committed as ASCII grids in `frontend/src/render/cast/`, converted by `frontend/scripts/grid-from-png.mjs`. The conversion is exact or it fails naming the pixel; a near-miss is never snapped to the nearest slot. Rebuild with `npm run cast`.
- Keep raw generation sources and intermediate cutouts outside the shipped bundle. `frontend/art/` is committed but excluded from the image and never bundled.
- Review artifacts are produced by `npm run verify` into `docs/assets/company-os-visual-redesign/verification/`: the cast beside its candidates, and the office at three widths.

### Acceptance checks

- Every sprite is exactly `48×64`, has alpha, and keeps all four corners transparent.
- Every directional frame remains recognizable as the chosen A/B/C identity at native scale.
- Every frame plants its feet on row 63 — the row the tile anchor and the depth sort both read.
- Nearest-neighbor enlargement stays crisp at every supported zoom.
- No ordinary asset uses `#f2c46b` as a clothing or room signal.
- Named identities remain stable within a run; cosmetic appearance never changes simulation state.
- No two of the eleven identities share a silhouette. Checked against the composed outline, not against the feature indices behind it — most accessories draw inside the body, and two hair shapes can occupy the same pixels.

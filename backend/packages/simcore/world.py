"""The floor: rooms, desks, furniture and pathfinding.

Ported from script section 1 of `company-os.html` (`:973` room plan, `planFloor`,
`walkable`, `path`).

One change of ownership matters. In the prototype the floorplan is generated to fit
the browser window, and re-planned on every resize — geometry is a function of the
viewport. Here the server computes it **once at genesis and records it**, because a
floorplan derived from a viewport is not replayable: two clients with different window
sizes would fold the same log to different desks, different paths and different
arrival ticks. The generator is ported faithfully; only the trigger moves.

Everything here is integers. Rooms, desks and grid cells were already integral in the
prototype; the parts that were not — positions during a walk — are derived from the
tick index in `simcore.time` rather than accumulated as floats.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: One tile is 16 logical pixels, drawn at integer zoom with nearest-neighbour
#: scaling. The kernel needs the constant only so the client and server agree on
#: what a tile means.
TILE = 16

WALL = 0
FLOOR = 1
SOLID = 2
CORRIDOR = 3

#: The smallest grid the generator supports, from the prototype's `Math.max` floors.
MIN_COLS = 26
MIN_ROWS = 16

#: The default grid a run is created with.
#:
#: 31x18, not the prototype's initial `let COLS = 30, ROWS = 18` — that pair is
#: overwritten by `sizeCanvas()` before anything runs. 31x18 is what the floor
#: actually generates at for the assertion harness's 1000x600 stage at zoom 2, and
#: it is the grid whose desk layout the ported parity suite asserts against. A run
#: may record any grid at genesis; this is only the default.
DEFAULT_COLS = 31
DEFAULT_ROWS = 18


@dataclass(frozen=True, slots=True)
class RoomSpec:
    """A room before it has a position. Eight of these, in two bands."""

    id: str
    name: str
    band: str  # "top" | "bot"
    floor: str
    kind: str  # "office" | "meeting" | "lounge"
    seats: int = 0
    desk_rows: int = 3


#: Always eight rooms in two bands around a central corridor; only their size
#: changes with the grid. Order is load-bearing: rooms are sorted back into it after
#: layout so that indices are stable across grid sizes.
ROOM_PLAN: tuple[RoomSpec, ...] = (
    RoomSpec("exec", "Executive Office", "top", "wood", "office", seats=0, desk_rows=1),
    RoomSpec("sales", "Sales", "top", "blue", "office", seats=3),
    RoomSpec("accounting", "Accounting", "top", "green", "office", seats=1),
    RoomSpec("meeting", "Meeting Room", "top", "slate", "meeting"),
    RoomSpec("hr", "People", "bot", "violet", "office", seats=2),
    RoomSpec("support", "Customer Support", "bot", "amber", "office", seats=2),
    RoomSpec("admin", "Administration", "bot", "slate", "office", seats=2),
    RoomSpec("lounge", "Lounge", "bot", "wood", "lounge"),
)

ROOM_ORDER: dict[str, int] = {spec.id: index for index, spec in enumerate(ROOM_PLAN)}


@dataclass(slots=True)
class Room:
    """A room with a position on the grid."""

    id: str
    name: str
    band: str
    floor: str
    kind: str
    seats: int
    desk_rows: int
    x1: int
    x2: int
    y1: int
    y2: int
    door: tuple[int, int]
    seat_y: int = 0
    #: Desk slots, in the order the generator produced them. `slot` on a person
    #: indexes into this list.
    slots: list[tuple[int, int]] = field(default_factory=list)
    #: Where someone stands for a cross-department meeting. Meeting room only.
    visit: tuple[int, int] | None = None


@dataclass(slots=True)
class Furniture:
    x: int
    y: int
    sprite: str
    solid: bool


@dataclass(slots=True)
class Floor:
    """The whole generated floor. Recorded at genesis and immutable for a run."""

    cols: int
    rows: int
    rooms: list[Room]
    hall: tuple[int, int, int, int]  # x1, y1, x2, y2
    furniture: list[Furniture]
    lamps: list[tuple[int, int]]
    windows: list[tuple[int, int]]
    grid: list[list[int]]
    spawn: tuple[int, int]

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise KeyError(f"no room {room_id!r}")

    def to_state(self) -> dict[str, Any]:
        """Canonical, integer-only projection for the state hash and the log.

        Sprite names and room labels are strings, which canonical encoding allows;
        nothing here is a float and nothing is a set.
        """
        return {
            "cols": self.cols,
            "rows": self.rows,
            "hall": list(self.hall),
            "spawn": list(self.spawn),
            "rooms": [
                {
                    "id": room.id,
                    "kind": room.kind,
                    "band": room.band,
                    "box": [room.x1, room.y1, room.x2, room.y2],
                    "door": list(room.door),
                    "seat_y": room.seat_y,
                    "slots": [list(slot) for slot in room.slots],
                    "visit": list(room.visit) if room.visit else None,
                }
                for room in self.rooms
            ],
            "furniture": [
                [f.x, f.y, f.sprite, 1 if f.solid else 0] for f in self.furniture
            ],
            "lamps": [list(lamp) for lamp in self.lamps],
            "windows": [list(window) for window in self.windows],
        }


def plan_floor(cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> Floor:
    """Generate the whole floor for a grid of `cols` x `rows`.

    A direct port of the prototype's `planFloor`. Called once, at genesis.
    """
    cols = max(MIN_COLS, cols)
    rows = max(MIN_ROWS, rows)

    hall_height = 3 if rows >= 23 else 2
    top_height = (rows - 4 - hall_height) // 2
    bottom_height = rows - 4 - hall_height - top_height

    top_y1 = 1
    top_y2 = top_y1 + top_height - 1
    hall_y1 = top_y2 + 2
    hall_y2 = hall_y1 + hall_height - 1
    bottom_y1 = hall_y2 + 2
    bottom_y2 = bottom_y1 + bottom_height - 1

    hall = (1, hall_y1, cols - 2, hall_y2)

    # Four rooms per band, separated by single wall columns. The remainder is spread
    # across the leftmost rooms rather than dropped, so the band fills the grid.
    usable = cols - 2 - 3
    base = usable // 4
    extra = usable - base * 4
    widths = [base + (1 if index < extra else 0) for index in range(4)]

    rooms: list[Room] = []
    for band in ("top", "bot"):
        x = 1
        for index, spec in enumerate(spec for spec in ROOM_PLAN if spec.band == band):
            width = widths[index]
            y1 = top_y1 if band == "top" else bottom_y1
            y2 = top_y2 if band == "top" else bottom_y2
            rooms.append(
                Room(
                    id=spec.id,
                    name=spec.name,
                    band=spec.band,
                    floor=spec.floor,
                    kind=spec.kind,
                    seats=spec.seats,
                    desk_rows=spec.desk_rows,
                    x1=x,
                    x2=x + width - 1,
                    y1=y1,
                    y2=y2,
                    door=(
                        x + width // 2,
                        top_y2 + 1 if band == "top" else bottom_y1 - 1,
                    ),
                )
            )
            x += width + 1

    rooms.sort(key=lambda room: ROOM_ORDER[room.id])

    # ---- grid ----
    grid = [[WALL] * cols for _ in range(rows)]

    def fill(box: tuple[int, int, int, int], value: int) -> None:
        x1, y1, x2, y2 = box
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                grid[y][x] = value

    for room in rooms:
        fill((room.x1, room.y1, room.x2, room.y2), FLOOR)
        grid[room.door[1]][room.door[0]] = FLOOR
    fill(hall, CORRIDOR)

    # ---- furniture, derived from each room's own box ----
    furniture: list[Furniture] = []

    def add(x: int, y: int, sprite: str, solid: bool) -> None:
        furniture.append(Furniture(x=x, y=y, sprite=sprite, solid=solid))

    for room in rooms:
        # Desks sit one row nearer the viewer than the seat, so people face us.
        seat_y = room.y1 + 2
        room.seat_y = seat_y

        if room.kind == "office":
            # Desk spacing adapts: a narrow room at 3-tile spacing cannot seat
            # everyone who reports into it, and two people would land on one chair.
            # Tighten the spacing, then add a second row, until they all fit.
            need = room.seats
            start = room.x1 + 1
            end = max(room.x1 + 1, room.x2 - 1)
            capacity_at = lambda stride: (end - start) // stride + 1  # noqa: E731
            stride = 3 if capacity_at(3) >= need else 2

            row_ys = [seat_y]
            # A tall room gets further rows, so the floor reads like a real
            # open-plan office rather than one lonely row against a wall.
            sy = seat_y + 3
            while sy + 1 <= room.y2 and len(row_ys) < room.desk_rows:
                row_ys.append(sy)
                sy += 3

            for row_y in row_ys:
                for x in range(start, end + 1, stride):
                    room.slots.append((x, row_y))
                    add(x, row_y + 1, "desk", True)
                    add(x, row_y, "chair", False)

            if room.x2 - room.x1 >= 4:
                add(room.x2, room.y2, "plant", True)
            if room.x2 - room.x1 >= 6:
                add(room.x1, room.y1, "shelf", True)

        elif room.kind == "meeting":
            cx = (room.x1 + room.x2) // 2
            cy = (room.y1 + room.y2) // 2
            for x in range(cx - 1, cx + 2):
                add(x, cy, "table", True)
                add(x, cy + 1, "table", True)
                add(x, cy - 1, "chair", False)
                if cy + 2 <= room.y2:
                    add(x, cy + 2, "chair", False)
            room.visit = (cx, min(cy + 2, room.y2))
            add(room.x2, room.y1, "board", True)
            add(room.x1, room.y2, "plant", True)

        else:  # lounge
            cy = (room.y1 + room.y2) // 2
            add(room.x1 + 1, cy, "sofaL", True)
            add(room.x1 + 2, cy, "sofaR", True)
            add(room.x1 + 4, cy, "table", True)
            add(room.x1 + 4, cy - 1, "chair", False)
            add(room.x1 + 4, cy + 1, "chair", False)
            add(room.x2, room.y1, "coffee", True)
            add(room.x2, room.y2, "plant", True)

    # The corridor gets some life so it does not read as a dead grey band.
    for x in range(4, cols - 3, 7):
        add(x, hall[1], "plant", True)
    add(cols - 2, hall[3], "cooler", True)

    for piece in furniture:
        if piece.solid:
            grid[piece.y][piece.x] = SOLID

    # ---- lighting and windows ----
    lamps: list[tuple[int, int]] = []
    for room in rooms:
        for x in range(room.x1 + 1, room.x2 + 1, 6):
            lamps.append((x, room.y1 + 1))
    for x in range(3, cols - 2, 6):
        lamps.append((x, hall[1]))

    windows: list[tuple[int, int]] = []
    for x in range(2, cols - 2, 5):
        if grid[0][x] == WALL:
            windows.append((x, 0))
        if grid[rows - 1][x] == WALL:
            windows.append((x, rows - 1))

    exec_room = next(room for room in rooms if room.id == "exec")
    spawn = _pick_spawn(
        grid,
        cols,
        rows,
        hall,
        preferred=(exec_room.door[0], hall[1] + (1 if hall_height > 2 else 0)),
    )

    return Floor(
        cols=cols,
        rows=rows,
        rooms=rooms,
        hall=hall,
        furniture=furniture,
        lamps=lamps,
        windows=windows,
        grid=grid,
        spawn=spawn,
    )


def _pick_spawn(
    grid: list[list[int]],
    cols: int,
    rows: int,
    hall: tuple[int, int, int, int],
    preferred: tuple[int, int],
) -> tuple[int, int]:
    """The corridor tile the CEO starts on, guaranteed walkable.

    The prototype computes `SPAWN` as the executive door's column in the corridor and
    then never checks it, because in practice the CEO's position is initialised to a
    hardcoded `(3, 9)` and `reseat()` only relocates them to `SPAWN` if where they are
    is *not* walkable. That hides a real defect: the corridor plants are placed at
    `x = 4, 11, 18, ...` on the corridor's first row, and on the default grid the
    executive door is at column 4 — so `SPAWN` is a solid plant. Anything that actually
    used it would put an actor inside furniture, and since the movement check looks
    ahead from the current tile, an actor standing in a plant cannot move in any
    direction. It is wedged permanently.

    Reproducing that faithfully would mean porting a bug whose only saving grace is
    that the prototype avoids the code path. So the preferred tile is used when it is
    walkable, and otherwise the nearest walkable corridor tile is chosen — scanning
    outward by column, nearest first, with a fixed tiebreak so the answer is identical
    on every machine.
    """
    hall_x1, hall_y1, hall_x2, hall_y2 = hall

    def clear(x: int, y: int) -> bool:
        return 0 <= x < cols and 0 <= y < rows and grid[y][x] in (FLOOR, CORRIDOR)

    if clear(*preferred):
        return preferred

    preferred_x, preferred_y = preferred
    # Rows: the preferred corridor row first, then the rest of the corridor.
    rows_to_try = [preferred_y] + [
        y for y in range(hall_y1, hall_y2 + 1) if y != preferred_y
    ]
    # Columns: outward from the preferred one, left before right at equal distance.
    span = max(preferred_x - hall_x1, hall_x2 - preferred_x)
    offsets = [0] + [offset for distance in range(1, span + 1) for offset in (-distance, distance)]

    for y in rows_to_try:
        for offset in offsets:
            x = preferred_x + offset
            if hall_x1 <= x <= hall_x2 and clear(x, y):
                return (x, y)

    raise AssertionError("the corridor has no walkable tile; the floor is malformed")


def walkable(floor: Floor, x: int, y: int) -> bool:
    if x < 0 or y < 0 or x >= floor.cols or y >= floor.rows:
        return False
    return floor.grid[y][x] in (FLOOR, CORRIDOR)


#: Neighbour order for the breadth-first search. Fixed, and part of the run's
#: determinism: a different order yields a different equally-short path, which would
#: change arrival ticks and therefore the whole run.
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def find_path(
    floor: Floor, origin: tuple[int, int], target: tuple[int, int]
) -> list[tuple[int, int]]:
    """Breadth-first path that never crosses a wall.

    Returns the tiles to walk through, excluding the origin and including the
    target — the same convention as the prototype's `path()`, because arrival is
    detected by the path emptying.

    An unwalkable target (a desk, a wall) falls back to an adjacent walkable cell,
    which is how someone walks *to* a desk rather than onto it.
    """
    if not walkable(floor, *target):
        alternative = next(
            (
                (target[0] + dx, target[1] + dy)
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                if walkable(floor, target[0] + dx, target[1] + dy)
            ),
            None,
        )
        if alternative is None:
            return []
        target = alternative

    if origin == target:
        return []

    previous: dict[tuple[int, int], tuple[int, int]] = {}
    seen = {origin}
    queue: deque[tuple[int, int]] = deque([origin])

    while queue:
        current = queue.popleft()
        if current == target:
            out: list[tuple[int, int]] = []
            node = current
            while node in previous:
                out.append(node)
                node = previous[node]
            out.reverse()
            return out

        for dx, dy in _NEIGHBOURS:
            neighbour = (current[0] + dx, current[1] + dy)
            if neighbour in seen or not walkable(floor, *neighbour):
                continue
            seen.add(neighbour)
            previous[neighbour] = current
            queue.append(neighbour)

    return []


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

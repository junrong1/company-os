"""A QR reader, so the checked-in code can be read back rather than taken on trust.

Execution decision §5 settled that the export's QR code is a **checked-in inline SVG** rather
than a runtime encoder: the URL is fixed at authoring time, the document may contain no script,
and R8's no-new-dependency posture rules out pulling in an encoder to do what a constant already
does. That decision leaves one hole, and this closes it: an asset nothing can read is an asset
nobody notices has gone wrong, and "the QR code resolves to the repository" (M60) would be a
claim resting on somebody having pointed a phone at it once.

So the test decodes the asset. It reads the SVG's own path data back into a module grid, reads
the format information out of the grid, undoes the mask the symbol declares, walks the data
modules in the interleaving order, and parses the byte-mode segment. What comes out is compared
to `export.REPOSITORY`.

**It is a reader and not half an encoder.** There is no Reed-Solomon here, no error correction
and no re-encoding: nothing in this file could reproduce the asset, which is what makes it an
independent check rather than the same walk written twice. What it does share with any encoder
is the *spec* — the mask conditions, the function-module map and the zigzag are ISO/IEC 18004,
and they are as stated there.

Scope: single-data-block symbols below version 7, byte mode, no error correction. That covers
the one asset this repository has, and anything outside it raises rather than guessing.
"""

from __future__ import annotations

import re

#: The quiet zone the asset is authored with, in modules. The spec's minimum is 4 and the
#: generator used it; deriving it instead would mean guessing the version from the border.
QUIET = 4

#: Mask id -> the condition that inverts a module, by column and row. ISO/IEC 18004 §8.8.1.
MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)

#: The two format-info bits, as the symbol carries them, to the level's name.
LEVELS = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}

#: Alignment-pattern centre coordinates by version. Only the versions this reader accepts.
CENTRES = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34)}

_RUN = re.compile(r"M(\d+) (\d+)h(\d+)v1h-\d+z")
_VIEWBOX = re.compile(r'viewBox="0 0 (\d+) (\d+)"')


class Unreadable(ValueError):
    """The asset is not a symbol this reader understands."""


def modules_of(svg: str) -> list[list[bool]]:
    """The module grid an export's QR asset draws, quiet zone removed.

    Read off the path rather than off a fixture beside it, so the thing decoded is the thing the
    document embeds. A second file holding the same matrix would be a second thing to keep in
    step.
    """
    box = _VIEWBOX.search(svg)
    if box is None or box.group(1) != box.group(2):
        raise Unreadable("the asset has no square viewBox")

    size = int(box.group(1)) - 2 * QUIET
    if size < 21 or (size - 17) % 4:
        raise Unreadable(f"{size} modules is not a QR symbol size")

    grid = [[False] * size for _ in range(size)]
    for x, y, width in ((int(a), int(b), int(c)) for a, b, c in _RUN.findall(svg)):
        for step in range(width):
            grid[y - QUIET][x - QUIET + step] = True
    return grid


def version_of(grid: list[list[bool]]) -> int:
    return (len(grid) - 17) // 4


def format_of(grid: list[list[bool]]) -> tuple[str, int]:
    """The error-correction level and the mask id the symbol declares.

    Read from the copy beside the top-left finder, un-XOR'd with the spec's 0x5412. The second
    copy, split between the other two corners, is redundancy for a damaged symbol and there is
    nothing here to damage one.
    """
    bits = 0
    for index in range(6):
        bits |= int(grid[index][8]) << index
    bits |= int(grid[7][8]) << 6
    bits |= int(grid[8][8]) << 7
    bits |= int(grid[8][7]) << 8
    for index in range(9, 15):
        bits |= int(grid[8][14 - index]) << index

    bits ^= 0x5412
    declared = bits >> 10
    level = LEVELS.get(declared >> 3)
    if level is None:  # pragma: no cover - every two-bit value is a level
        raise Unreadable("the format information names no error-correction level")
    return level, declared & 0b111


def _functional(grid: list[list[bool]]) -> list[list[bool]]:
    """Which modules carry the symbol's structure rather than its data.

    The three finder corners with their separators and format reserve, the two timing lines, and
    the alignment patterns — the centres that fall inside a finder are not drawn, which is what
    the exclusion below is. The dark module needs no case of its own: it sits inside the
    bottom-left reserve.
    """
    size = len(grid)
    taken = [[False] * size for _ in range(size)]

    def reserve(rows: range, columns: range) -> None:
        for row in rows:
            for column in columns:
                taken[row][column] = True

    reserve(range(9), range(9))
    reserve(range(9), range(size - 8, size))
    reserve(range(size - 8, size), range(9))
    reserve(range(size), range(6, 7))
    reserve(range(6, 7), range(size))

    centres = CENTRES.get(version_of(grid))
    if centres is None:
        raise Unreadable("this reader stops below version 7, where version information appears")
    last = len(centres) - 1
    for row_index, row_centre in enumerate(centres):
        for column_index, column_centre in enumerate(centres):
            if (row_index, column_index) in ((0, 0), (0, last), (last, 0)):
                continue
            reserve(
                range(row_centre - 2, row_centre + 3),
                range(column_centre - 2, column_centre + 3),
            )
    return taken


def codewords_of(grid: list[list[bool]], mask: int) -> list[int]:
    """The symbol's codewords, unmasked and read in the interleaving order.

    Two-module columns, right to left, alternating up and down, skipping the vertical timing
    line — ISO/IEC 18004 §8.7.3. The trailing remainder bits, which are fewer than eight and
    carry nothing, fall off the end.
    """
    size = len(grid)
    taken = _functional(grid)
    inverts = MASKS[mask]

    bits: list[int] = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for step in range(size):
            for offset in range(2):
                column = right - offset
                row = size - 1 - step if upward else step
                if taken[row][column]:
                    continue
                bits.append(int(grid[row][column] != inverts(column, row)))
        right -= 2

    return [
        int("".join(str(bit) for bit in bits[at : at + 8]), 2)
        for at in range(0, len(bits) - 7, 8)
    ]


def text_of(codewords: list[int]) -> str:
    """The byte-mode segment a single-block symbol's data codewords carry.

    The data codewords come first and the error-correction codewords after them, which is only
    true because the symbol has one block; a multi-block symbol interleaves them and would need
    the block table this reader deliberately does not carry. The length field decides where the
    text stops, so the parity bytes past it are never looked at.
    """
    mode = codewords[0] >> 4
    if mode != 0b0100:
        raise Unreadable(f"mode {mode:04b} is not byte mode")

    # The length field straddles the first two codewords: four bits of mode, then eight of
    # length, so every byte after it is split across two codewords as well.
    length = ((codewords[0] & 0x0F) << 4) | (codewords[1] >> 4)
    payload = bytes(
        ((codewords[index + 1] & 0x0F) << 4) | (codewords[index + 2] >> 4)
        for index in range(length)
    )
    return payload.decode("utf-8")


def read(svg: str) -> tuple[str, str, int]:
    """What the asset encodes, at what error-correction level, under which mask."""
    grid = modules_of(svg)
    level, mask = format_of(grid)
    return text_of(codewords_of(grid, mask)), level, mask

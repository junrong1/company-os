"""The README's first screenful, and the hero it leads with (U23, M66).

**The README is the first audience's only surface**, and the thing it has to do is the thing
nothing in the tree enforces: say what this is before anybody scrolls. So what is pinned here
is the shape of the first screenful rather than its prose — the hero above the fold, a sentence
that names the loop, the one command that works, and an alt text for the reader who cannot see
the picture.

**And that the hero is a recording.** M66 asks for eight seconds of walk, conversation, decision
and a cut to two futures side by side, and the easy version of that is four mockups in a row.
The difference is not visible in the file, so it is asserted where it *is* visible: the capture
refuses to run without a kernel, creates its own run through the gateway, and ends on the diff
route's own rows. A montage would need none of those, and could not have any of them.

What no test here claims is that the hero is *good*. Whether eight seconds of a company reads as
a company is a human call, like every other judgement `frontend/scripts/screenshots.mjs` exists
to put in front of somebody.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"
HERO = ROOT / "docs" / "assets" / "hero" / "company-os-hero.gif"
STILL = ROOT / "docs" / "assets" / "hero" / "company-os-hero-last-frame.png"
CAPTURE = ROOT / "frontend" / "scripts" / "hero.mjs"

#: What counts as "without scrolling". A generous screenful at a comfortable reading width —
#: generous on purpose, because the claim is about what a stranger meets first rather than about
#: any particular window, and a tight bound would fail on an editor somebody widened.
FIRST_SCREENFUL_LINES = 24


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def first_screenful(readme: str) -> str:
    return "\n".join(readme.splitlines()[:FIRST_SCREENFUL_LINES])


# =========================================================================
# The first screenful (M66)
# =========================================================================


def test_the_first_screenful_says_what_this_is(first_screenful: str) -> None:
    """A stranger reading only this much can say what the thing is.

    Three claims, because "says what this is" is three separate ways to fail: a title that could
    be anything, a description of the architecture rather than of the game, and a page that
    explains the loop only after the reader has scrolled past the licence.
    """
    assert first_screenful.startswith("# Company OS")

    lowered = first_screenful.lower()
    for word in ("ceo", "walk", "decide", "fork"):
        assert word in lowered, f"the first screenful never mentions {word}"

    assert "simulator" in lowered or "simulat" in lowered


def test_the_hero_is_the_first_thing_under_the_title(first_screenful: str) -> None:
    """The picture before the prose, and before the badge.

    A build badge above the hero is the shape this had before: the first coloured thing on the
    page was a status of the tests rather than a picture of the product.
    """
    image = re.search(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)", first_screenful, re.DOTALL)
    assert image is not None, "there is no image in the first screenful"

    assert image.start() < first_screenful.index("[![CI]"), "the badge is above the hero"
    assert image.group("src") == "docs/assets/hero/company-os-hero.gif"


def test_the_hero_carries_an_alt_text_that_describes_the_eight_seconds(
    first_screenful: str,
) -> None:
    """The one reader who cannot see the hero is the one who meets it first.

    An alt text of "hero" or "screenshot" satisfies a linter and tells that reader nothing, so
    what is checked is that it describes the *beats* — which is also the one summary of M66 a
    reader gets without watching anything.
    """
    image = re.search(r"!\[(?P<alt>[^\]]*)\]\([^)]+\)", first_screenful, re.DOTALL)
    assert image is not None
    alt = " ".join(image.group("alt").split()).lower()

    assert len(alt) > 60, f"the alt text is too short to describe eight seconds: {alt!r}"
    for beat in ("walk", "decide", "timeline"):
        assert beat in alt, f"the alt text never mentions the {beat} beat"


def test_the_first_screenful_carries_the_command_that_works(first_screenful: str) -> None:
    """M67's command, above the fold as well as in its own section.

    The same string in both places rather than two spellings of it: the one in the hero block is
    what a stranger copies, and a second, subtly different one is how a README starts being
    wrong.
    """
    assert "docker compose up" in first_screenful
    assert "127.0.0.1:8790" in first_screenful


def test_the_first_screenful_is_not_the_whole_document(readme: str) -> None:
    """A guard on the guard above: every assertion here is vacuous if the bound is the file.

    It also says something worth saying on its own — the hero block is an opening, and a first
    screenful that ran to a hundred lines would not be one.
    """
    assert len(readme.splitlines()) > FIRST_SCREENFUL_LINES * 10


# =========================================================================
# The hero is a recording
# =========================================================================


def test_the_hero_is_checked_in_and_is_an_animation() -> None:
    """A GIF of the right shape, long enough to be the eight seconds and short enough to load."""
    assert HERO.exists(), "the hero is not checked in"
    bytes_ = HERO.read_bytes()

    assert bytes_[:6] == b"GIF89a"
    width = int.from_bytes(bytes_[6:8], "little")
    height = int.from_bytes(bytes_[8:10], "little")
    assert (width, height) == (1200, 675), "the hero is not the frame the capture shoots"

    delays = _frame_delays(bytes_)
    assert len(delays) > 40, f"{len(delays)} frames is not an animation"
    seconds = sum(delays) / 100
    assert 5 <= seconds <= 12, f"the hero runs {seconds:.1f}s; M66 asks for about eight"

    # A README is fetched before anybody has decided to care. Two megabytes of it is a decision
    # made on their behalf.
    assert len(bytes_) < 2_000_000, f"the hero is {len(bytes_) / 1024 / 1024:.1f} MB"


def test_the_last_frame_is_kept_as_a_still() -> None:
    """For a print, a slide, or anywhere an animation does not run."""
    assert STILL.exists()
    bytes_ = STILL.read_bytes()

    assert bytes_[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(bytes_[16:20], "big") == 1200
    assert int.from_bytes(bytes_[20:24], "big") == 675


def test_the_hero_is_captured_from_a_live_run_rather_than_mocked() -> None:
    """The difference a viewer cannot see, asserted where it is visible.

    `screenshots.mjs` deliberately falls back to a recorded genesis, because what it photographs
    is the art and requiring a Python stack to look at the art means nobody looks at it. This
    one may not: what it photographs is the loop, and there is no fallback that would still be
    the loop. So it refuses without a kernel, it makes its own run through the gateway, and the
    walk is driven by keys rather than by writing a position into the store.
    """
    source = CAPTURE.read_text(encoding="utf-8")

    assert "/status" in source and "No kernel at" in source, "it does not refuse without a kernel"
    assert "/runs`" in source or "/runs'" in source, "it does not create a run through the gateway"
    assert "keyboard.down" in source, "the CEO is not walked with the keyboard"
    # The tell of a mocked capture: a genesis pushed straight into the store, which is exactly
    # what the screenshot harness next door does and is exactly what this one must not.
    assert "fixtures/golden" not in source
    assert "useRunStore.getState().apply" not in source


def test_the_hero_ends_on_the_diff_between_two_real_timelines() -> None:
    """M66's last clause, and the one that cannot be faked by a longer recording.

    Two futures side by side is a *fold across two logs* at one sim-day, so the capture has to
    have made a second timeline and waited for both to reach a day boundary past the decision
    they parted at — otherwise the columns are identical and the difference column is zeros.
    That is what these three lines are: the fork, the wait, and the rows the diff route answers
    with being the last thing filmed.
    """
    source = CAPTURE.read_text(encoding="utf-8")

    assert "alternative__fork" in source, "nothing in the capture forks"
    assert "bothReachDayTwo" in source, "the two timelines are never run past the divergence"
    assert "diff__rows" in source, "the capture does not wait for the diff's figures"

    # The rows, then the last hold: anything after that hold would be the frame the film ends on.
    rows = source.index("diff__rows")
    assert source.index("hold(page, 22)", rows) > rows
    assert "publish()" in source


def _frame_delays(bytes_: bytes) -> list[int]:
    """Every frame's delay, in hundredths of a second, walking the blocks.

    A reader written for one question rather than a decoder: the frame count and the running
    time are the two things this file has an opinion about, and both are in the graphic control
    extensions. `frontend/tests/hero.test.ts` decodes the pixels.
    """
    at = 13 + (3 * (2 << (bytes_[10] & 0x07)) if bytes_[10] & 0x80 else 0)
    delays: list[int] = []

    def sub_blocks(start: int) -> int:
        while bytes_[start] != 0:
            start += 1 + bytes_[start]
        return start + 1

    while at < len(bytes_) and bytes_[at] != 0x3B:
        marker = bytes_[at]
        if marker == 0x21:
            label = bytes_[at + 1]
            if label == 0xF9:
                delays.append(int.from_bytes(bytes_[at + 4 : at + 6], "little"))
                at = sub_blocks(at + 2)
            elif label == 0xFF:
                at = sub_blocks(at + 2 + 1 + bytes_[at + 2])
            else:
                at = sub_blocks(at + 2)
        elif marker == 0x2C:
            if bytes_[at + 9] & 0x80:  # pragma: no cover - the writer emits no local tables
                raise AssertionError("the hero carries a local colour table")
            at = sub_blocks(at + 11)
        else:  # pragma: no cover - any other marker is a file this reader does not understand
            raise AssertionError(f"unknown block 0x{marker:02x} at {at}")

    return delays

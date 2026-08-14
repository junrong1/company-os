"""The toolchain pins, asserted rather than trusted.

The interpreter on PATH is 3.9.12 and FastAPI, Starlette and Uvicorn all floor at
3.10, so "it worked on my machine" is a real failure mode here. These tests make
the pin a property of the suite instead of a line in a file nobody re-reads.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    with (BACKEND / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _dependency(name: str) -> str:
    for raw in _pyproject()["project"]["dependencies"]:
        # Matches "starlette==1.3.1" and "uvicorn[standard]==0.52.2" alike.
        head = raw.split("==")[0].split("[")[0].strip()
        if head == name:
            return raw
    raise AssertionError(f"{name} is not a declared dependency")


def test_running_on_python_312() -> None:
    """`uv run` resolves 3.12. On 3.9 the dependencies do not even install."""
    assert sys.version_info[:2] == (3, 12), (
        f"expected Python 3.12, got {sys.version.split()[0]}. "
        "`uv run` should select the pinned interpreter; a bare `python3` will not."
    )


def test_python_version_file_pins_312() -> None:
    assert (BACKEND / ".python-version").read_text().strip() == "3.12"


def test_requires_python_is_bounded_above() -> None:
    """A floor alone would let uv pick any newer line.

    The upper bound is what makes an unintended interpreter a loud failure instead
    of a silently different resolution.
    """
    requires = _pyproject()["project"]["requires-python"]
    assert ">=3.12" in requires
    assert "<3.13" in requires


def test_starlette_is_pinned_exactly() -> None:
    """R14: Starlette carries an explicit pin.

    FastAPI 0.141.1 declares `starlette>=0.46.0` with no upper bound. Left to
    resolve, that picks up Starlette 1.6.0 — released after the FastAPI version
    that depends on it, and therefore never tested against it.
    """
    assert _dependency("starlette") == "starlette==1.3.1"


def test_every_dependency_is_pinned_exactly() -> None:
    """One lockfile, one resolution. A range here is a future surprise."""
    unpinned = [
        raw
        for raw in _pyproject()["project"]["dependencies"]
        if "==" not in raw
    ]
    assert not unpinned, f"unpinned dependencies: {unpinned}"


def test_installed_starlette_matches_the_pin() -> None:
    """The pin means nothing if the environment drifted from it."""
    import starlette

    assert starlette.__version__ == "1.3.1"

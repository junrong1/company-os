"""How a run ends.

Two ways: it reaches its horizon, or it runs out of cash.

**The horizon is chosen at genesis, recorded, immutable, and inherited by forks.** Immutable
because a horizon that could move is not a bound; inherited because a child that outlived its
parent's bound would not be a comparable run, and comparing runs is the whole point of forking.

**Insolvency is evaluated at tick boundaries, and the crossing quantum completes.** This is
the load-bearing detail. If termination fired the instant cash went negative, then *where*
inside the quantum it fired would decide the final cash — and two implementations that
ordered the day's costs differently would produce different final numbers from the same log.
The quantum finishes, and termination is its last event, so the report is reproducible.

**The horizon is bounded by decision supply, not by ambition.** The eight authored items carry
nine checkpoints between them. Baseline load regenerates forever, so a longer horizon is burn
with nothing left to decide — which reads as a failing company rather than as a finished run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from simcore import time as simtime

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from simcore.scenario import Scenario

#: Sim-days a run gets by default.
#:
#: Sized against the decision supply rather than picked round: nine authored checkpoints, and
#: a run needs slack around each to walk over, decide, and let the consequence land. Twenty
#: days is a little over two days per decision.
DEFAULT_HORIZON_DAYS = 20

TERMINAL_HORIZON = "horizon"
TERMINAL_INSOLVENT = "insolvent"


def default_horizon_tick() -> int:
    return DEFAULT_HORIZON_DAYS * simtime.TICKS_PER_SIM_DAY


def decision_supply(scenario: Scenario) -> int:
    """How many decisions this company's authored work offers. The bound on a useful run.

    Read off the scenario rather than off a module constant, so a second company gets its own
    figure. The report reads it back from the genesis and termination payloads instead of
    recomputing it, which is what keeps an exported run's outcome self-describing.
    """
    return scenario.total_checkpoints


@dataclass(frozen=True, slots=True)
class Termination:
    reason: str
    tick: int
    day: int
    detail: str


def check(tick: int, cash: int, horizon_tick: int) -> Termination | None:
    """Should the run end at the boundary of the quantum just completed?

    Called after a quantum finishes, never inside one — see the module docstring.
    """
    if cash < 0:
        return Termination(
            reason=TERMINAL_INSOLVENT,
            tick=tick,
            day=simtime.day_of(tick),
            detail=(
                f"cash reached {cash} at the end of the quantum on day {simtime.day_of(tick)}. "
                "The quantum in which it crossed completed in full, so this figure is "
                "reproducible from the log."
            ),
        )

    if horizon_tick and tick >= horizon_tick:
        return Termination(
            reason=TERMINAL_HORIZON,
            tick=tick,
            day=simtime.day_of(tick),
            detail=(
                f"the run reached its horizon of {horizon_tick} ticks "
                f"({horizon_tick // simtime.TICKS_PER_SIM_DAY} sim-days), recorded at genesis."
            ),
        )

    return None

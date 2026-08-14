"""The five company metrics, and how an effect lands on them.

Ported from script section 5 of `company-os.html` (`METRICS`, `applyEffect`), minus
the DOM.

`good` is which direction counts as an improvement, and it is not cosmetic: spending
cash and cutting hours are both negative numbers, and a uniform rising-is-good rule
would render the automation gain — the whole point of the run — as a regression. U13's
HUD reads this field.

The clamps are the prototype's, and cash deliberately has none. Cash is allowed to go
negative because insolvency is a run outcome (U8), evaluated at a tick boundary rather
than clamped away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore import capacity


@dataclass(frozen=True, slots=True)
class MetricDef:
    key: str
    label: str
    unit: str
    #: +1 if rising is an improvement, -1 if falling is.
    good: int
    ceiling: int | None = None
    floor: int | None = None
    #: Where the HUD's bar reads full. Presentation only — it scales a bar, it clamps
    #: nothing — but it is authored here because `manual_hours_display_max` is coupled to
    #: the department draws that produce the value, and splitting the two would let a
    #: tuning pass move the draws and leave the bar reading against a stale maximum.
    display_max: int = 100
    #: The short unit a delta chip uses, where the full unit is too wide.
    chip_unit: str = ""


#: Keys are the prototype's, camelCase included. They travel into event payloads and
#: on to the client, so renaming them would mean a translation layer between the
#: parity suite, the log and the golden vectors — three places to disagree.
#:
#: `display_max` for `manualHours` is not a literal: it is the re-authored maximum that
#: replaced the prototype's 500, and it lives next to the draws in `simcore.capacity`.
METRICS: tuple[MetricDef, ...] = (
    MetricDef("cash", "Cash", "$K", good=1, display_max=6000, chip_unit="K"),
    MetricDef(
        "manualHours",
        "Manual work",
        "h/mo",
        good=-1,
        floor=0,
        display_max=capacity.MANUAL_HOURS_DISPLAY_MAX,
        chip_unit="h/mo",
    ),
    MetricDef("leadTime", "Avg lead time", "days", good=-1, floor=1, display_max=20, chip_unit="d"),
    MetricDef("morale", "Morale", "/100", good=1, floor=0, ceiling=100, display_max=100),
    MetricDef(
        "visibility", "Visibility", "%", good=1, floor=0, ceiling=100, display_max=100, chip_unit="%"
    ),
)

METRIC_KEYS: tuple[str, ...] = tuple(metric.key for metric in METRICS)
METRICS_BY_KEY: dict[str, MetricDef] = {metric.key: metric for metric in METRICS}


def metric_defs_to_state() -> list[dict[str, Any]]:
    """The metric table, as the genesis event carries it to the HUD (U13).

    `good` is the field the HUD needs most and the one it must not re-derive. Spending
    cash and cutting hours are both negative numbers, so a client applying a uniform
    rising-is-good rule would render the automation gain — the whole point of a run — as a
    regression. Shipping the table means the HUD reads the kernel's own answer instead of
    keeping a second copy of it in TypeScript, which is a duplication no golden vector
    would be cheap enough to guard.
    """
    return [
        {
            "key": metric.key,
            "label": metric.label,
            "unit": metric.unit,
            "chip_unit": metric.chip_unit,
            "good": metric.good,
            "floor": metric.floor,
            "ceiling": metric.ceiling,
            "display_max": metric.display_max,
        }
        for metric in METRICS
    ]

#: The key an authored option effect uses for a recurring-draw change, in hours per month.
#:
#: Not a metric. `manualHours` is *derived* from the sum of department draws (R49), so an
#: effect that moved the metric directly would immediately disagree with the draws it is
#: supposed to describe. Callers split this key out with `split_draw` and route it to
#: `simcore.capacity`.
DRAW_KEY = "draw"


def initial_metrics() -> dict[str, int]:
    """The company on day one.

    `manualHours` comes from the authored department draws rather than being authored
    separately — which is what replaces the prototype's 420 h/mo against a maximum of 500.
    """
    return {
        "cash": 4800,
        "manualHours": capacity.INITIAL_MANUAL_HOURS,
        "leadTime": 12,
        "morale": 72,
        "visibility": 6,
    }


def split_draw(effect: dict[str, int]) -> tuple[dict[str, int], int]:
    """Separate an authored effect into metric deltas and a recurring-draw change."""
    metrics = {key: value for key, value in effect.items() if key != DRAW_KEY}
    return metrics, int(effect.get(DRAW_KEY, 0))


def clamp(metrics: dict[str, int]) -> dict[str, int]:
    """Apply each metric's own bounds. Cash has none, on purpose."""
    for metric in METRICS:
        value = metrics[metric.key]
        if metric.floor is not None:
            value = max(metric.floor, value)
        if metric.ceiling is not None:
            value = min(metric.ceiling, value)
        metrics[metric.key] = value
    return metrics


def apply_effect(
    metrics: dict[str, int], effect: dict[str, int]
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply integer deltas and return `(metrics, effective_deltas)`.

    The returned deltas are what actually happened, after clamping — not what was
    requested. The prototype accumulates the *requested* delta, but it only uses that
    for a UI flash. Here the number has to answer "follow this metric movement to the
    event that caused it", so a request for -5 morale against a floor of 0 that only
    moved 2 must report 2. Reporting 5 would make the report's arithmetic not add up.
    """
    unknown = sorted(set(effect) - set(METRIC_KEYS))
    if unknown:
        raise KeyError(f"effect names metrics that do not exist: {unknown}")

    for key, delta in effect.items():
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise ValueError(
                f"effect on {key!r} is {type(delta).__name__}; metric deltas are integers"
            )

    before = dict(metrics)
    for key, delta in effect.items():
        metrics[key] = metrics[key] + delta
    clamp(metrics)

    effective = {
        key: metrics[key] - before[key] for key in effect if metrics[key] != before[key]
    }
    return metrics, effective


def is_favourable(key: str, delta: int) -> bool:
    """Whether a movement is an improvement, by the metric's own direction."""
    return (delta > 0) == (METRICS_BY_KEY[key].good > 0) if delta else False

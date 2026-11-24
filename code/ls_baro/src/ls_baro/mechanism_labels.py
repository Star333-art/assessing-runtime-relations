"""Exploratory operational labels for paired complete-mask contrasts."""
from __future__ import annotations

import math
from collections.abc import Iterable


RULES = {
    "Alignment-dependent recovery": "Real-Mapping and all four Real-Control interval lower bounds > 0",
    "Recovery not separable from controls": "Real-Mapping lower bound > 0; any Real-Control lower bound <= 0",
    "Uncertain recovery": "Real-Mapping interval contains zero and its point estimate > 0",
    "No recovery": "Real-Mapping interval contains zero and its point estimate <= 0",
    "Harmful": "Real-Mapping interval upper bound < 0",
}


def mechanism_label(total, controls: Iterable) -> str:
    """Use unrounded intervals; require exactly four matched controls."""
    controls = tuple(controls)
    if len(controls) != 4:
        raise ValueError("Exactly four mechanism controls are required.")
    for row in (total, *controls):
        values = (row.difference_pp, row.ci95_low_pp, row.ci95_high_pp)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Contrast estimates and interval endpoints must be finite.")
        if row.ci95_low_pp > row.ci95_high_pp:
            raise ValueError("Invalid interval endpoint order.")
    if total.ci95_low_pp > 0:
        if all(row.ci95_low_pp > 0 for row in controls):
            return "Alignment-dependent recovery"
        return "Recovery not separable from controls"
    if total.ci95_high_pp < 0:
        return "Harmful"
    return "Uncertain recovery" if total.difference_pp > 0 else "No recovery"

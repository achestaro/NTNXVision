"""Where part 1 of a figure goes: under its section's headings and design summary, or
at the top of a page of its own.

The report measures the room under the headings and summary (`first_budget`, from
`exporters.content.first_part_budget`) and the layout fits part 1 into it, cutting the
figure into more parts or drawing less detail where it must. Only when part 1 still
does not fit (a single column or line taller than the room) is the figure laid out for
a full page, its part 1 marked ``meta["own_page"]``: Word and the PDF keep the summary
with its heading and start the figure and its caption on the next page, so a caption
never slips away from its figure and a figure never runs into the footer.
"""

from __future__ import annotations

from collections.abc import Callable

from ..scene import Scene
from .conceptual import Budget

#: Rounding slack (pt) when comparing a laid-out height with its budget.
EPS = 0.01


def fits_first(scenes: list[Scene], first_budget: Budget) -> bool:
    """Part 1 of `scenes` fits under the headings and summary."""
    return not scenes or scenes[0].height <= first_budget(scenes[0].width) + EPS


def place_first(run: Callable[[Budget | None], list[Scene]],
                first_budget: Budget | None) -> list[Scene]:
    """`run(budget)` laid out under the summary when its part 1 fits there, else for a
    page of its own (part 1 marked ``own_page``)."""
    if first_budget is None:
        return run(None)
    under = run(first_budget)
    if fits_first(under, first_budget):
        return under
    free = run(None)
    if free:
        free[0].meta["own_page"] = True
    return free


__all__ = ["fits_first", "place_first"]

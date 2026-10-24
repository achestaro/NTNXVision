"""Capacity runway: when a resource reaches 80% and 100%, from its trend.

The one place the Metrics page's runway is calculated. Utilization says what is happening
now; runway says when it becomes a problem.

Method, per resource (CPU %, memory %, storage %) and per range (30, 60 or 90 days):

* Keep the daily points inside the range (newest point's day minus ``window_days``).
* Fewer than `MIN_POINTS` points, or less than `MIN_SPAN_DAYS` between the first and the
  last, is ``not_enough_data``: a fit over a few days says nothing about months.
* Fit a straight line (ordinary least squares, value against days). Its slope is the
  growth in percentage points per day; ``r2`` is how well the line explains the points.
* Start from today's level, the mean of the last `START_POINTS` points (one noisy day
  does not move the forecast), and project at the fitted slope:
  ``days = ceil((threshold - start) / slope)``.
* A slope below `MIN_SLOPE` points per day (about 0.3 points a month) is ``no_growth``:
  flat or shrinking usage never reaches a threshold it is not already at.
* A start already at or above a threshold is ``reached`` for it (0 days). A projection
  past `HORIZON_DAYS` (five years) is ``beyond``: too far to plan against.

Nothing here talks to an API; `live.py` feeds it the series the Prism Central groups API
returned, so the same numbers are reproducible in tests.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

DAY_MS = 86_400_000
#: Points needed before a trend is fitted.
MIN_POINTS = 7
#: Days the points must span before a trend is fitted.
MIN_SPAN_DAYS = 7.0
#: Below this growth (percentage points per day) usage counts as flat.
MIN_SLOPE = 0.01
#: Points averaged for today's level.
START_POINTS = 3
#: Projections further out than this are reported as "beyond".
HORIZON_DAYS = 1825
#: The thresholds every runway reports.
THRESHOLDS = (80.0, 100.0)
#: The ranges the page offers.
RANGES = (30, 60, 90)


def linear_fit(points: Sequence[tuple[float, float]]) -> tuple[float, float, float] | None:
    """Least-squares line through (x, y) points: (intercept, slope, r2), None if undefined.

    r2 is 1.0 for a perfectly flat series (every point on the line).
    """
    n = len(points)
    if n < 2:
        return None
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mean_x) ** 2 for p in points)
    if sxx == 0:
        return None
    sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((p[1] - mean_y) ** 2 for p in points)
    ss_res = sum((p[1] - (intercept + slope * p[0])) ** 2 for p in points)
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return intercept, slope, r2


def _clean(series: Iterable[tuple[float, float | None]]) -> list[tuple[float, float]]:
    out = [(float(t), float(v)) for t, v in series
           if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]
    out.sort(key=lambda p: p[0])
    return out


def _date(ms: float, days: int) -> str:
    when = datetime.fromtimestamp(ms / 1000, tz=UTC) + timedelta(days=days)
    return when.date().isoformat()


def runway(series: Iterable[tuple[float, float | None]], window_days: int,
           thresholds: Sequence[float] = THRESHOLDS) -> dict[str, Any]:
    """Runway of one percentage series ((epoch ms, percent) points) over `window_days`.

    Returns ``{status, window_days, points, current, slope_per_day, r2, thresholds}`` with
    ``status`` one of ``not_enough_data``, ``no_growth``, ``growing``, and each threshold
    ``{pct, state, days, date}``, ``state`` one of ``reached``, ``projected``, ``beyond``,
    ``never``, ``unknown``. ``days`` and ``date`` (ISO day) are set for ``reached`` and
    ``projected`` only.
    """
    points = _clean(series)
    if points:
        newest = points[-1][0]
        points = [p for p in points if p[0] >= newest - window_days * DAY_MS]
    out: dict[str, Any] = {"status": "not_enough_data", "window_days": window_days,
                           "points": len(points), "current": None, "slope_per_day": None,
                           "r2": None,
                           "thresholds": [{"pct": t, "state": "unknown", "days": None,
                                           "date": None} for t in thresholds]}
    if not points:
        return out
    tail = points[-START_POINTS:]
    start = sum(p[1] for p in tail) / len(tail)
    out["current"] = round(start, 2)
    span_days = (points[-1][0] - points[0][0]) / DAY_MS
    fit = linear_fit([((t - points[0][0]) / DAY_MS, v) for t, v in points])
    if len(points) < MIN_POINTS or span_days < MIN_SPAN_DAYS or fit is None:
        # Too little history to project, but a threshold already crossed is still a fact.
        for th in out["thresholds"]:
            if start >= th["pct"]:
                th.update(state="reached", days=0, date=_date(points[-1][0], 0))
        return out
    _, slope, r2 = fit
    out["slope_per_day"] = round(slope, 4)
    out["r2"] = round(r2, 3)
    out["status"] = "growing" if slope >= MIN_SLOPE else "no_growth"
    now_ms = points[-1][0]
    for th in out["thresholds"]:
        target = th["pct"]
        if start >= target:
            th.update(state="reached", days=0, date=_date(now_ms, 0))
        elif slope < MIN_SLOPE:
            th["state"] = "never"
        else:
            days = math.ceil((target - start) / slope)
            if days > HORIZON_DAYS:
                th["state"] = "beyond"
            else:
                th.update(state="projected", days=days, date=_date(now_ms, days))
    return out


def runways(series: Iterable[tuple[float, float | None]],
            ranges: Sequence[int] = RANGES) -> dict[str, dict[str, Any]]:
    """`runway` for every range, keyed by the range in days as a string ("30", ...)."""
    points = list(series)
    return {str(days): runway(points, days) for days in ranges}

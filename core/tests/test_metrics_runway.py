"""Runway: the linear fit and the days until 80% / 100% (metrics/runway.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ntnxvision_core.metrics.runway import (
    DAY_MS,
    HORIZON_DAYS,
    linear_fit,
    runway,
    runways,
)

T0 = datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000


def daily(values: list[float | None]) -> list[tuple[float, float | None]]:
    return [(T0 + i * DAY_MS, v) for i, v in enumerate(values)]


def by_pct(result: dict) -> dict[float, dict]:
    return {t["pct"]: t for t in result["thresholds"]}


def test_linear_fit_exact_line() -> None:
    intercept, slope, r2 = linear_fit([(0, 10), (1, 12), (2, 14), (3, 16)])
    assert intercept == pytest.approx(10)
    assert slope == pytest.approx(2)
    assert r2 == pytest.approx(1)


def test_linear_fit_undefined() -> None:
    assert linear_fit([(1, 5)]) is None
    assert linear_fit([(1, 5), (1, 6)]) is None


def test_flat_series_has_r2_one_and_no_growth() -> None:
    res = runway(daily([50.0] * 30), 30)
    assert res["status"] == "no_growth"
    assert res["r2"] == 1.0
    assert all(t["state"] == "never" for t in res["thresholds"])


def test_growing_series_projects_days_and_dates() -> None:
    # 50% growing half a point a day: today's level (mean of the last 3) is 64.0.
    res = runway(daily([50 + 0.5 * i for i in range(30)]), 30)
    assert res["status"] == "growing"
    assert res["slope_per_day"] == pytest.approx(0.5)
    assert res["current"] == pytest.approx(64.0)
    th = by_pct(res)
    assert th[80.0] == {"pct": 80.0, "state": "projected", "days": 32, "date": "2026-08-31"}
    assert th[100.0]["days"] == 72


def test_threshold_already_reached() -> None:
    res = runway(daily([79 + 0.2 * i for i in range(20)]), 30)
    th = by_pct(res)
    assert th[80.0]["state"] == "reached" and th[80.0]["days"] == 0
    assert th[100.0]["state"] == "projected"


def test_not_enough_points_or_span() -> None:
    assert runway(daily([10, 11, 12]), 30)["status"] == "not_enough_data"
    assert runway([], 30)["status"] == "not_enough_data"
    # Seven points but all within one day: no span to fit over.
    same_day = [(T0 + i * 3_600_000, 10.0 + i) for i in range(8)]
    assert runway(same_day, 30)["status"] == "not_enough_data"


def test_not_enough_data_still_reports_a_crossed_threshold() -> None:
    res = runway(daily([90, 91, 92]), 30)
    assert res["status"] == "not_enough_data"
    assert by_pct(res)[80.0]["state"] == "reached"
    assert by_pct(res)[100.0]["state"] == "unknown"


def test_shrinking_usage_never_reaches() -> None:
    res = runway(daily([70 - 0.3 * i for i in range(40)]), 30)
    assert res["status"] == "no_growth"
    assert {t["state"] for t in res["thresholds"]} == {"never"}


def test_slow_growth_beyond_horizon() -> None:
    res = runway(daily([10 + 0.011 * i for i in range(60)]), 60)
    assert res["status"] == "growing"
    th = by_pct(res)
    assert th[80.0]["state"] == "beyond" and th[80.0]["days"] is None
    assert HORIZON_DAYS < (80 - 10) / 0.011


def test_gaps_are_skipped_and_window_applies() -> None:
    values: list[float | None] = [20.0] * 60 + [20 + i for i in range(30)]
    values[70] = None
    whole = runway(daily(values), 90)
    recent = runway(daily(values), 30)
    assert recent["points"] == 30  # one gap, plus the newest point's own day
    assert recent["slope_per_day"] > whole["slope_per_day"]


def test_runways_cover_every_range() -> None:
    res = runways(daily([40 + 0.1 * i for i in range(91)]))
    assert set(res) == {"30", "60", "90"}
    assert res["90"]["points"] == 91
    assert res["30"]["points"] == 31

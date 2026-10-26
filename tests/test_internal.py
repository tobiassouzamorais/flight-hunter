from datetime import date
from pathlib import Path

from hunter.internal import build_grid, load_internal, plans

CFG = load_internal(Path(__file__).resolve().parents[1] / "config.yaml")


def test_plans_respect_full_days_and_return_margin():
    ps = plans(CFG)
    assert ps, "no feasible schedule"
    for p in ps:
        assert (p.d2 - p.d1).days == CFG.full_days_min + 1
        assert (p.d3 - p.d2).days == CFG.full_days_min + 1
        assert p.d1 >= date(2027, 2, 9) and p.d3 <= date(2027, 2, 23)
    assert {p.order for p in ps} == {("Paris", "Rome"), ("Rome", "Paris")}
    assert max(p.d1 for p in ps) == date(2027, 2, 13)


def test_first_leg_on_arrival_day_respects_connection_time():
    grid = build_grid(CFG, today=date(2026, 10, 26))
    first_day = [r for r in grid if r.date_out == date(2027, 2, 9)]
    assert first_day and all(r.earliest_hour == 14 for r in first_day)
    assert all(r.earliest_hour is None for r in grid if r.date_out != date(2027, 2, 9))
    assert len({r.key() for r in grid}) == len(grid)

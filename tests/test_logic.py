from datetime import date
from pathlib import Path

from hunter.anomaly import neighbor_flags, robust_drop
from hunter.combine import build_trips, internal_route
from hunter.config import load_config
from hunter.sweep import build_grid, shard_of

CFG = load_config(Path(__file__).resolve().parents[1] / "config.yaml")


def flat(a, b):
    return 1.0


def test_route_entry_and_exit_in_target_cities():
    cost, seq = internal_route("Paris", "Rome", ["Barcelona", "Paris", "Rome"], flat)
    assert cost == 2 and seq == ["Paris", "Barcelona", "Rome"]


def test_route_round_trip_needs_three_legs():
    cost, seq = internal_route("Paris", "Paris", ["Barcelona", "Paris", "Rome"], flat)
    assert cost == 3 and seq[0] == seq[-1] == "Paris"


def test_route_outside_gateway_adds_a_leg():
    cost, _ = internal_route("Lisbon", "Rome", ["Barcelona", "Paris", "Rome"], flat)
    assert cost == 3


def test_robust_drop_flags_clear_outlier_only():
    hist = [5000, 5100, 4900, 5050, 4950]
    assert robust_drop(3000, hist, 0.7, 3.0)[0]
    assert not robust_drop(4700, hist, 0.7, 3.0)[0]


def test_robust_drop_flat_history():
    flag, m, z = robust_drop(3000, [5000] * 5, 0.7, 3.0)
    assert flag and m == 5000 and z == float("inf")


def _row(kind, o, d, dout, price, dret=None, ts="2026-10-13T12:00:00+00:00"):
    return {"kind": kind, "origin": o, "dest": d, "date_out": dout, "date_ret": dret, "price": price,
            "airlines": "X", "stops": 1, "duration_min": 900, "url": "u", "ts": ts, "pax": 3,
            "qkey": f"{kind}|{o}|{d}|{dout}|{dret}|3"}


def test_neighbor_outlier():
    rows = [_row("ow", "GRU", "CDG", f"2027-01-{18 + i}", 9000) for i in range(8)]
    rows.append(_row("ow", "GRU", "CDG", "2027-01-30", 4000))
    flagged = [r for r, _ in neighbor_flags(rows, 0.6)]
    assert len(flagged) == 1 and flagged[0]["price"] == 4000


def test_open_jaw_combination_and_stay_filter():
    rows = [
        _row("ow", "GRU", "CDG", "2027-01-20", 6000),
        _row("ow", "FCO", "GRU", "2027-02-04", 5000),   # 15 days, valid
        _row("ow", "FCO", "GRU", "2027-02-10", 1000),   # 21 days, outside stay range
    ]
    trips = build_trips(CFG, rows)
    assert len(trips) == 1
    t = trips[0]
    assert t.stay == 15 and t.flights_total == 11000
    assert t.internal_total == 2 * CFG.internal_default_cost * CFG.passengers
    assert t.route == ["Paris", "Barcelona", "Rome"]


def test_grid_and_shards_cover_everything_once():
    grid = build_grid(CFG)
    keys = [r.key() for r in grid]
    assert len(keys) == len(set(keys))
    assert sum(1 for r in grid if shard_of(r, CFG.shards) in range(CFG.shards)) == len(grid)
    assert min(CFG.return_dates()) == date(2027, 1, 16)
    assert not [r for r in build_grid(CFG, today=date(2027, 1, 20)) if r.date_out < date(2027, 1, 22)]

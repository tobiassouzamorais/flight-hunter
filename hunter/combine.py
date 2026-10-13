"""Combine scraped legs into complete trips and rank them by total cost.

A complete trip is either
  * open-jaw: one-way Brazil -> entry gateway, plus one-way exit gateway -> Brazil, or
  * round trip: a single round-trip ticket to one gateway.

The ranking cost is the flight total for all passengers plus an ESTIMATE of the intra-Europe
legs needed to visit every city in `must_visit` between entry and exit. That estimate is
deliberately simple (leg count times a configurable price) and exists only so that, say,
entering via Lisbon is not unfairly favored over entering via Barcelona.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import permutations

from .config import Config
from .scraper import multicity_url


def internal_route(entry_city: str, exit_city: str, must_visit: list[str], leg_cost) -> tuple[float, list[str]]:
    """Cheapest city sequence from entry to exit visiting all `must_visit` cities.

    Returns (cost per person, sequence). Consecutive repeats are merged, so entering at a
    city that must be visited costs nothing extra. Brute force is fine: 3 cities -> 6 orders.
    """
    best_cost, best_seq = float("inf"), []
    for perm in permutations(must_visit):
        seq = [entry_city, *perm, exit_city]
        merged = [seq[0]]
        for c in seq[1:]:
            if c != merged[-1]:
                merged.append(c)
        cost = sum(leg_cost(a, b) for a, b in zip(merged, merged[1:]))
        if cost < best_cost:
            best_cost, best_seq = cost, merged
    return best_cost, best_seq


@dataclass
class Trip:
    kind: str                  # "open-jaw" or "round-trip"
    origin: str
    entry: str
    date_out: date
    exit: str
    home: str
    date_ret: date
    flights_total: float
    internal_total: float
    route: list[str]
    airlines_out: str
    airlines_ret: str
    stops_out: int | None
    stops_ret: int | None
    dur_out_min: int | None
    dur_ret_min: int | None
    links: list[str] = field(default_factory=list)
    quote_ts: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.flights_total + self.internal_total

    @property
    def stay(self) -> int:
        return (self.date_ret - self.date_out).days

    def label(self) -> str:
        return (f"{self.origin}->{self.entry} {self.date_out:%d/%m} | "
                f"{self.exit}->{self.home} {self.date_ret:%d/%m} ({self.stay}d, {self.kind})")


def _d(s: str) -> date:
    return date.fromisoformat(s)


def build_trips(cfg: Config, rows) -> list[Trip]:
    gateways, homes = set(cfg.gateways), set(cfg.origins)
    pax = cfg.passengers
    route_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}

    def route(entry: str, exit_: str):
        k = (entry, exit_)
        if k not in route_cache:
            route_cache[k] = internal_route(cfg.city(entry), cfg.city(exit_), cfg.must_visit, cfg.leg_cost)
        return route_cache[k]

    outs = [r for r in rows if r["kind"] == "ow" and r["origin"] in homes and r["dest"] in gateways]
    rets = [r for r in rows if r["kind"] == "ow" and r["origin"] in gateways and r["dest"] in homes]
    rts = [r for r in rows if r["kind"] == "rt"]

    rets_by_date: dict[date, list] = {}
    for r in rets:
        rets_by_date.setdefault(_d(r["date_out"]), []).append(r)

    trips: list[Trip] = []
    for o in outs:
        d1 = _d(o["date_out"])
        for stay in cfg.stays():
            for r in rets_by_date.get(d1.fromordinal(d1.toordinal() + stay), []):
                if r["dest"] != o["origin"] and not cfg.allow_mixed_home_airports:
                    continue
                cost_pp, seq = route(o["dest"], r["origin"])
                d2 = _d(r["date_out"])
                trips.append(Trip(
                    kind="open-jaw", origin=o["origin"], entry=o["dest"], date_out=d1,
                    exit=r["origin"], home=r["dest"], date_ret=d2,
                    flights_total=o["price"] + r["price"], internal_total=cost_pp * pax, route=seq,
                    airlines_out=o["airlines"], airlines_ret=r["airlines"],
                    stops_out=o["stops"], stops_ret=r["stops"],
                    dur_out_min=o["duration_min"], dur_ret_min=r["duration_min"],
                    links=[o["url"], r["url"], multicity_url(
                        o["origin"], o["dest"], d1, r["origin"], r["dest"], d2,
                        pax, cfg.currency, cfg.language)],
                    quote_ts=[o["ts"], r["ts"]],
                ))
    for t in rts:
        cost_pp, seq = route(t["dest"], t["dest"])
        trips.append(Trip(
            kind="round-trip", origin=t["origin"], entry=t["dest"], date_out=_d(t["date_out"]),
            exit=t["dest"], home=t["origin"], date_ret=_d(t["date_ret"]),
            flights_total=t["price"], internal_total=cost_pp * pax, route=seq,
            airlines_out=t["airlines"], airlines_ret="(same ticket)",
            stops_out=t["stops"], stops_ret=None, dur_out_min=t["duration_min"], dur_ret_min=None,
            links=[t["url"]], quote_ts=[t["ts"]],
        ))
    trips.sort(key=lambda x: x.total)
    return trips


def diverse_top(trips: list[Trip], n: int = 15, per_pair: int = 2) -> list[Trip]:
    """Top trips, but at most `per_pair` per (entry, exit, kind) so the list shows real alternatives."""
    seen: dict[tuple, int] = {}
    out = []
    for t in trips:
        k = (t.entry, t.exit, t.kind)
        if seen.get(k, 0) >= per_pair:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(t)
        if len(out) >= n:
            break
    return out

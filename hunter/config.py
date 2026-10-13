"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml


@dataclass
class Config:
    passengers: int
    currency: str
    language: str
    origins: list[str]
    allow_mixed_home_airports: bool
    gateways: dict[str, str]
    must_visit: list[str]
    outbound_from: date
    outbound_to: date
    stay_min_days: int
    stay_max_days: int
    max_stops: int
    max_duration_hours: float
    hide_self_transfer: bool

    internal_default_cost: float
    internal_pair_cost: dict[frozenset, float]

    shards: int
    hours_per_shard: int
    delay_seconds: tuple[float, float]
    retries: int
    roundtrip_gateways: list[str]
    roundtrip_origins: list[str]
    freshness_hours: float
    stop_after: date

    max_flights_total: float
    history_days: int
    min_history: int
    ratio_vs_history: float
    robust_z: float
    ratio_vs_neighbors: float
    new_best_drop: float
    cooldown_hours: float

    feeds: list[str] = field(default_factory=list)
    place_keywords: list[str] = field(default_factory=list)
    origin_keywords: list[str] = field(default_factory=list)
    boost_keywords: list[str] = field(default_factory=list)

    # Derived helpers -------------------------------------------------------

    def outbound_dates(self) -> list[date]:
        n = (self.outbound_to - self.outbound_from).days
        return [self.outbound_from + timedelta(days=i) for i in range(n + 1)]

    def return_dates(self) -> list[date]:
        first = self.outbound_from + timedelta(days=self.stay_min_days)
        last = self.outbound_to + timedelta(days=self.stay_max_days)
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    def stays(self) -> list[int]:
        return list(range(self.stay_min_days, self.stay_max_days + 1))

    def city(self, airport: str) -> str:
        return self.gateways.get(airport, airport)

    def leg_cost(self, a: str, b: str) -> float:
        return self.internal_pair_cost.get(frozenset((a, b)), self.internal_default_cost)


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    t, il, s, a = raw["trip"], raw.get("internal_legs", {}), raw["search"], raw["alerts"]
    d = raw.get("deals", {})

    pair_cost = {}
    for k, v in (il.get("pair_cost_per_person") or {}).items():
        x, y = (p.strip() for p in k.split("|"))
        pair_cost[frozenset((x, y))] = float(v)

    cfg = Config(
        passengers=int(t["passengers"]),
        currency=t.get("currency", "BRL"),
        language=t.get("language", "en-US"),
        origins=[o.upper() for o in t["origins"]],
        allow_mixed_home_airports=bool(t.get("allow_mixed_home_airports", True)),
        gateways={k.upper(): v for k, v in t["gateways"].items()},
        must_visit=list(t["must_visit"]),
        outbound_from=_as_date(t["outbound_from"]),
        outbound_to=_as_date(t["outbound_to"]),
        stay_min_days=int(t["stay_min_days"]),
        stay_max_days=int(t["stay_max_days"]),
        max_stops=int(t.get("max_stops", 2)),
        max_duration_hours=float(t.get("max_duration_hours", 30)),
        hide_self_transfer=bool(t.get("hide_self_transfer", True)),
        internal_default_cost=float(il.get("default_cost_per_person", 450)),
        internal_pair_cost=pair_cost,
        shards=max(1, int(s.get("shards", 1))),
        hours_per_shard=max(1, int(s.get("hours_per_shard", 3))),
        delay_seconds=tuple(s.get("delay_seconds", [2.5, 6.0])),
        retries=int(s.get("retries", 3)),
        roundtrip_gateways=[g.upper() for g in s.get("roundtrip_gateways", [])],
        roundtrip_origins=[o.upper() for o in s.get("roundtrip_origins", [])],
        freshness_hours=float(s.get("freshness_hours", 36)),
        stop_after=_as_date(s.get("stop_after", "2099-01-01")),
        max_flights_total=float(a.get("max_flights_total", a.get("target_total", 0))),
        history_days=int(a.get("history_days", 14)),
        min_history=int(a.get("min_history", 4)),
        ratio_vs_history=float(a.get("ratio_vs_history", 0.7)),
        robust_z=float(a.get("robust_z", 3.0)),
        ratio_vs_neighbors=float(a.get("ratio_vs_neighbors", 0.6)),
        new_best_drop=float(a.get("new_best_drop", 0.05)),
        cooldown_hours=float(a.get("cooldown_hours", 24)),
        feeds=list(d.get("feeds", [])),
        place_keywords=[k.lower() for k in d.get("place_keywords", [])],
        origin_keywords=[k.lower() for k in d.get("origin_keywords", [])],
        boost_keywords=[k.lower() for k in d.get("boost_keywords", [])],
    )

    if cfg.stay_min_days > cfg.stay_max_days:
        raise ValueError("stay_min_days must not exceed stay_max_days")
    if cfg.outbound_from > cfg.outbound_to:
        raise ValueError("outbound_from must not be after outbound_to")
    missing = [c for c in cfg.must_visit if c not in cfg.gateways.values()]
    if missing:
        # Not fatal: a city can be visited without being a gateway, but it is usually a typo.
        print(f"[config] note: must_visit cities without a gateway airport: {missing}")
    return cfg

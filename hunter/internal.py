"""Intra-Europe leg of the trip: base city, then two cities, then back to the base.

The long-haul tickets are already bought (into and out of the base city). This mode searches
the three one-way flights that remain, for both visiting orders:

    base -> A on day d1,   A -> B on day d2,   B -> base on day d3

"N full days" in a city means N whole days on the ground with no flying: arriving on day D
and leaving on day D + N + 1 gives exactly N full days (D+1 ... D+N).

Constraints come from the long-haul tickets: the first internal flight leaves at least
`min_hours_after_arrival` after landing at the base, and the last one lands at the base at
least one calendar day before the long-haul departure, so a delay on a separate ticket cannot
cost the flight home.

Each leg is priced as its own one-way ticket for all passengers together. Different airports
of the same city (CDG and ORY, say) are interchangeable: each leg uses whichever is cheapest.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import permutations
from pathlib import Path

import yaml

from . import notify
from .db import Store, utcnow
from .report import money
from .scraper import QuoteRequest, ScrapeError, fetch, polite_pause

CITY_CODES = {"Paris": "PAR", "Rome": "ROM", "Barcelona": "BCN", "Milan": "MIL", "London": "LON",
              "Madrid": "MAD", "Lisbon": "LIS"}


@dataclass
class InternalConfig:
    pax: int
    currency: str
    language: str
    base_city: str
    base_airports: list[str]
    cities: dict[str, list[str]]
    full_days_min: int
    full_days_max: int
    arrive_base: datetime
    depart_base: datetime
    min_hours_after_arrival: float
    max_stops: int
    alert_below_total: float | None
    new_best_drop: float
    cooldown_hours: float
    freshness_hours: float
    delay_seconds: tuple[float, float]
    retries: int
    stop_after: date

    @property
    def first_leg_earliest_hour(self) -> int:
        t = self.arrive_base + timedelta(hours=self.min_hours_after_arrival)
        return t.hour + (1 if t.minute else 0)

    @property
    def last_return_date(self) -> date:
        return self.depart_base.date() - timedelta(days=1)


def load_internal(path: str | Path) -> InternalConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    i, s, a = raw["internal"], raw.get("search", {}), raw.get("alerts", {})
    t = raw.get("trip", {})
    return InternalConfig(
        pax=int(i["passengers"]),
        currency=t.get("currency", "BRL"),
        language=t.get("language", "en-US"),
        base_city=i["base"]["city"],
        base_airports=[x.upper() for x in i["base"]["airports"]],
        cities={c: [x.upper() for x in aps] for c, aps in i["cities"].items()},
        full_days_min=int(i["full_days"]["min"]),
        full_days_max=int(i["full_days"]["max"]),
        arrive_base=datetime.fromisoformat(str(i["arrive_base"])),
        depart_base=datetime.fromisoformat(str(i["depart_base"])),
        min_hours_after_arrival=float(i.get("min_hours_after_arrival", 5)),
        max_stops=int(i.get("max_stops", 0)),
        alert_below_total=(float(i["alert_below_total"]) if i.get("alert_below_total") else None),
        new_best_drop=float(a.get("new_best_drop", 0.05)),
        cooldown_hours=float(a.get("cooldown_hours", 24)),
        freshness_hours=float(s.get("freshness_hours", 36)),
        delay_seconds=tuple(s.get("delay_seconds", [2.5, 6.0])),
        retries=int(s.get("retries", 3)),
        stop_after=date.fromisoformat(str(s.get("stop_after", "2099-01-01"))),
    )


@dataclass
class Plan:
    """One schedule: visiting order and the three flight dates."""
    order: tuple[str, str]
    d1: date
    d2: date
    d3: date

    def legs(self, cfg: InternalConfig) -> list[tuple[str, str, date]]:
        a, b = self.order
        return [(cfg.base_city, a, self.d1), (a, b, self.d2), (b, cfg.base_city, self.d3)]

    def label(self) -> str:
        a, b = self.order
        return f"{a} {self.d1:%d/%m} to {self.d2:%d/%m}, {b} {self.d2:%d/%m} to {self.d3:%d/%m}"


def plans(cfg: InternalConfig) -> list[Plan]:
    out = []
    first = cfg.arrive_base.date()
    span = range(cfg.full_days_min + 1, cfg.full_days_max + 2)   # days between arrival and departure
    for order in permutations(cfg.cities, 2):
        d1 = first
        while d1 <= cfg.last_return_date:
            for na in span:
                for nb in span:
                    d2, d3 = d1 + timedelta(days=na), d1 + timedelta(days=na + nb)
                    if d3 <= cfg.last_return_date:
                        out.append(Plan(order, d1, d2, d3))
            d1 += timedelta(days=1)
    return out


def airports(cfg: InternalConfig, city: str) -> list[str]:
    return cfg.base_airports if city == cfg.base_city else cfg.cities[city]


def leg_requests(cfg: InternalConfig, o_city: str, d_city: str, day: date) -> list[QuoteRequest]:
    hour = cfg.first_leg_earliest_hour if (o_city == cfg.base_city and day == cfg.arrive_base.date()) else None
    return [QuoteRequest("ow", o, d, day, None, cfg.pax, hour)
            for o in airports(cfg, o_city) for d in airports(cfg, d_city)]


def build_grid(cfg: InternalConfig, today: date | None = None) -> list[QuoteRequest]:
    first = (today or utcnow().date()) + timedelta(days=2)
    seen, out = set(), []
    for p in plans(cfg):
        for o, d, day in p.legs(cfg):
            if day < first:
                continue
            for r in leg_requests(cfg, o, d, day):
                if r.key() not in seen:
                    seen.add(r.key())
                    out.append(r)
    return out


def sweep(cfg: InternalConfig, store: Store, log=print) -> dict:
    import time
    grid = build_grid(cfg)
    stats = {"requests": len(grid), "ok": 0, "empty": 0, "failed": 0}
    log(f"[internal] {len(grid)} searches")
    t0, consecutive = time.time(), 0
    for req in grid:
        try:
            its = fetch(req, cfg.currency, cfg.language, cfg.max_stops, 30, cfg.retries, True)
            if its:
                store.add_quotes(req, its, utcnow())
                stats["ok"] += 1
            else:
                stats["empty"] += 1
            consecutive = 0
        except ScrapeError:
            # Google answers routes without nonstop flights with a page fast-flights cannot parse,
            # so a failure here often just means "no flight"; it is counted separately anyway.
            stats["failed"] += 1
            consecutive += 1
            if consecutive >= 8:
                log("[internal] 8 consecutive failures, stopping early")
                break
        polite_pause(cfg.delay_seconds)
    store.add_run(utcnow(), -1, stats["requests"], stats["ok"], stats["empty"], stats["failed"])
    log(f"[internal] done ok={stats['ok']} empty={stats['empty']} failed={stats['failed']} "
        f"in {time.time() - t0:.0f}s")
    return stats


@dataclass
class LegQuote:
    origin: str
    dest: str
    day: date
    price: int
    airlines: str
    dep: str
    arr: str
    url: str


@dataclass
class Option:
    plan: Plan
    legs: list[LegQuote] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(q.price for q in self.legs)


def _leg_quote(row) -> LegQuote:
    segs = json.loads(row["legs"]) if row["legs"] else []
    dep = segs[0]["dep"][-5:] if segs else "?"
    arr = segs[-1]["arr"][-5:] if segs else "?"
    return LegQuote(row["origin"], row["dest"], date.fromisoformat(row["date_out"]), row["price"],
                    row["airlines"] or "", dep, arr, row["url"] or "")


def options(cfg: InternalConfig, store: Store) -> list[Option]:
    rows = store.latest_cheapest(utcnow() - timedelta(hours=cfg.freshness_hours), cfg.pax)
    by_key = {r["qkey"]: r for r in rows}
    out = []
    for p in plans(cfg):
        opt, complete = Option(p), True
        for o, d, day in p.legs(cfg):
            found = [by_key[r.key()] for r in leg_requests(cfg, o, d, day) if r.key() in by_key]
            if not found:
                complete = False
                break
            opt.legs.append(_leg_quote(min(found, key=lambda r: r["price"])))
        if complete:
            out.append(opt)
    out.sort(key=lambda x: x.total)
    return out


def kayak_link(cfg: InternalConfig, p: Plan) -> str:
    parts = [f"{CITY_CODES.get(o, o[:3].upper())}-{CITY_CODES.get(d, d[:3].upper())}/{day:%Y-%m-%d}"
             for o, d, day in p.legs(cfg)]
    return f"https://www.kayak.com.br/flights/{'/'.join(parts)}/{cfg.pax}adults?sort=price_a"


def describe(cfg: InternalConfig, opt: Option) -> str:
    lines = [f"{opt.plan.label()}",
             f"Total {money(opt.total)} {cfg.currency} for {cfg.pax} "
             f"({money(opt.total / cfg.pax)} per person), base fares without checked bags."]
    for q in opt.legs:
        lines.append(f"- {q.day:%a %d/%m} {q.origin}-{q.dest} {q.dep}-{q.arr} {q.airlines}: "
                     f"{money(q.price)} | {q.url.replace('&hl=en-US', '&hl=pt-BR')}")
    lines.append(f"- All three legs on Kayak: {kayak_link(cfg, opt.plan)}")
    return "\n".join(lines)


def _prev_best(store: Store) -> float | None:
    store.con.execute("CREATE TABLE IF NOT EXISTS best_internal (ts TEXT, total REAL, description TEXT)")
    r = store.con.execute("SELECT MIN(total) FROM best_internal").fetchone()
    return r[0] if r and r[0] is not None else None


def analyze(cfg: InternalConfig, store: Store, stats: dict | None, report_path="reports/latest.md"):
    opts = options(cfg, store)
    messages = []
    if opts:
        best = opts[0]
        prev = _prev_best(store)
        if prev is None or best.total < prev * (1 - cfg.new_best_drop):
            store.con.execute("INSERT INTO best_internal VALUES (?,?,?)",
                              (utcnow().isoformat(), best.total, best.plan.label()))
            store.con.commit()
            head = ("First result for the flights inside Europe" if prev is None else
                    f"New best for the flights inside Europe ({100 * (1 - best.total / prev):.0f}% "
                    f"below previous best {money(prev)})")
            messages.append(f"{head}:\n{describe(cfg, best)}")
        if cfg.alert_below_total and best.total < cfg.alert_below_total:
            akey = f"internal-threshold|{best.plan.label()}|{best.total}"
            if not store.recently_alerted(akey, cfg.cooldown_hours):
                store.add_alert(akey, "internal threshold")
                messages.append(f"Below {money(cfg.alert_below_total)}:\n{describe(cfg, best)}")
    if stats and stats["requests"] >= 10 and stats["failed"] / stats["requests"] >= 0.5:
        if not store.recently_alerted("health|internal", 24):
            store.add_alert("health|internal", "scraper health")
            messages.append(f"Scraper health: {stats['failed']}/{stats['requests']} searches failed.")

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(render(cfg, stats, opts), encoding="utf-8")
    if messages:
        notify.send(f"Flight Hunter (Europe legs): {len(messages)} alert(s)", "\n\n".join(messages))
    if opts:
        print(f"[internal] best: {opts[0].plan.label()} total {money(opts[0].total)}")
    return opts


def render(cfg: InternalConfig, stats, opts: list[Option]) -> str:
    a, b = list(cfg.cities)[:2]
    out = [f"# Flight Hunter report: flights inside Europe\n",
           f"Generated {utcnow():%Y-%m-%d %H:%M} UTC. {cfg.pax} adults, arriving in {cfg.base_city} "
           f"{cfg.arrive_base:%d/%m %H:%M} and flying home {cfg.depart_base:%d/%m %H:%M} (local time). "
           f"Visiting {a} and {b} with {cfg.full_days_min} to {cfg.full_days_max} full days each. "
           f"First internal flight leaves at or after {cfg.first_leg_earliest_hour}:00 on "
           f"{cfg.arrive_base:%d/%m}; the last one lands in {cfg.base_city} by {cfg.last_return_date:%d/%m}."]
    if stats:
        out.append(f"\nLast sweep: {stats['requests']} searches, {stats['ok']} with results, "
                   f"{stats['empty']} empty, {stats['failed']} failed.")
    out.append("\nPrices are base fares for all passengers together, without checked bags. "
               "Low-cost carriers (Vueling, Ryanair, easyJet) charge bags separately, so compare "
               "with the bag fee added before buying.\n")
    out.append("## Best schedules\n")
    if not opts:
        out.append("No complete schedule priced yet.")
    else:
        out.append(f"| # | Schedule | Nights in {cfg.base_city} | Total | Per person | Leg 1 | Leg 2 | Leg 3 | Kayak |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        seen = set()
        n = 0
        for o in opts:
            k = (o.plan.order, o.plan.d1)
            if k in seen:
                continue
            seen.add(k)
            n += 1
            before = (o.plan.d1 - cfg.arrive_base.date()).days
            after = (cfg.depart_base.date() - o.plan.d3).days
            cells = [f"[{q.origin}-{q.dest} {q.day:%d/%m} {q.dep} {q.airlines} {money(q.price)}]"
                     f"({q.url.replace('&hl=en-US', '&hl=pt-BR')})" for q in o.legs]
            out.append(f"| {n} | {o.plan.label()} | {before} before, {after} after | "
                       f"**{money(o.total)}** | {money(o.total / cfg.pax)} | {' | '.join(cells)} | "
                       f"[link]({kayak_link(cfg, o.plan)}) |")
            if n >= 12:
                break
        out.append(f"\nAll values in {cfg.currency}.")
    return "\n".join(out) + "\n"


def run(config_path: str, store: Store, log=print):
    cfg = load_internal(config_path)
    stats = None
    if utcnow().date() > cfg.stop_after:
        log(f"[internal] stop_after ({cfg.stop_after}) has passed; not scraping")
    else:
        stats = sweep(cfg, store, log)
    return analyze(cfg, store, stats)

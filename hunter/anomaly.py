"""Detection of abnormally low fares (candidate error fares or flash sales).

Two independent tests run on each leg quote (cheapest result of a search):

1. Time series. Compare the price with its own history over the last `history_days`.
   With median m and median absolute deviation MAD, the robust z-score is
       z = (m - p) / (1.4826 * MAD),
   where 1.4826 makes MAD a consistent estimator of the standard deviation for normal data.
   Flag when p <= ratio_vs_history * m AND z >= robust_z. Both conditions are required:
   the ratio alone fires on naturally volatile routes, the z-score alone fires on tiny
   absolute moves of very stable routes. MAD = 0 (flat history) is treated as z = infinity
   whenever p < m, leaving the ratio as the binding condition.

2. Cross section. Compare the price with the median of the same route (same direction,
   same stay length for round trips) on the other dates of the window. A single date far
   below its neighbors is the classic signature of a mispriced fare, and this test works
   from the very first sweep, before any history exists.

Limitation: Google Flights shows what its partners return at scrape time. A price that is
genuinely low on a date with little demand also passes these tests; the alert is a prompt
to look, not proof of an error fare.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from .links import fmt, leg_links


def robust_drop(price: float, hist: list[float], ratio: float, z_min: float) -> tuple[bool, float, float]:
    """Return (flag, median, z)."""
    m = median(hist)
    mad = median(abs(h - m) for h in hist)
    if mad == 0:
        z = float("inf") if price < m else 0.0
    else:
        z = (m - price) / (1.4826 * mad)
    return (price <= ratio * m and z >= z_min), m, z


def neighbor_flags(rows, ratio: float, min_neighbors: int = 5) -> list[tuple[dict, float]]:
    """Rows whose price is <= ratio * median of the same route on other dates."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        stay = None
        if r["kind"] == "rt":
            stay = (date.fromisoformat(r["date_ret"]) - date.fromisoformat(r["date_out"])).days
        groups[(r["kind"], r["origin"], r["dest"], stay)].append(r)
    out = []
    for g in groups.values():
        if len(g) <= min_neighbors:
            continue
        for r in g:
            others = [x["price"] for x in g if x is not r]
            m = median(others)
            if r["price"] <= ratio * m:
                out.append((r, m))
    return out


def describe(r) -> str:
    if r["kind"] == "rt":
        return f"round trip {r['origin']}<->{r['dest']} {r['date_out']} to {r['date_ret']}"
    return f"one-way {r['origin']}->{r['dest']} {r['date_out']}"


def leg_anomalies(cfg, store, rows, now: datetime) -> list[tuple[str, str]]:
    """Return (alert_key, message) pairs."""
    alerts = []
    since = now - timedelta(days=cfg.history_days)
    for r in rows:
        hist = store.history(r["qkey"], since, datetime.fromisoformat(r["ts"]))
        if len(hist) >= cfg.min_history:
            flag, m, z = robust_drop(r["price"], hist, cfg.ratio_vs_history, cfg.robust_z)
            if flag:
                pct = 100 * (1 - r["price"] / m)
                alerts.append((f"hist|{r['qkey']}|{r['price']}",
                               f"Price drop: {describe(r)} now {r['price']:,} {cfg.currency} "
                               f"for {r['pax']} pax, {pct:.0f}% below its {len(hist)}-sample median "
                               f"({m:,.0f}), z={z:.1f}. {r['airlines']}\n{fmt(leg_links(r))}"))
    for r, m in neighbor_flags(rows, cfg.ratio_vs_neighbors):
        pct = 100 * (1 - r["price"] / m)
        alerts.append((f"nbr|{r['qkey']}|{r['price']}",
                       f"Outlier date: {describe(r)} at {r['price']:,} {cfg.currency} for {r['pax']} pax, "
                       f"{pct:.0f}% below the same route on other dates (median {m:,.0f}). "
                       f"{r['airlines']}\n{fmt(leg_links(r))}"))
    return alerts

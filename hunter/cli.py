"""Command line entry point.

    python -m hunter run [--shard auto|all|N] [--limit N]   scrape, scan deals, analyze, alert
    python -m hunter analyze                                analyze what is in the database
    python -m hunter deals                                  scan deal feeds only
    python -m hunter grid                                   show grid size per shard
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path

from . import notify
from .anomaly import leg_anomalies
from .combine import build_trips, diverse_top
from .config import load_config
from .db import Store, utcnow
from .deals import scan_feeds
from .report import money, render
from .scraper import QuoteRequest, ScrapeError, fetch, polite_pause
from .sweep import auto_shard, build_grid, run_sweep, shard_of


def bucket_check(cfg, trips, max_trips: int = 3) -> list[str]:
    """Re-price the legs of the best trips for ONE passenger.

    Airlines sell seats in fare classes with limited inventory. If only one or two seats are
    left in the cheapest class, a 3-passenger search prices everyone in the next class up,
    and buying in separate bookings on the same flights can be cheaper.
    """
    notes = []
    for t in trips[:max_trips]:
        if t.kind == "open-jaw":
            legs = [QuoteRequest("ow", t.origin, t.entry, t.date_out, None, 1),
                    QuoteRequest("ow", t.exit, t.home, t.date_ret, None, 1)]
        else:
            legs = [QuoteRequest("rt", t.origin, t.entry, t.date_out, t.date_ret, 1)]
        try:
            single = 0
            for q in legs:
                its = fetch(q, cfg.currency, cfg.language, cfg.max_stops, cfg.max_duration_hours, 2,
                            cfg.hide_self_transfer)
                if not its:
                    raise ScrapeError("no single-passenger result")
                single += its[0].price
                polite_pause(cfg.delay_seconds)
        except ScrapeError as e:
            notes.append(f"{t.label()}: could not re-price for 1 passenger ({e}).")
            continue
        scaled = single * cfg.passengers
        if scaled < 0.97 * t.flights_total:
            notes.append(f"{t.label()}: 1 passenger costs {money(single)}, so {cfg.passengers} x 1 = "
                         f"{money(scaled)} vs {money(t.flights_total)} quoted for {cfg.passengers} together. "
                         f"Cheapest fare class probably has fewer than {cfg.passengers} seats left; "
                         f"try buying in separate bookings (check the cheapest one first).")
        else:
            notes.append(f"{t.label()}: consistent ({money(single)} per person alone).")
    return notes


def analyze(cfg, store, stats=None, deals=(), do_bucket_check=False, report_path="reports/latest.md"):
    now = utcnow()
    rows = store.latest_cheapest(now - timedelta(hours=cfg.freshness_hours), cfg.passengers)
    trips = build_trips(cfg, rows)
    top = diverse_top(trips, n=15)

    messages: list[str] = []

    # 1) leg-level anomalies (only for quotes scraped in this run, to avoid re-reporting)
    fresh = [r for r in rows if stats is None or r["ts"] >= (now - timedelta(hours=2)).isoformat()]
    anomalies = []
    for akey, msg in leg_anomalies(cfg, store, fresh, now):
        anomalies.append(msg)
        if not store.recently_alerted(akey, cfg.cooldown_hours):
            store.add_alert(akey, msg)
            messages.append(msg)

    # 2) a meaningful new best complete trip (flights + internal estimate)
    if top:
        best = top[0]
        desc = (f"{best.label()}\nTotal {money(best.total)} {cfg.currency} for {cfg.passengers} "
                f"({money(best.total / cfg.passengers)} per person), of which flights "
                f"{money(best.flights_total)} and internal legs estimate {money(best.internal_total)}.\n"
                f"Airlines: {best.airlines_out} / {best.airlines_ret}\n" + "\n".join(best.links))
        prev = store.previous_best()
        if prev is None or best.total < prev * (1 - cfg.new_best_drop):
            store.add_best(best.total, best.label())
            if prev is not None:
                messages.append(f"New best trip ({100 * (1 - best.total / prev):.0f}% below previous "
                                f"best {money(prev)}):\n{desc}")

    # 2b) any trip whose international flights cost less than the user's threshold
    cheap = sorted((t for t in trips if t.flights_total < cfg.max_flights_total),
                   key=lambda t: t.flights_total)[:5]
    for t in cheap:
        akey = f"threshold|{t.label()}|{int(t.flights_total)}"
        if store.recently_alerted(akey, cfg.cooldown_hours):
            continue
        msg = (f"Flights below {money(cfg.max_flights_total)} {cfg.currency}: {t.label()}\n"
               f"Flights {money(t.flights_total)} for {cfg.passengers} "
               f"({money(t.flights_total / cfg.passengers)} per person). "
               f"Airlines: {t.airlines_out} / {t.airlines_ret}\n" + "\n".join(t.links))
        store.add_alert(akey, msg)
        messages.append(msg)

    # 3) deals
    for d in deals:
        messages.append(f"{'URGENT ' if d.urgent else ''}Deal post: {d.title}\n{d.link}")

    # 4) scraper health
    if stats and stats["requests"] >= 10 and stats["failed"] / stats["requests"] >= 0.5:
        akey = "health|scraper"
        if not store.recently_alerted(akey, 24):
            msg = (f"Scraper health: {stats['failed']}/{stats['requests']} searches failed. Google may be "
                   f"throttling the runner or fast-flights may need an update.")
            store.add_alert(akey, msg)
            messages.append(msg)

    bucket_notes = bucket_check(cfg, top) if (do_bucket_check and top) else []
    for b in bucket_notes:
        if "separate bookings" in b:
            akey = f"bucket|{b[:60]}"
            if not store.recently_alerted(akey, cfg.cooldown_hours):
                store.add_alert(akey, b)
                messages.append(b)

    text = render(cfg, now, stats, top, len(trips), len(rows), anomalies, deals, bucket_notes)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(text, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)

    if messages:
        notify.send(f"Flight Hunter: {len(messages)} alert(s)", "\n\n".join(messages))
    else:
        print("[analyze] no alerts")
    if top:
        print(f"[analyze] best: {top[0].label()} total {money(top[0].total)} {cfg.currency}")
    return top


def main(argv=None):
    p = argparse.ArgumentParser(prog="hunter")
    p.add_argument("command", choices=["run", "analyze", "deals", "grid", "test-alert"])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--db", default="data/hunter.sqlite")
    p.add_argument("--shard", default="auto", help="auto, all, or a shard number")
    p.add_argument("--limit", type=int, default=None, help="cap the number of searches (testing)")
    p.add_argument("--no-bucket-check", action="store_true")
    a = p.parse_args(argv)

    cfg = load_config(a.config)

    if a.command == "grid":
        grid = build_grid(cfg)
        c = Counter(shard_of(r, cfg.shards) for r in grid)
        print(f"total searches: {len(grid)}; per shard: {dict(sorted(c.items()))}")
        return

    if a.command == "test-alert":
        ok = notify.send("Flight Hunter: test alert",
                         "This is a test message from your Flight Hunter agent. If you are reading it, "
                         f"this alert channel works. You will be alerted when international flights for "
                         f"{cfg.passengers} people cost less than {money(cfg.max_flights_total)} {cfg.currency}.")
        raise SystemExit(0 if ok else 1)

    store = Store(a.db)
    try:
        if a.command == "deals":
            ds = scan_feeds(cfg, store)
            if ds:
                notify.send(f"Flight Hunter: {len(ds)} deal post(s)",
                            "\n\n".join(f"{d.title}\n{d.link}" for d in ds))
            else:
                print("[deals] nothing new and relevant")
            return
        if a.command == "analyze":
            analyze(cfg, store)
            return

        stats = None
        if utcnow().date() > cfg.stop_after:
            print(f"[run] stop_after ({cfg.stop_after}) has passed; not scraping")
        else:
            shard = None if a.shard == "all" else (auto_shard(cfg) if a.shard == "auto" else int(a.shard))
            stats = run_sweep(cfg, store, shard, a.limit)
        deals = scan_feeds(cfg, store)
        analyze(cfg, store, stats, deals, do_bucket_check=not a.no_bucket_check)
    finally:
        store.close()


if __name__ == "__main__":
    main()

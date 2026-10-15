"""Search grid generation, sharding and the scraping loop."""

from __future__ import annotations

import time
import zlib
from datetime import timedelta

from .config import Config
from .db import Store, utcnow
from .scraper import QuoteRequest, ScrapeError, fetch, polite_pause


def build_grid(cfg: Config, today=None) -> list[QuoteRequest]:
    reqs: list[QuoteRequest] = []
    pax = cfg.passengers
    # Dates already in the past (or tomorrow, too late to buy) are not searched.
    first = (today or utcnow().date()) + timedelta(days=2)
    for o in cfg.origins:
        for g in cfg.gateways:
            for d in [x for x in cfg.outbound_dates() if x >= first]:
                reqs.append(QuoteRequest("ow", o, g, d, None, pax))
    for g in cfg.gateways:
        for h in cfg.origins:
            for d in [x for x in cfg.return_dates() if x >= first]:
                reqs.append(QuoteRequest("ow", g, h, d, None, pax))
    for o in cfg.roundtrip_origins:
        for g in cfg.roundtrip_gateways:
            for d in [x for x in cfg.outbound_dates() if x >= first]:
                for s in cfg.stays():
                    reqs.append(QuoteRequest("rt", o, g, d, d + timedelta(days=s), pax))
    return reqs


def shard_of(req: QuoteRequest, shards: int) -> int:
    return zlib.crc32(req.key().encode()) % shards


def auto_shard(cfg: Config, store: Store | None = None) -> int:
    """Pick the shard whose data is oldest.

    GitHub delays and drops scheduled runs, so rotating by wall-clock hour can scrape the same
    shard twice and skip others. Choosing the least recently scraped shard keeps coverage even.
    """
    if store is not None:
        last = {r[0]: r[1] for r in store.con.execute(
            "SELECT shard, MAX(ts) FROM runs WHERE shard IS NOT NULL GROUP BY shard")}
        never = [s for s in range(cfg.shards) if s not in last]
        if never:
            return never[0]
        return min(last, key=lambda s: last[s])
    return int(time.time() // (3600 * cfg.hours_per_shard)) % cfg.shards


def run_sweep(cfg: Config, store: Store, shard: int | None, limit: int | None = None,
              log=print) -> dict:
    grid = build_grid(cfg)
    if shard is not None:
        grid = [r for r in grid if shard_of(r, cfg.shards) == shard]
    if limit:
        grid = grid[:limit]

    stats = {"requests": len(grid), "ok": 0, "empty": 0, "failed": 0, "errors": []}
    log(f"[sweep] shard={shard} requests={len(grid)}")
    t0 = time.time()
    consecutive_fail = 0
    for i, req in enumerate(grid):
        ts = utcnow()
        try:
            its = fetch(req, cfg.currency, cfg.language, cfg.max_stops,
                        cfg.max_duration_hours, cfg.retries, cfg.hide_self_transfer)
            if its:
                store.add_quotes(req, its, ts)
                stats["ok"] += 1
            else:
                stats["empty"] += 1
            consecutive_fail = 0
        except ScrapeError as e:
            stats["failed"] += 1
            stats["errors"].append(str(e))
            consecutive_fail += 1
            if consecutive_fail >= 8:
                log("[sweep] 8 consecutive failures, Google is probably throttling us; stopping early")
                break
        if (i + 1) % 25 == 0:
            log(f"[sweep] {i + 1}/{len(grid)} ok={stats['ok']} empty={stats['empty']} "
                f"failed={stats['failed']} elapsed={time.time() - t0:.0f}s")
        polite_pause(cfg.delay_seconds)

    store.add_run(utcnow(), shard, stats["requests"], stats["ok"], stats["empty"], stats["failed"])
    log(f"[sweep] done ok={stats['ok']} empty={stats['empty']} failed={stats['failed']} "
        f"in {time.time() - t0:.0f}s")
    return stats

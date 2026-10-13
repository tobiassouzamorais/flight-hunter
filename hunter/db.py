"""SQLite storage for price history, alerts and deal posts already seen."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,               -- UTC ISO timestamp of the scrape
    qkey TEXT NOT NULL,             -- QuoteRequest.key()
    kind TEXT NOT NULL,             -- ow | rt
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    date_out TEXT NOT NULL,
    date_ret TEXT,
    pax INTEGER NOT NULL,
    rank INTEGER NOT NULL,          -- 0 = cheapest result of that search
    price INTEGER NOT NULL,         -- total for all passengers
    airlines TEXT,
    stops INTEGER,
    duration_min INTEGER,
    legs TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS ix_quotes_key ON quotes(qkey, ts);
CREATE INDEX IF NOT EXISTS ix_quotes_route ON quotes(kind, origin, dest, ts);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    shard INTEGER,
    requests INTEGER,
    ok INTEGER,
    empty INTEGER,
    failed INTEGER
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    akey TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts_key ON alerts(akey, ts);

CREATE TABLE IF NOT EXISTS best (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    total REAL NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals_seen (
    link TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    title TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.commit()
        self.con.close()

    # quotes -----------------------------------------------------------------

    def add_quotes(self, req, itineraries, ts: datetime, keep: int = 1) -> None:
        rows = []
        for rank, it in enumerate(itineraries[:keep]):
            rows.append((iso(ts), req.key(), req.kind, req.origin, req.dest,
                         req.date_out.isoformat(),
                         req.date_ret.isoformat() if req.date_ret else None,
                         req.pax, rank, it.price, it.airlines, it.stops, it.duration_min,
                         it.legs, it.url))
        self.con.executemany(
            "INSERT INTO quotes(ts,qkey,kind,origin,dest,date_out,date_ret,pax,rank,price,"
            "airlines,stops,duration_min,legs,url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.con.commit()

    def prune(self, history_days: int, detail_days: int = 2) -> None:
        """Keep the database small enough to live in git.

        Quotes older than `history_days` are deleted. Quotes older than `detail_days` keep only
        what the anomaly test needs (price and keys); their segment details and links are dropped.
        """
        now = utcnow()
        self.con.execute("DELETE FROM quotes WHERE ts < ?", (iso(now - timedelta(days=history_days)),))
        self.con.execute("UPDATE quotes SET legs = NULL, url = NULL WHERE ts < ? AND legs IS NOT NULL",
                         (iso(now - timedelta(days=detail_days)),))
        self.con.execute("DELETE FROM alerts WHERE ts < ?", (iso(now - timedelta(days=30)),))
        self.con.commit()
        self.con.execute("VACUUM")

    def latest_cheapest(self, since: datetime, pax: int) -> list[sqlite3.Row]:
        """Most recent rank-0 quote for every search key scraped since `since`."""
        return self.con.execute(
            """SELECT q.* FROM quotes q
               JOIN (SELECT qkey, MAX(ts) AS mts FROM quotes
                     WHERE rank = 0 AND ts >= ? AND pax = ? GROUP BY qkey) m
                 ON q.qkey = m.qkey AND q.ts = m.mts
               WHERE q.rank = 0""", (iso(since), pax)).fetchall()

    def history(self, qkey: str, since: datetime, before: datetime) -> list[int]:
        return [r[0] for r in self.con.execute(
            "SELECT price FROM quotes WHERE qkey = ? AND rank = 0 AND ts >= ? AND ts < ?",
            (qkey, iso(since), iso(before)))]

    # runs ---------------------------------------------------------------------

    def add_run(self, ts, shard, requests, ok, empty, failed):
        self.con.execute("INSERT INTO runs(ts,shard,requests,ok,empty,failed) VALUES (?,?,?,?,?,?)",
                         (iso(ts), shard, requests, ok, empty, failed))
        self.con.commit()

    # alerts -------------------------------------------------------------------

    def recently_alerted(self, akey: str, hours: float) -> bool:
        since = utcnow() - timedelta(hours=hours)
        return self.con.execute("SELECT 1 FROM alerts WHERE akey = ? AND ts >= ? LIMIT 1",
                                (akey, iso(since))).fetchone() is not None

    def add_alert(self, akey: str, message: str):
        self.con.execute("INSERT INTO alerts(ts,akey,message) VALUES (?,?,?)",
                         (iso(utcnow()), akey, message))
        self.con.commit()

    # best itinerary history ------------------------------------------------------

    def previous_best(self) -> float | None:
        r = self.con.execute("SELECT MIN(total) FROM best").fetchone()
        return r[0] if r and r[0] is not None else None

    def add_best(self, total: float, description: str):
        self.con.execute("INSERT INTO best(ts,total,description) VALUES (?,?,?)",
                         (iso(utcnow()), total, description))
        self.con.commit()

    # deals -----------------------------------------------------------------------

    def deal_seen(self, link: str) -> bool:
        return self.con.execute("SELECT 1 FROM deals_seen WHERE link = ?", (link,)).fetchone() is not None

    def mark_deal(self, link: str, title: str):
        self.con.execute("INSERT OR IGNORE INTO deals_seen(link,ts,title) VALUES (?,?,?)",
                         (link, iso(utcnow()), title))
        self.con.commit()

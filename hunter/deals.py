"""Watch deal-site RSS feeds for promotions and error fares relevant to the trip."""

from __future__ import annotations

import calendar
import re
import time
import unicodedata
from dataclasses import dataclass

import feedparser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower())


@dataclass
class Deal:
    title: str
    link: str
    source: str
    published: float | None
    urgent: bool


def _matches(text: str, keywords: list[str]) -> bool:
    return any(re.search(r"\b" + re.escape(norm(k)) + r"\b", text) for k in keywords)


def scan_feeds(cfg, store, max_age_days: float = 3, log=print) -> list[Deal]:
    found: list[Deal] = []
    now = time.time()
    for url in cfg.feeds:
        try:
            feed = feedparser.parse(url, agent=UA)
        except Exception as e:
            log(f"[deals] {url}: {e}")
            continue
        status = feed.get("status")
        if not feed.entries:
            log(f"[deals] {url}: no entries (status {status})")
            continue
        for e in feed.entries:
            link = e.get("link") or ""
            if not link or store.deal_seen(link):
                continue
            title = e.get("title", "")
            store.mark_deal(link, title)
            ts = None
            if e.get("published_parsed"):
                ts = calendar.timegm(e.published_parsed)
            if ts is not None and now - ts > max_age_days * 86400:
                continue
            text = norm(f"{title} {e.get('summary', '')}")
            if _matches(text, cfg.place_keywords) and _matches(text, cfg.origin_keywords):
                found.append(Deal(title=title, link=link, source=feed.feed.get("title", url),
                                  published=ts, urgent=_matches(text, cfg.boost_keywords)))
    found.sort(key=lambda d: (not d.urgent, -(d.published or 0)))
    return found

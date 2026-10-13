"""Links that take the reader from an alert to the purchase.

None of the free data sources returns a direct checkout link, so each alert carries search
links that open the exact itinerary (route, dates, three adults) on sites where it can be
bought: Google Flights lists the airline and agency offers for the itinerary, Kayak and
Skyscanner compare online travel agencies that Google does not always show.
"""

from __future__ import annotations

from datetime import date

from .scraper import multicity_url


def _kayak(legs: list[tuple[str, str, date]], pax: int) -> str:
    if len(legs) == 2 and legs[0][:2] == legs[1][:2][::-1]:
        # plain round trip uses Kayak's canonical form ORIG-DEST/out-date/return-date
        (o, d, d1), (_, _, d2) = legs
        return f"https://www.kayak.com.br/flights/{o}-{d}/{d1:%Y-%m-%d}/{d2:%Y-%m-%d}/{pax}adults?sort=price_a"
    parts = [f"{o}-{d}/{dt:%Y-%m-%d}" for o, d, dt in legs]
    return f"https://www.kayak.com.br/flights/{'/'.join(parts)}/{pax}adults?sort=price_a"


def _skyscanner(o: str, d: str, d_out: date, d_ret: date | None, pax: int) -> str:
    path = f"{o.lower()}/{d.lower()}/{d_out:%y%m%d}/"
    if d_ret:
        path += f"{d_ret:%y%m%d}/"
    return f"https://www.skyscanner.com.br/transporte/passagens-aereas/{path}?adultsv2={pax}&currency=BRL"


def trip_links(t, pax: int, currency: str, language: str) -> list[tuple[str, str]]:
    """(label, url) pairs for a complete trip from combine.Trip."""
    if t.kind == "round-trip":
        return [
            ("Google Flights", t.links[0]),
            ("Kayak", _kayak([(t.origin, t.entry, t.date_out), (t.entry, t.origin, t.date_ret)], pax)),
            ("Skyscanner", _skyscanner(t.origin, t.entry, t.date_out, t.date_ret, pax)),
        ]
    return [
        ("Google Flights, single open-jaw ticket", multicity_url(
            t.origin, t.entry, t.date_out, t.exit, t.home, t.date_ret, pax, currency, language)),
        ("Kayak, single open-jaw ticket",
         _kayak([(t.origin, t.entry, t.date_out), (t.exit, t.home, t.date_ret)], pax)),
        ("Google Flights, outbound only", t.links[0]),
        ("Google Flights, return only", t.links[1]),
    ]


def leg_links(row) -> list[tuple[str, str]]:
    """(label, url) pairs for a single leg quote (a database row)."""
    pax, d_out = row["pax"], date.fromisoformat(row["date_out"])
    d_ret = date.fromisoformat(row["date_ret"]) if row["date_ret"] else None
    legs = [(row["origin"], row["dest"], d_out)]
    if d_ret:
        legs.append((row["dest"], row["origin"], d_ret))
    out = []
    if row["url"]:
        out.append(("Google Flights", row["url"]))
    out.append(("Kayak", _kayak(legs, pax)))
    out.append(("Skyscanner", _skyscanner(row["origin"], row["dest"], d_out, d_ret, pax)))
    return out


def fmt(links: list[tuple[str, str]]) -> str:
    # Google Flights pages open in Portuguese for the reader; the scraper itself keeps en-US.
    links = [(label, url.replace("&hl=en-US", "&hl=pt-BR")) for label, url in links]
    return "Buy / check prices:\n" + "\n".join(f"- {label}: {url}" for label, url in links)

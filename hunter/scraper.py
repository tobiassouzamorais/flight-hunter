"""Thin, defensive wrapper around fast-flights (Google Flights scraping).

Facts verified against fast-flights 3.1.0 on 2026-10-13:
  * The returned price is the TOTAL for all passengers in the query (3 adults gave exactly
    3x the 1-adult fare when all three fit in the same fare class).
  * One-way and round-trip searches parse correctly. For round trips the price is the full
    round-trip total and `flights` holds the outbound segments only.
  * Multi-city searches come back empty (Google loads them asynchronously), so open-jaw
    trips are priced here as two one-way tickets. The Google Flights multi-city URL is still
    generated so a human can check whether a single open-jaw ticket is cheaper.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.exceptions import FlightsNotFound

# Winter (Jan/Feb) UTC offsets, used only to turn local departure/arrival times into an
# elapsed travel time. Airports not listed fall back to the sum of segment durations.
UTC_OFFSET_WINTER = {
    "GRU": -3, "VCP": -3, "CGH": -3, "GIG": -3, "CNF": -3, "BSB": -3, "UDI": -3,
    "LIS": 0, "OPO": 0, "LHR": 0, "LGW": 0, "DUB": 0,
}
EUROPE_CET = {"BCN", "MAD", "CDG", "ORY", "FCO", "CIA", "MXP", "LIN", "BGY", "AMS",
              "FRA", "MUC", "ZRH", "BRU", "VIE", "NCE", "VCE", "BLQ", "NAP", "PMI", "AGP", "VLC"}


def utc_offset(code: str) -> int | None:
    if code in UTC_OFFSET_WINTER:
        return UTC_OFFSET_WINTER[code]
    if code in EUROPE_CET:
        return 1
    return None


@dataclass
class QuoteRequest:
    kind: str            # "ow" (one-way) or "rt" (round trip)
    origin: str
    dest: str
    date_out: date
    date_ret: date | None
    pax: int

    def key(self) -> str:
        r = self.date_ret.isoformat() if self.date_ret else "-"
        return f"{self.kind}|{self.origin}|{self.dest}|{self.date_out.isoformat()}|{r}|{self.pax}"


@dataclass
class Itinerary:
    price: int
    airlines: str
    stops: int
    duration_min: int | None
    legs: str            # compact JSON with the segments
    url: str


class ScrapeError(RuntimeError):
    pass


def build_query(req: QuoteRequest, currency: str, language: str, max_stops: int | None,
                hide_self_transfer: bool = True):
    flights = [FlightQuery(date=req.date_out.isoformat(), from_airport=req.origin,
                           to_airport=req.dest, max_stops=max_stops)]
    trip = "one-way"
    if req.kind == "rt":
        flights.append(FlightQuery(date=req.date_ret.isoformat(), from_airport=req.dest,
                                   to_airport=req.origin, max_stops=max_stops))
        trip = "round-trip"
    return create_query(flights=flights, trip=trip, passengers=Passengers(adults=req.pax),
                        currency=currency, language=language,
                        hide_separate_and_self_transfer=hide_self_transfer)


def multicity_url(origin: str, entry: str, d_out: date, exit_: str, home: str, d_ret: date,
                  pax: int, currency: str, language: str) -> str:
    """Google Flights link for a single open-jaw ticket, for manual verification."""
    q = create_query(
        flights=[FlightQuery(date=d_out.isoformat(), from_airport=origin, to_airport=entry),
                 FlightQuery(date=d_ret.isoformat(), from_airport=exit_, to_airport=home)],
        trip="multi-city", passengers=Passengers(adults=pax), currency=currency, language=language)
    return q.url()


def _elapsed_minutes(segs) -> int | None:
    first, last = segs[0], segs[-1]
    o1, o2 = utc_offset(first.from_airport.code), utc_offset(last.to_airport.code)
    if o1 is None or o2 is None:
        return sum(int(s.duration or 0) for s in segs) or None
    dep = datetime(*first.departure.date, *first.departure.time)
    arr = datetime(*last.arrival.date, *last.arrival.time)
    # local difference plus the offset difference gives true elapsed time
    return int((arr - dep).total_seconds() // 60 + (o1 - o2) * 60)


def _to_itinerary(f, url: str) -> Itinerary:
    segs = f.flights
    legs = [{
        "from": s.from_airport.code, "to": s.to_airport.code,
        "dep": "%04d-%02d-%02d %02d:%02d" % (*s.departure.date, *s.departure.time),
        "arr": "%04d-%02d-%02d %02d:%02d" % (*s.arrival.date, *s.arrival.time),
        "min": s.duration,
    } for s in segs]
    return Itinerary(
        price=int(f.price),
        airlines=", ".join(f.airlines),
        stops=max(0, len(segs) - 1),
        duration_min=_elapsed_minutes(segs) if segs else None,
        legs=json.dumps(legs, separators=(",", ":")),
        url=url,
    )


def fetch(req: QuoteRequest, currency: str, language: str, max_stops: int,
          max_duration_hours: float, retries: int = 3,
          hide_self_transfer: bool = True) -> list[Itinerary]:
    """Return itineraries sorted by price. Empty list means Google found no flights.

    Raises ScrapeError when every attempt failed for a technical reason (blocked,
    page format changed, network), so the caller can track the failure rate.
    """
    q = build_query(req, currency, language, max_stops, hide_self_transfer)
    url = q.url()
    last_err = None
    for attempt in range(retries):
        try:
            res = get_flights(q)
            its = [_to_itinerary(f, url) for f in res if f.price and f.price > 0]
            limit = max_duration_hours * 60
            its = [i for i in its if i.duration_min is None or i.duration_min <= limit]
            its.sort(key=lambda i: i.price)
            return its
        except FlightsNotFound:
            return []
        except Exception as e:  # parser IndexError/AttributeError usually means a throttled page
            last_err = e
            time.sleep((2 ** attempt) * 5 + random.uniform(0, 3))
    raise ScrapeError(f"{req.key()}: {type(last_err).__name__}: {last_err}")


def polite_pause(bounds: tuple[float, float]) -> None:
    time.sleep(random.uniform(*bounds))


def as_dict(it: Itinerary) -> dict:
    return asdict(it)

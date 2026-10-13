"""Markdown report with the current best trips, anomalies and deal posts."""

from __future__ import annotations

from .combine import Trip


def money(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def hours(m: int | None) -> str:
    return "?" if m is None else f"{m // 60}h{m % 60:02d}"


def trip_table(trips: list[Trip], cfg) -> str:
    cur, pax = cfg.currency, cfg.passengers
    lines = [
        f"| # | Out | Back | Stay | Flights ({pax} pax) | + internal est. | Total | Per person | Airlines out / back | Stops | Time out / back | Route in Europe | Links |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(trips, 1):
        links = " ".join(f"[{n}]({u})" for n, u in zip(
            ["out", "back", "open-jaw"] if t.kind == "open-jaw" else ["round trip"], t.links))
        stops = f"{t.stops_out}/{t.stops_ret if t.stops_ret is not None else '-'}"
        lines.append(
            f"| {i} | {t.origin} to {t.entry} {t.date_out:%a %d/%m} | {t.exit} to {t.home} {t.date_ret:%a %d/%m} "
            f"| {t.stay}d | {money(t.flights_total)} | {money(t.internal_total)} | **{money(t.total)}** "
            f"| {money(t.total / pax)} | {t.airlines_out} / {t.airlines_ret} | {stops} "
            f"| {hours(t.dur_out_min)} / {hours(t.dur_ret_min)} | {' > '.join(t.route)} | {links} |")
    return "\n".join(lines) + f"\n\nAll values in {cur}."


def render(cfg, generated_at, stats, top: list[Trip], n_trips: int, n_quotes: int,
           anomalies: list[str], deals, bucket_notes: list[str]) -> str:
    out = [f"# Flight Hunter report\n\nGenerated {generated_at:%Y-%m-%d %H:%M} UTC. "
           f"Trip: {cfg.passengers} adults from {', '.join(cfg.origins)}, outbound "
           f"{cfg.outbound_from:%d/%m/%Y} to {cfg.outbound_to:%d/%m/%Y}, stay "
           f"{cfg.stay_min_days} to {cfg.stay_max_days} days, visiting {', '.join(cfg.must_visit)}."]
    if stats:
        out.append(f"\nLast sweep: {stats['requests']} searches, {stats['ok']} with results, "
                   f"{stats['empty']} empty, {stats['failed']} failed.")
    out.append(f"\nFresh leg quotes in the database: {n_quotes}. Complete trips evaluated: {n_trips}.")

    out.append("\n## Best trips\n")
    if top:
        out.append("The internal column is an estimate of the intra-Europe flights you will buy later "
                   "(leg count times the configured price per person). It only serves to compare "
                   "entry and exit points fairly. Open-jaw trips are priced as two separate one-way "
                   "tickets; the open-jaw link opens the same itinerary as a single multi-city "
                   "ticket on Google Flights, which is sometimes cheaper and protects the connection.\n")
        out.append(trip_table(top, cfg))
    else:
        out.append("No complete trip yet. The grid is scraped in shards, so the first full picture "
                   "appears after all shards have run once.")

    if bucket_notes:
        out.append("\n## Fare class check (1 vs 3 passengers)\n")
        out.extend(f"- {b}" for b in bucket_notes)

    out.append("\n## Price anomalies in this run\n")
    out.extend([f"- {a}" for a in anomalies] or ["None."])

    out.append("\n## New deal posts\n")
    out.extend([f"- {'[URGENT] ' if d.urgent else ''}[{d.title}]({d.link}) ({d.source})" for d in deals]
               or ["None in this run."])
    return "\n".join(out) + "\n"

# Flight Hunter

Flight Hunter watches Google Flights and a few deal sites for cheap fares from São Paulo to Europe and back. It was built for one specific trip: three adults, leaving between January 2 and February 15, 2027, staying 14 to 16 days, visiting Barcelona, Paris and Rome, with the flights inside Europe bought separately. Everything about the trip lives in `config.yaml`, so the same code works for other trips.

It runs for free on GitHub Actions, keeps a price history in a SQLite file inside the repository, and sends alerts to Telegram, e-mail and/or WhatsApp.

## How it searches

Entering Europe through one city and leaving through another (an open-jaw trip) is often the cheapest way to cover three cities, so the agent does not assume a round trip. The search space is every Brazilian origin, every European gateway in the config, every outbound date in the window and every stay length. Pricing all of those combinations directly would mean thousands of searches per sweep, which is neither polite nor necessary, so the work is split in two levels.

The scraper only prices single legs: one-way flights from Brazil to each gateway on each outbound date, one-way flights from each gateway back to Brazil on each possible return date, and same-airport round trips for a short list of gateways. With the default config that is about 1,960 searches. They are spread over four shards, and each scheduled run scrapes one shard, so with the hourly schedule the whole grid is refreshed every four hours, with roughly 490 searches per run and a random pause of a few seconds between requests.

The combination step then assembles every valid trip from the stored legs: any outbound one-way paired with any return one-way whose date gives a stay inside the allowed range, plus the round trips. Each trip is charged an estimate of the intra-Europe flights it implies. The estimate is the cheapest ordering of the cities you must visit between the entry city and the exit city, with each leg costing a configurable amount per person. Entering at Paris and leaving from Rome needs two internal legs (Paris, Barcelona, Rome). A round trip to Paris needs three because you have to come back. Entering at Lisbon needs an extra leg because Lisbon is not on the list. This number is deliberately crude; its only job is to stop the ranking from favoring a cheap entry point that costs more later. Calibrate `internal_legs.default_cost_per_person` once you have real quotes, or set per-pair prices.

## Why open-jaw trips are priced as two one-way tickets

The scraper is the open source `fast-flights` library, which reads the data embedded in the Google Flights results page. As of version 3.1.0 (tested on 2026-10-13), one-way and round-trip searches work, but multi-city searches return an empty page because Google loads those results asynchronously. The report therefore prices an open-jaw trip as two separate one-way tickets and adds a third link that opens the same itinerary as a single multi-city ticket on Google Flights. Always click it for the trips you are considering. A single open-jaw ticket is often cheaper than two one-ways, and it protects you if the outbound is delayed in a way that affects the return (two separate tickets do not).

The tests also confirmed that the price Google returns is the total for all passengers in the search.

## Finding error fares and flash sales

Each leg quote goes through two tests. The first compares it with its own history over the last two weeks: a price is flagged when it is at most 70 percent of its historical median and at least three robust standard deviations below it, using the median absolute deviation scaled by 1.4826. Requiring both conditions avoids alerts on routes that are merely volatile and on tiny moves of very stable routes. The second test compares the price with the same route on the other dates of the window and flags any date at 60 percent or less of that median. That is the typical signature of a mispriced fare, and it works from the very first sweep, before there is any history.

Neither test proves an error fare. A price can also be low simply because a date has little demand. An alert is a prompt to look quickly, not a verdict.

The agent also reads RSS feeds of deal sites and forwards new posts that mention a European destination together with a Brazilian origin. Posts mentioning words such as "erro" or "relâmpago" are marked urgent. Some sites block automated readers (Passagens Imperdíveis and Secret Flying returned HTTP 403 from the test environment); they are skipped and logged, and they may still work from GitHub's runners.

## Alerts

You get one consolidated message per run when any of the following happens: an anomalously cheap round-trip ticket (one-way anomalies are only listed in the report, since an alert always describes a complete trip with both directions), a new best trip at least 5 percent cheaper than the previous best, any trip whose flights fall below `alerts.max_flights_total`, a relevant deal post, or more than half of the searches failing (which usually means Google is throttling the runner). The same alert is not repeated within 24 hours.

There is also a fare class check. Airlines sell seats in fare classes with limited inventory, and a search for three passengers prices all three in the cheapest class that still has three seats. After each run the agent re-prices the legs of the three best trips for a single passenger. If three times the single price is clearly below the group price, it tells you that splitting the purchase into separate bookings on the same flights may save money.

The price alert fires when the international flights of a complete trip (outbound plus return, all three passengers together, internal-leg estimate not included) cost less than `alerts.max_flights_total`, currently 10,000 BRL. The first sweep found the cheapest combinations around 12,200 BRL for three, so this threshold will only fire on a real promotion or an error fare.

## Setup

You need a GitHub account. A public repository is recommended because GitHub Actions minutes are free and unlimited for public repositories, while private ones include 2,000 minutes per month. One run takes about 20 to 25 minutes, so 24 runs a day would use roughly 15,000 minutes per month. If you prefer a private repository, change the cron in `.github/workflows/hunt.yml` to every eight hours (`17 */8 * * *`) and set `search.hours_per_shard` to 8 in the config. Nothing sensitive is stored in the repository; the credentials live in GitHub Secrets.

1. Create a new repository and upload the contents of this folder, including the hidden `.github` folder.
2. Set up at least one alert channel. For Telegram, talk to @BotFather in the Telegram app, create a bot and copy its token. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number in `"chat":{"id":...}`. For e-mail through Gmail, enable two-step verification on the account, create an app password at myaccount.google.com/apppasswords, and use `smtp.gmail.com`, port 587, your address as the user and the app password as the password.
   For WhatsApp, the free route is CallMeBot, a third-party service meant for personal use that can only message the number that activated it. Save +34 644 31 95 65 as a contact, send it the WhatsApp message "I allow callmebot to send me messages" and wait for the reply with your API key. If no key arrives within two minutes, CallMeBot asks you to retry after 24 hours. Its messages pass through CallMeBot's servers, so only flight prices and links are sent that way.
3. In the repository, open Settings, Secrets and variables, Actions, and add the secrets you need: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, and/or `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` and `ALERT_EMAIL_TO`, and/or `WHATSAPP_PHONE` (with country code, for example +5534...) and `CALLMEBOT_APIKEY`.
4. Open the Actions tab, enable workflows if GitHub asks, select "hunt", click "Run workflow", tick "Only send a test alert" and run it to check every channel; then run it once more without the box ticked to test a real sweep. The run summary shows the report, and `reports/latest.md` is updated in the repository after every run.

From then on it runs by itself every hour. GitHub disables scheduled workflows after 60 days without repository activity, but the commits each run makes count as activity. Scraping stops automatically after `search.stop_after` (February 8, 2027 by default); departure dates that are already past are skipped automatically.

## Running locally

```
pip install -r requirements.txt
python -m pytest -q
python -m hunter grid                       # size of the search grid per shard
python -m hunter run --shard all --limit 30 # quick test with 30 searches
python -m hunter run --shard all            # full sweep, about 1 to 1.5 hours
python -m hunter analyze                    # rebuild the report from the database
python -m hunter deals                      # deal feeds only
```

## Known limitations

Google may throttle or block the GitHub runner IP addresses at any time. When that happens the run stops after eight consecutive failures and you get a health alert. The natural next step is a paid source; SerpApi and Ignav both have small free tiers and the scraper module is the only part that would change.

The `fast-flights` library depends on the internal structure of the Google Flights page, which Google can change without notice. The version is pinned in `requirements.txt`; if searches start failing across the board, try upgrading it.

Google Flights does not show every airline or every online travel agency, and the fares it displays come from its partners at the time of the request. A price seen by the agent can be gone, or slightly different, when you open the link.

Travel times are converted from local times with fixed winter UTC offsets for the airports in the default config. For airports outside that table the duration falls back to the sum of flight segments, which excludes layovers.

Buying an error fare is legal, but airlines sometimes cancel those tickets. Wait a few days after the ticket is issued before booking anything non-refundable.

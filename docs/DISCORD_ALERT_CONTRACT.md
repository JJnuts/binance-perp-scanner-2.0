# Discord Best-Setup Alert Contract

Status: frozen for implementation

Contract version: 1

Frozen at: 2026-09-06T15:25:23Z

## Purpose

Send concise Discord notifications for newly qualified Binance USDT-M
altcoin best setups, then send a reminder every three hours while the setup
still qualifies. Alerting is observational only. It must not place orders,
change position sizing, or imply a probability of profit.

## Protected implementation baseline

- Pre-alert Git commit: `dc096d8c` (`feat: add guarded confluence validation stage`)
- Branch: `master`, seven commits ahead of `origin/master` when frozen
- Existing regression suite: 187 tests passed before alert implementation
- Frozen calibration database:
  `data/calibration/research_snapshots_through_20260903T120129Z.sqlite`
- Frozen calibration size: 286,846,976 bytes
- Enforced calibration SHA-256:
  `e6c1c9bfff325958473abe58c04e1d4a7efbc14f91e67313360b9707633c3126`
- Holdout cutoff: `2026-09-03T12:01:29.372069Z`
- Holdout policy: `binance-perp-scanner-holdout-20260903T120129Z`
- Live append-only research store at freeze time:
  `data/research_snapshots.sqlite`, 310,149,120 bytes, SHA-256
  `b50a1c7def60eb4f3bd497e489f3b6e8f79cedf21da64523897076369a80d53f`

Alert implementation must not change score formulas, calibration weights,
the frozen calibration database, the holdout cutoff, or holdout access rules.
The live research-store checksum is a point-in-time audit value and is expected
to change when normal append-only observation logging resumes.

## Scope

- Binance USDT-M perpetual altcoins already covered by the scanner
- Existing `Best Setups` model only
- `Long best setup` and `Short best setup` states only
- Default entry and continuation strength threshold: 75 out of 100
- Threshold must be configurable without changing the scoring formula
- BTCUSDT remains excluded from the altcoin universe

The first implementation excludes LTF-only watches, HTF-only candidates,
compression setups, mixed setups, BTC pages, BTC options, and Strategy Lab
results.

## Meaning of strength

The displayed strength is the existing best-setup score, clipped to 1 through
100 and rounded to the nearest whole number. It is a relative setup-quality
score. It must never be presented as win probability, confidence percentage,
expected return, or trading advice.

The existing construction remains unchanged:

`0.55 * LTF ignition + 0.30 * HTF expansion + 0.15 * alignment`

## Initial notification eligibility

A ticker becomes eligible for an immediate notification only when the latest
successful scan shows all of the following:

1. It satisfies the existing Best Setups entry gates, including the existing
   LTF freshness and timeframe-alignment rules.
2. Its state is exactly `Long best setup` or `Short best setup`.
3. Its best-setup score is at least the configured threshold.
4. All data required to evaluate the setup came from the current successful
   scan; a partial or failed scan cannot create an alert.
5. The corresponding symbol and direction are not already active.

The qualifying direction comes from `best_setup_state`; it must not be inferred
from the sign of an unrelated score.

## Continuing qualification and reminders

An initial fresh trigger opens an active setup. The trigger is not required to
remain fresh for three hours. Instead, every later successful scan re-evaluates
the setup using current inputs and the unchanged best-setup formula.

A setup remains active only while all of the following are true:

1. Its direction is unchanged.
2. Its current LTF direction still agrees with that direction.
3. Its HTF expansion direction still agrees with that direction.
4. Its corresponding daily structure confirmation remains true.
5. Its current recomputed strength remains at or above the configured
   continuation threshold.
6. The revalidation scan completed successfully with current data.

A reminder becomes due three hours after the last successful Discord delivery.
It is sent only after a current scan confirms all continuation conditions.
Each reminder uses the latest strength, never the original stored strength.

If the worker was stopped for more than one reminder interval, it sends at most
one currently valid reminder after startup. It must not replay missed reminders.
The next three-hour interval begins only after Discord confirms successful
delivery.

## Clearing, re-entry, and direction changes

- A failed or partial market-data scan is `unknown`, not a setup failure.
- One completed scan below qualification suppresses a due reminder but leaves
  the setup in a probationary state.
- Two consecutive completed scans below qualification clear the setup. This
  avoids alert churn from a single threshold-boundary observation.
- A cleared setup that later passes all initial gates is a new setup and is
  notified immediately.
- A confirmed direction change clears the old direction and creates the new
  direction immediately when the new side passes all initial gates. It does not
  wait for the old side's three-hour timer.
- An unsent or failed Discord attempt does not count as a successful delivery.

## Message contract

The Discord message contains only ticker, direction, and current strength:

`SOLUSDT — LONG — Strength 84/100`

`ETHUSDT — SHORT — Strength 79/100`

New notifications and reminders use the same format. No entry price, target,
stop, leverage, explanation, link, timestamp, `@everyone`, or role mention is
included. Discord mentions must be disabled in the payload.

## Persistence and delivery guarantees

- Active state, consecutive qualification failures, last successful delivery,
  and pending messages must persist in a dedicated alert SQLite database.
- Repeating the same scan must not create a second initial notification.
- Queue insertion and lifecycle updates must be transactional.
- A pending message uses a stable idempotency key so retries do not create a
  second logical notification.
- Discord timeouts, temporary failures, and rate limits must not crash or block
  the market-scanning loop indefinitely.
- Retry delays must be bounded and respect Discord's rate-limit response.
- Restarting either the worker or computer must preserve deduplication and the
  reminder clock.

## Runtime contract

Alert scanning must run in a dedicated background worker, independent of the
Streamlit browser session. Closing or suspending the browser must not stop
scanning or Discord delivery. The Streamlit site may later display sanitized
worker status, but it is not the scheduler.

Only one alert worker may own a given alert database at a time. The worker must
shut down cleanly and recover pending messages after restart.

## Secret handling

- Read the webhook only from `DISCORD_WEBHOOK_URL` at runtime.
- Never store the webhook in source code, tracked configuration, SQLite,
  reports, exceptions, screenshots, or command history produced by the app.
- Never echo the webhook when validating configuration.
- Sanitize URLs, response bodies, and exceptions before logging.
- Tests use fake webhook values and mocked HTTP responses only.
- Live Discord testing requires an explicit later task and user-provided local
  environment configuration.

## Failure behaviour

- No webhook configured: scanning and state evaluation may run, but delivery is
  disabled with a sanitized local status.
- Binance scan failure: do not open, clear, or remind setups from that scan.
- Discord failure: retain the message in the outbox and retry later.
- Database failure: do not send a message whose durable state cannot be
  recorded first.
- Process restart: recover durable state and send no duplicate initial alert.

## Acceptance criteria for the completed feature

1. A newly qualifying Long or Short best setup produces exactly one immediate
   logical notification.
2. Repeated identical scans produce no duplicate initial notification.
3. A continuously valid setup produces one reminder after each three hours of
   successful-delivery time.
4. A reminder contains the latest revalidated strength.
5. Invalid, stale, mixed, compression, or below-threshold setups do not notify.
6. Two consecutive completed qualification failures clear a setup.
7. A valid direction flip can notify immediately.
8. Restarts, timeouts, and rate limits do not lose durable state or create a
   second logical notification.
9. The worker functions with the browser closed.
10. The full pre-existing regression suite remains green.
11. Protected calibration and holdout artifacts remain unchanged.
12. Repository and logs contain no Discord webhook.

Any behavioural change to this contract must be reviewed as a separate change
before implementation continues.

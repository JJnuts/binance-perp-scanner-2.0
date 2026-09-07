# Discord Best-Setup Alert QA Status

Status: local implementation and mocked end-to-end QA passed

Recorded: 2026-09-06

Contract: `docs/DISCORD_ALERT_CONTRACT.md`, version 1

## Implemented boundary

- Pure selection of existing Long and Short best setups.
- Fresh-entry and current-continuation evaluation without changing scoring
  weights or dashboard defaults.
- Durable active-state and transactional SQLite outbox.
- One-scan probation and two-scan clearing.
- Three-hour reminders measured from successful Discord delivery.
- Bounded Discord retries with rate-limit handling and disabled mentions.
- Browser-independent worker, fixed scan cadence, clean shutdown, and
  single-instance database locking.
- Delivery disabled by default and webhook accepted only from the runtime
  environment.

## Local proof

The complete test suite passed on 2026-09-06:

`py -m unittest discover -s tests`

Result: 279 tests passed.

The worker integration suite includes these end-to-end simulations:

- Initial qualification followed by repeated-scan deduplication.
- Discord timeout, durable retry state, process restart, and successful retry
  using the original notification identity.
- Three-hour reminder using the latest revalidated strength.
- Market-data failure isolation with no lifecycle or outbox mutation.
- Immediate qualified Long-to-Short direction reversal.
- A 225-signal burst drained as 100, 100, and 25 deliveries with no duplicate
  notification identities.
- Single-instance lock rejection and clean lock release.
- Graceful scheduler shutdown and fixed scan cadence.

All Discord and market services were mocked for these simulations. No webhook
was contacted and no real alert was sent.

## Live Binance smoke proof

An explicit delivery-disabled public-market-data smoke test passed on
2026-09-06:

`py -m unittest tests.test_alert_live_smoke.LiveBinanceAlertSmokeTests.test_public_market_scan_with_delivery_and_research_writes_disabled -v`

Observed snapshot:

- Binance USDT perpetual universe: 528 symbols.
- HTF rows: 116.
- LTF rows: 118.
- Symbols present in both frames: 116.
- Currently qualifying alert signals: 0.
- Discord delivery attempts: 0.
- Research rows written: 0.

The smoke test used a temporary alert database, no Discord client, no webhook,
no WebSocket feed, and no research logger. A zero-signal snapshot is valid: it
means no symbol satisfied the frozen alert gates at that observation time.

## Live Discord smoke proof

One explicitly authorized, masked-input Discord transport test passed on
2026-09-07:

- Message: `TESTUSDT — LONG — Strength 75/100`.
- Discord response: HTTP 204.
- Delivery result: successful.
- Messages attempted: one.
- Mentions: disabled.
- Alert state: isolated temporary SQLite database, removed after the test.
- Binance scan and research logging: not invoked by this transport-only proof.

The webhook was not written to source, SQLite, or the sanitized result report.

## Protected-state verification

- Frozen calibration database SHA-256:
  `e6c1c9bfff325958473abe58c04e1d4a7efbc14f91e67313360b9707633c3126`
- Holdout policy SHA-256:
  `4c1efb92892f25a9f993ae80cc4c17b7a8ff77146e00eacd23480fa6c1dcba21`
- Pre-calibration baseline SHA-256:
  `cc0b3fdf266775b316dcdc7daf0ba3f1fee1bbe84f39a4b90f7fa8c65a2bec68`

These values still match the protected baseline. Local QA created no
production `data/alerts.sqlite` file and found no Discord webhook URL in the
source, tests, or documentation.

## Deliberately not yet accepted

The following require separate, explicit operational tasks:

1. Hetzner service configuration, secret installation, firewall review,
   restart proof, and server monitoring.

No live deployment or Discord activation is implied by this status.

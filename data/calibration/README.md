# Frozen research calibration store

Ordinary Research / Signal Quality reports read only:

`research_snapshots_through_20260903T120129Z.sqlite`

The required SHA-256 is:

`e6c1c9bfff325958473abe58c04e1d4a7efbc14f91e67313360b9707633c3126`

The file is intentionally ignored by Git and must be copied separately when
the application is deployed. Set `PERPSCANNER_CALIBRATION_DB_PATH` when the
verified backup is stored elsewhere. Before opening it, the application
requires the exact checksum above. It then uses SQLite read-only immutable
mode and never falls back to the live observation store.

The development boundary is inclusive at
`2026-09-03T12:01:29.372069Z`; later live observations are sealed holdout data.

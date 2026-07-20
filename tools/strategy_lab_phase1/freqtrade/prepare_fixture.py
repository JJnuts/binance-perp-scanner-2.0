import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


PHASE1 = Path(os.environ.get("PHASE1_ROOT", "/phase1"))
USER_DATA = Path(os.environ.get("FREQTRADE_USER_DATA", "/freqtrade/user_data"))
SOURCE = PHASE1 / "fixture" / "ema3_bounce_5m.csv"
EXPECTED = PHASE1 / "fixture" / "expected.json"
TARGET = USER_DATA / "data" / "binance" / "BTC_USDT-5m.json"

expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
source_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
if source_sha256 != expected["fixture_sha256"]:
    raise AssertionError(f"Fixture checksum changed: {source_sha256}")

rows = []
with SOURCE.open(newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        timestamp = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
        rows.append(
            [
                int(timestamp.timestamp() * 1000),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            ]
        )

if len(rows) != expected["rows"]:
    raise AssertionError(f"Expected {expected['rows']} rows, got {len(rows)}")
if any(left[0] >= right[0] for left, right in zip(rows, rows[1:])):
    raise AssertionError("Fixture timestamps must be strictly increasing")

TARGET.parent.mkdir(parents=True, exist_ok=True)
TARGET.write_text(json.dumps(rows, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"Wrote {len(rows)} candles to {TARGET}")

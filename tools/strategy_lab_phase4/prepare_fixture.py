"""Generate deterministic futures, mark, and funding files for Freqtrade."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


PHASE4 = Path(os.environ.get("PHASE4_ROOT", "/phase4"))
USER_DATA = Path(os.environ.get("FREQTRADE_USER_DATA", "/freqtrade/user_data"))
EXPECTED_PATH = PHASE4 / "fixture" / "expected.json"
FUTURES_DIR = USER_DATA / "data" / "binance" / "futures"
PAIR_PREFIX = "BTC_USDT_USDT"


def candle(timestamp: int, value: float, volume: float = 0.0) -> list[float | int]:
    return [timestamp, value, value, value, value, volume]


expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
start = datetime.fromisoformat(expected["start"].replace("Z", "+00:00"))
rows = []
closes = [100.0 + index * 0.1 for index in range(expected["rows"])]
ema = pd.Series(closes).ewm(span=9, adjust=False, min_periods=9).mean()
for index, close in enumerate(closes):
    timestamp = int((start + timedelta(minutes=5 * index)).timestamp() * 1000)
    open_price = close - 0.02
    high = close + 0.1
    low = close - 0.1
    if index in expected["candidate_signal_indices"]:
        low = float(ema.iloc[index])
    rows.append([timestamp, open_price, high, low, close, 10.0])

frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
frame["ema9"] = frame["close"].ewm(span=9, adjust=False, min_periods=9).mean()
condition = (
    (frame["close"].shift(1) > frame["ema9"].shift(1))
    & (frame["close"].shift(2) > frame["ema9"].shift(2))
    & (frame["close"].shift(3) > frame["ema9"].shift(3))
    & (frame["low"] >= frame["ema9"] * (1.0 - 0.0015))
    & (frame["low"] <= frame["ema9"] * (1.0 + 0.0005))
    & (frame["close"] > frame["ema9"])
).fillna(False)
candidate_indices = frame.index[condition].tolist()
if candidate_indices != expected["candidate_signal_indices"]:
    raise AssertionError(f"Unexpected fixture signals: {candidate_indices}")

hour_start = start.replace(minute=0, second=0, microsecond=0)
hour_end = start + timedelta(minutes=5 * expected["rows"])
funding_timestamp = int(expected["funding_timestamp"])
mark_rows = []
funding_rows = []
cursor = hour_start
while cursor < hour_end + timedelta(hours=1):
    timestamp = int(cursor.timestamp() * 1000)
    elapsed_bars = max(
        0,
        min(expected["rows"] - 1, int((cursor - start).total_seconds() // 300)),
    )
    mark = closes[elapsed_bars]
    rate = (
        expected["expected_trades"][0]["funding_rate"]
        if timestamp == funding_timestamp
        else 0.0
    )
    mark_rows.append(candle(timestamp, mark))
    funding_rows.append(candle(timestamp, rate))
    cursor += timedelta(hours=1)

FUTURES_DIR.mkdir(parents=True, exist_ok=True)
(FUTURES_DIR / f"{PAIR_PREFIX}-5m-futures.json").write_text(
    json.dumps(rows, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
(FUTURES_DIR / f"{PAIR_PREFIX}-1h-mark.json").write_text(
    json.dumps(mark_rows, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
(FUTURES_DIR / f"{PAIR_PREFIX}-1h-funding_rate.json").write_text(
    json.dumps(funding_rows, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
print(
    f"Wrote {len(rows)} futures candles, {len(mark_rows)} mark candles, "
    f"and {len(funding_rows)} funding candles"
)

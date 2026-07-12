"""Binance futures websocket kline feed for the LTF scan.

Polling 5m/15m/1h klines for ~100 symbols on a 45s cache is a request-
weight ban waiting to happen. This feed subscribes to combined kline
streams, keeps rolling buffers of CLOSED bars only (the k.x flag), and
serves frames in the exact schema of the REST fetcher. Consumers seed
each buffer with one REST fetch and fall back to REST whenever a buffer
is missing or stale, so a dropped connection degrades to today's
behavior instead of failing.

No imports from the data layer (the REST seed is handed in by the
consumer), so this module stays cycle-free and testable offline.
"""
import json
import threading
import time
from typing import Callable, Iterable, Optional

import pandas as pd

from .config import (
    EMA_SLOW,
    WS_BASE,
    WS_MAX_SYMBOLS,
    WS_STREAMS_PER_CONNECTION,
)
from .utils import _interval_to_timedelta, _utc_now_naive

BAR_COLUMNS = ["open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"]


class WSKlineFeed:
    def __init__(self, symbols: Iterable[str], intervals: Iterable[str], max_bars: int = 240):
        self.symbols = tuple(dict.fromkeys(s.upper() for s in symbols))[:WS_MAX_SYMBOLS]
        self.intervals = tuple(intervals)
        self.max_bars = max_bars
        self._buffers: dict[tuple[str, str], list[dict[str, object]]] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False

    # ------------------------------------------------------------------ data
    def handle_kline_payload(self, payload: dict) -> bool:
        """Ingest one ws kline message. Returns True if a closed bar landed."""
        k = payload.get("data", payload).get("k") if isinstance(payload, dict) else None
        if not isinstance(k, dict) or not k.get("x"):
            return False
        symbol = str(k.get("s", "")).upper()
        interval = str(k.get("i", ""))
        key = (symbol, interval)
        bar = {
            "ts": pd.to_datetime(int(k["t"]), unit="ms"),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "vol": float(k["v"]),
            "quote_vol": float(k["q"]),
            "trades": float(k["n"]),
            "tb_quote": float(k["Q"]),
        }
        with self._lock:
            buf = self._buffers.setdefault(key, [])
            if buf and buf[-1]["ts"] == bar["ts"]:
                buf[-1] = bar  # duplicate close event (reconnect) - replace
            else:
                buf.append(bar)
                buf.sort(key=lambda b: b["ts"])
            if len(buf) > self.max_bars:
                del buf[: len(buf) - self.max_bars]
        return True

    def seed(self, symbol: str, interval: str, df: Optional[pd.DataFrame]) -> None:
        """Merge a REST-fetched frame under the ws bars as the baseline."""
        if df is None or df.empty:
            return
        key = (symbol.upper(), interval)
        rows = [
            {"ts": ts, **{c: float(row[c]) for c in BAR_COLUMNS}}
            for ts, row in df.iterrows()
        ]
        with self._lock:
            existing = {b["ts"]: b for b in self._buffers.get(key, [])}
            merged = {b["ts"]: b for b in rows}
            merged.update(existing)  # ws bars win on collision
            buf = sorted(merged.values(), key=lambda b: b["ts"])
            self._buffers[key] = buf[-self.max_bars:]

    def frame(self, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        """Closed-bar frame in the REST fetcher's schema, or None if unusable."""
        key = (symbol.upper(), interval)
        with self._lock:
            buf = list(self._buffers.get(key, []))
        if len(buf) < EMA_SLOW + 5:
            return None
        last_ts = buf[-1]["ts"]
        staleness_limit = _interval_to_timedelta(interval) * 2.5
        if _utc_now_naive() - last_ts > staleness_limit:
            return None  # feed went quiet - let the caller REST-fallback
        df = pd.DataFrame(buf).set_index("ts")[BAR_COLUMNS]
        return df

    # ------------------------------------------------------------- lifecycle
    def _stream_names(self) -> list[str]:
        return [f"{s.lower()}@kline_{i}" for s in self.symbols for i in self.intervals]

    def start(self) -> None:
        if self._started or not self.symbols:
            return
        self._started = True
        try:
            import websocket  # websocket-client
        except ImportError:
            return  # dependency missing - feed stays empty, REST covers it
        streams = self._stream_names()
        for chunk_start in range(0, len(streams), WS_STREAMS_PER_CONNECTION):
            chunk = streams[chunk_start : chunk_start + WS_STREAMS_PER_CONNECTION]
            thread = threading.Thread(
                target=self._run_connection,
                args=(websocket, "/".join(chunk)),
                daemon=True,
                name=f"ws-kline-{chunk_start}",
            )
            thread.start()
            self._threads.append(thread)

    def _run_connection(self, websocket_mod, stream_path: str) -> None:
        url = f"{WS_BASE}?streams={stream_path}"

        def on_message(_ws, message: str) -> None:
            try:
                self.handle_kline_payload(json.loads(message))
            except Exception:
                pass

        while not self._stopped.is_set():
            try:
                app = websocket_mod.WebSocketApp(url, on_message=on_message)
                app.run_forever(ping_interval=180, ping_timeout=10)
            except Exception:
                pass
            if self._stopped.is_set():
                break
            time.sleep(5.0)  # reconnect backoff

    def stop(self) -> None:
        self._stopped.set()


# One shared feed per process. Streamlit reruns reuse the module, so the
# daemon threads and buffers survive across refreshes.
_shared_feed: Optional[WSKlineFeed] = None
_shared_lock = threading.Lock()


def get_shared_feed(symbols: Iterable[str], intervals: Iterable[str]) -> WSKlineFeed:
    """Return the process-wide feed, rebuilding if the universe grew."""
    global _shared_feed
    requested = tuple(dict.fromkeys(s.upper() for s in symbols))[:WS_MAX_SYMBOLS]
    with _shared_lock:
        feed = _shared_feed
        if feed is not None and set(requested) <= set(feed.symbols) and set(intervals) <= set(feed.intervals):
            return feed
        if feed is not None:
            feed.stop()
        merged_symbols = requested
        if feed is not None:
            merged_symbols = tuple(dict.fromkeys(feed.symbols + requested))[:WS_MAX_SYMBOLS]
        _shared_feed = WSKlineFeed(merged_symbols, tuple(intervals))
        _shared_feed.start()
        return _shared_feed

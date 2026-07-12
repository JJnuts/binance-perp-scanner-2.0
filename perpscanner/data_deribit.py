"""Deribit options tickers and the block-trade store."""

import numpy as np
import pandas as pd
import sqlite3
from typing import Optional

from .config import BLOCK_FLOW_RETENTION_DAYS, BTC_OPTIONS_BLOCK_DB_PATH
from .net import _get_deribit
from .utils import _safe_float, _utc_now_naive


def _fetch_deribit_option_ticker(instrument_name: str) -> dict[str, object]:
    payload = _get_deribit("public/ticker", params={"instrument_name": instrument_name}, timeout=12)
    greeks = payload.get("greeks", {}) if isinstance(payload, dict) else {}
    underlying_price = 0.0
    if isinstance(payload, dict):
        underlying_price = _safe_float(payload.get("underlying_price"))
        if underlying_price <= 0.0:
            underlying_price = _safe_float(payload.get("index_price"))
    return {
        "instrument_name": instrument_name,
        "underlying_price": underlying_price,
        "mark_iv": _safe_float(payload.get("mark_iv") if isinstance(payload, dict) else 0.0),
        "bid_iv": _safe_float(payload.get("bid_iv") if isinstance(payload, dict) else 0.0),
        "ask_iv": _safe_float(payload.get("ask_iv") if isinstance(payload, dict) else 0.0),
        "delta": _safe_float(greeks.get("delta")),
        "gamma": _safe_float(greeks.get("gamma")),
        "vega": _safe_float(greeks.get("vega")),
        "theta": _safe_float(greeks.get("theta")),
        "last_price": _safe_float(payload.get("last_price") if isinstance(payload, dict) else 0.0),
    }
def _safe_fetch_deribit_option_ticker(instrument_name: str) -> Optional[dict[str, object]]:
    try:
        return _fetch_deribit_option_ticker(instrument_name)
    except Exception:
        return None
def _init_block_trade_store() -> sqlite3.Connection:
    BTC_OPTIONS_BLOCK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BTC_OPTIONS_BLOCK_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deribit_block_trades (
            trade_id TEXT PRIMARY KEY,
            block_trade_id TEXT NOT NULL,
            block_rfq_id TEXT,
            combo_id TEXT,
            combo_trade_id TEXT,
            block_trade_leg_count INTEGER,
            timestamp INTEGER NOT NULL,
            instrument_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL,
            contracts REAL,
            price REAL,
            mark_price REAL,
            iv REAL,
            index_price REAL,
            inserted_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deribit_blocks_ts ON deribit_block_trades(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deribit_blocks_block_id ON deribit_block_trades(block_trade_id)")
    return conn
def _fetch_recent_deribit_block_trades() -> list[dict[str, object]]:
    payload = _get_deribit(
        "public/get_last_trades_by_currency",
        params={"currency": "BTC", "kind": "option", "count": 1000, "sorting": "desc"},
        timeout=20,
    )
    trades = payload.get("trades", []) if isinstance(payload, dict) else []
    return [trade for trade in trades if isinstance(trade, dict) and trade.get("block_trade_id") and trade.get("trade_id")]
def _store_deribit_block_trades(trades: list[dict[str, object]]) -> dict[str, int]:
    conn = _init_block_trade_store()
    inserted = 0
    try:
        now_text = _utc_now_naive().isoformat()
        rows = []
        for trade in trades:
            rows.append(
                (
                    str(trade.get("trade_id")),
                    str(trade.get("block_trade_id")),
                    str(trade.get("block_rfq_id")) if trade.get("block_rfq_id") not in (None, "") else None,
                    str(trade.get("combo_id")) if trade.get("combo_id") not in (None, "") else None,
                    str(trade.get("combo_trade_id")) if trade.get("combo_trade_id") not in (None, "") else None,
                    int(_safe_float(trade.get("block_trade_leg_count"), 1.0)),
                    int(_safe_float(trade.get("timestamp"))),
                    str(trade.get("instrument_name", "")),
                    str(trade.get("direction", "")).lower(),
                    _safe_float(trade.get("amount")),
                    _safe_float(trade.get("contracts")),
                    _safe_float(trade.get("price")),
                    _safe_float(trade.get("mark_price")),
                    _safe_float(trade.get("iv")),
                    _safe_float(trade.get("index_price")),
                    now_text,
                )
            )
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO deribit_block_trades (
                trade_id, block_trade_id, block_rfq_id, combo_id, combo_trade_id,
                block_trade_leg_count, timestamp, instrument_name, direction,
                amount, contracts, price, mark_price, iv, index_price, inserted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.total_changes - before
        cutoff_ms = int((_utc_now_naive() - pd.Timedelta(days=BLOCK_FLOW_RETENTION_DAYS)).timestamp() * 1000)
        conn.execute("DELETE FROM deribit_block_trades WHERE timestamp < ?", (cutoff_ms,))
        conn.commit()
    finally:
        conn.close()
    return {"fetched": len(trades), "inserted": int(inserted)}
def _read_deribit_block_trades(days: int = BLOCK_FLOW_RETENTION_DAYS) -> pd.DataFrame:
    if not BTC_OPTIONS_BLOCK_DB_PATH.exists():
        return pd.DataFrame()
    cutoff_ms = int((_utc_now_naive() - pd.Timedelta(days=days)).timestamp() * 1000)
    conn = sqlite3.connect(BTC_OPTIONS_BLOCK_DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM deribit_block_trades WHERE timestamp >= ? ORDER BY timestamp DESC",
            conn,
            params=(cutoff_ms,),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True).dt.tz_localize(None)
    for col in ["amount", "contracts", "price", "mark_price", "iv", "index_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df
def _update_deribit_block_trade_store() -> dict[str, object]:
    try:
        trades = _fetch_recent_deribit_block_trades()
        result = _store_deribit_block_trades(trades)
        result["error"] = ""
        return result
    except Exception as exc:
        return {"fetched": 0, "inserted": 0, "error": str(exc)}

"""Pure DataFrame transformations for options market data."""

import pandas as pd


def _merge_deribit_option_tickers(summary_df: pd.DataFrame, ticker_df: pd.DataFrame) -> pd.DataFrame:
    """Join option tickers while keeping their live values authoritative.

    Deribit's book summaries and per-instrument tickers both expose fields such
    as ``mark_iv`` and ``underlying_price``. Preserve the summary versions for
    diagnostics, but leave the ticker versions unsuffixed because downstream
    analytics expect the live ticker field names.
    """
    return summary_df.merge(
        ticker_df,
        on="instrument_name",
        how="inner",
        suffixes=("_summary", ""),
    )

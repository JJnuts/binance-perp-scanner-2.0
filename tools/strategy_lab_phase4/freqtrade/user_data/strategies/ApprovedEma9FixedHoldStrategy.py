from datetime import datetime, timedelta

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class ApprovedEma9FixedHoldStrategy(IStrategy):
    """Static allowlisted Phase 4 strategy. No generated Python is executed."""

    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False
    startup_candle_count = 500

    minimal_roi = {"0": 1000.0}
    stoploss = -0.99
    trailing_stop = False

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {
        "entry": "GTC",
        "exit": "GTC",
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema9"] = dataframe["close"].ewm(
            span=9,
            adjust=False,
            min_periods=9,
        ).mean()
        precondition = (
            (dataframe["close"].shift(1) > dataframe["ema9"].shift(1))
            & (dataframe["close"].shift(2) > dataframe["ema9"].shift(2))
            & (dataframe["close"].shift(3) > dataframe["ema9"].shift(3))
        )
        lower = dataframe["ema9"] * (1.0 - 0.0015)
        upper = dataframe["ema9"] * (1.0 + 0.0005)
        dataframe["approved_ema9_bounce"] = (
            precondition
            & (dataframe["low"] >= lower)
            & (dataframe["low"] <= upper)
            & (dataframe["close"] > dataframe["ema9"])
        ).fillna(False)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        condition = dataframe["approved_ema9_bounce"] & (dataframe["volume"] > 0)
        dataframe.loc[condition, ["enter_long", "enter_tag"]] = (
            1,
            "approved_ema9_bounce",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        del pair, current_rate, current_profit, kwargs
        if current_time >= trade.open_date_utc + timedelta(minutes=60):
            return "fixed_12_bar_horizon"
        return None

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        del (
            pair,
            current_time,
            current_rate,
            proposed_stake,
            min_stake,
            leverage,
            entry_tag,
            side,
            kwargs,
        )
        return min(1000.0, max_stake)

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        del (
            pair,
            current_time,
            current_rate,
            proposed_leverage,
            max_leverage,
            entry_tag,
            side,
            kwargs,
        )
        return 1.0

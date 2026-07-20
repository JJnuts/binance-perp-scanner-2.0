from pandas import DataFrame

from freqtrade.strategy import IStrategy


class Phase1FixtureStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False

    minimal_roi = {"0": 1000.0}
    stoploss = -0.99
    trailing_stop = False

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    startup_candle_count = 5

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
        dataframe["ema3"] = dataframe["close"].ewm(
            span=3,
            adjust=False,
            min_periods=3,
        ).mean()
        dataframe["phase1_bounce"] = (
            (dataframe["close"].shift(1) > dataframe["ema3"].shift(1))
            & (dataframe["close"].shift(2) > dataframe["ema3"].shift(2))
            & (dataframe["close"].shift(3) > dataframe["ema3"].shift(3))
            & (dataframe["low"] <= dataframe["ema3"])
            & (dataframe["close"] > dataframe["ema3"])
        ).fillna(False)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        condition = dataframe["phase1_bounce"] & (dataframe["volume"] > 0)
        dataframe.loc[condition, ["enter_long", "enter_tag"]] = (
            1,
            "phase1_ema3_bounce",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        condition = dataframe["phase1_bounce"].shift(2).fillna(False)
        condition &= dataframe["volume"] > 0
        dataframe.loc[condition, "exit_long"] = 1
        return dataframe

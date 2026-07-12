"""Regime-conditional trigger thresholds.

Fixed z-score gates mean different things in dead vs hot tape: a 2.0
volume z-spike is routine when BTC is trending hard and rare when it is
flat. The ignition gates therefore scale with the BTC daily regime that
build_metrics already computes.
"""
from dataclasses import dataclass
from typing import Optional

from .config import ATR_ROC_THRESHOLD, OI_Z_THRESHOLD, VOLUME_Z_THRESHOLD


@dataclass(frozen=True)
class RegimeThresholds:
    volume_z: float = VOLUME_Z_THRESHOLD
    oi_z: float = OI_Z_THRESHOLD
    atr_roc: float = ATR_ROC_THRESHOLD
    regime: str = "Unknown"


# Multipliers applied to the static gates per BTC daily regime.
# Hot tape (trend or panic) -> spikes are common, demand more unusual
# ones. Quiet range -> genuine ignitions are subtler, loosen the gates.
REGIME_THRESHOLD_MULTIPLIERS = {
    "Bull trend": 1.20,
    "Drawdown": 1.15,
    "Range": 0.85,
    "Unknown": 1.00,
}


def thresholds_for_regime(btc_regime: Optional[dict]) -> RegimeThresholds:
    regime = str((btc_regime or {}).get("btc_daily_regime", "Unknown"))
    mult = REGIME_THRESHOLD_MULTIPLIERS.get(regime, 1.0)
    return RegimeThresholds(
        volume_z=VOLUME_Z_THRESHOLD * mult,
        oi_z=OI_Z_THRESHOLD * mult,
        atr_roc=ATR_ROC_THRESHOLD * mult,
        regime=regime,
    )

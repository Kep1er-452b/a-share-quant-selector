"""
B1 Min J 61 Complex - full B1(V2.42.61) conditions with dynamic Min J.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from strategy.b1_min_j_simple import calculate_min_j
from strategy.b1_v24261 import (
    apply_b1_v24261_signal,
    b1_v24261_default_params,
    build_b1_v24261_signal,
)
from utils.strategy_labels import is_invalid_stock_name


class B1MinJ61ComplexStrategy(BaseStrategy):
    """Full B1(V2.42.61) formula with dynamic Min J replacing J <= 13."""

    def __init__(self, params=None):
        default_params = b1_v24261_default_params({
            "MIN_HISTORY_DAYS": 160,
            "J_VALLEY_MAX": 55,
            "LONG_OFFSET": 10,
        })
        default_params.pop("J_MAX", None)
        if params:
            default_params.update(params)
        super().__init__("B1 Min J 61 Complex", default_params)

    def calculate_indicators(self, df) -> pd.DataFrame:
        from strategy.b1_v24261 import B1V24261Strategy

        base_params = dict(self.params)
        base_params["J_MAX"] = 13
        result = B1V24261Strategy(base_params).calculate_indicators(df)
        result["MIN_J"] = calculate_min_j(
            result,
            j_valley_max=self.params["J_VALLEY_MAX"],
            long_offset=self.params["LONG_OFFSET"],
        )
        result["J_OK"] = result["J"] <= result["MIN_J"]
        result = apply_b1_v24261_signal(result, self.params, j_ok_series=result["J_OK"])
        result["B1_MIN_J_61_COMPLEX_SIGNAL"] = result["B1_V24261_SIGNAL"]
        return result

    def select_stocks(self, df, stock_name="") -> list:
        if df.empty or len(df) < self.params["MIN_HISTORY_DAYS"]:
            return []
        if stock_name and is_invalid_stock_name(stock_name):
            return []

        latest = df.iloc[0]
        if latest.get("volume", 0) <= 0 or pd.isna(latest.get("close")):
            return []
        if not bool(latest.get("B1_MIN_J_61_COMPLEX_SIGNAL", False)):
            return []

        signal = build_b1_v24261_signal(
            latest,
            category="b1_min_j_61_complex",
            fallback_reason="满足 B1 Min J 61 Complex 条件",
        )
        signal["MIN_J"] = round(float(latest["MIN_J"]), 2)
        if bool(latest.get("J_OK", False)):
            signal["reasons"].insert(0, "J值跌破动态Min J")
        return [signal]

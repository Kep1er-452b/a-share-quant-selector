"""
B1MinJComplex 策略 - B1(V2.42P) 条件 + 动态 Min J
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from strategy.b1_min_j_simple import calculate_min_j
from strategy.b1_v242p import (
    apply_b1_v242p_signal,
    build_b1_v242p_signal,
    b1_v242p_default_params,
    calculate_b1_v242p_indicators,
)
from utils.strategy_labels import is_invalid_stock_name


class B1MinJComplexStrategy(BaseStrategy):
    """B1MinJComplex 策略"""

    def __init__(self, params=None):
        default_params = b1_v242p_default_params({
            "MIN_HISTORY_DAYS": 160,
            "J_VALLEY_MAX": 55,
            "LONG_OFFSET": 10,
        })
        if params:
            default_params.update(params)
        super().__init__("B1MinJComplex", default_params)

    def calculate_indicators(self, df) -> pd.DataFrame:
        result = df.copy()
        result = calculate_b1_v242p_indicators(result, self.params)
        result["MIN_J"] = calculate_min_j(
            result,
            j_valley_max=self.params["J_VALLEY_MAX"],
            long_offset=self.params["LONG_OFFSET"],
        )
        result["J_OK"] = result["J"] <= result["MIN_J"]
        result = apply_b1_v242p_signal(result, self.params, j_ok_series=result["J_OK"])
        result["B1_MIN_J_COMPLEX_SIGNAL"] = result["B1_V242P_SIGNAL"]
        return result

    def select_stocks(self, df, stock_name='') -> list:
        if df.empty:
            return []

        if len(df) < self.params["MIN_HISTORY_DAYS"]:
            return []

        if stock_name and is_invalid_stock_name(stock_name):
            return []

        latest = df.iloc[0]
        if latest.get("volume", 0) <= 0 or pd.isna(latest.get("close")):
            return []

        if not bool(latest.get("B1_MIN_J_COMPLEX_SIGNAL", False)):
            return []

        signal = build_b1_v242p_signal(
            latest,
            category="b1_min_j_complex",
            fallback_reason="满足 B1MinJComplex 条件",
        )
        signal["MIN_J"] = round(float(latest["MIN_J"]), 2)
        if bool(latest.get("J_OK", False)):
            signal["reasons"].insert(0, "J值跌破动态Min J")
        return [signal]

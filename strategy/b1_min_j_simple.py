"""
B1MinJSimple 策略 - 基于知行趋势过滤和 Min J 动态底部线
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from utils.technical import KDJ, calculate_min_j, calculate_zhixing_trend
from utils.strategy_labels import is_invalid_stock_name


class B1MinJSimpleStrategy(BaseStrategy):
    """B1MinJSimple 策略"""

    def __init__(self, params=None):
        default_params = {
            "MIN_HISTORY_DAYS": 114,
            "J_VALLEY_MAX": 55,
            "LONG_OFFSET": 10,
        }
        if params:
            default_params.update(params)
        super().__init__("B1MinJSimple", default_params)

    def calculate_indicators(self, df) -> pd.DataFrame:
        result = df.copy(deep=False)

        if not {"K", "D", "J"}.issubset(result.columns):
            kdj_df = KDJ(result, n=9, m1=3, m2=3)
            result["K"] = kdj_df["K"]
            result["D"] = kdj_df["D"]
            result["J"] = kdj_df["J"]

        if {"short_term_trend", "bull_bear_line"}.issubset(result.columns):
            result["ZX_SHORT"] = result["short_term_trend"]
            result["ZX_LONG"] = result["bull_bear_line"]
        else:
            trend_df = calculate_zhixing_trend(result, m1=14, m2=28, m3=57, m4=114)
            result["ZX_SHORT"] = trend_df["short_term_trend"]
            result["ZX_LONG"] = trend_df["bull_bear_line"]

        result["MIN_J"] = self._calculate_min_j(result)
        result["COND_TREND"] = result["ZX_SHORT"] > result["ZX_LONG"]
        result["COND_J"] = result["J"] < result["MIN_J"]
        result["B1_MIN_J_SIMPLE_SIGNAL"] = result["COND_TREND"] & result["COND_J"]

        return result

    def _calculate_min_j(self, df) -> pd.Series:
        return calculate_min_j(
            df,
            j_valley_max=self.params["J_VALLEY_MAX"],
            long_offset=self.params["LONG_OFFSET"],
        )

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

        if not bool(latest.get("B1_MIN_J_SIMPLE_SIGNAL", False)):
            return []

        reasons = []
        if bool(latest.get("COND_TREND", False)):
            reasons.append("白线在黄线上方")
        if bool(latest.get("COND_J", False)):
            reasons.append("J值跌破动态Min J")

        return [{
            "date": latest["date"],
            "close": round(float(latest["close"]), 2),
            "J": round(float(latest["J"]), 2),
            "MIN_J": round(float(latest["MIN_J"]), 2),
            "market_cap": round(float(latest["market_cap"]) / 1e8, 2) if pd.notna(latest.get("market_cap")) else 0,
            "reasons": reasons or ["满足 B1MinJSimple 条件"],
            "category": "b1_min_j_simple",
            "zx_short": round(float(latest["ZX_SHORT"]), 2),
            "zx_long": round(float(latest["ZX_LONG"]), 2),
        }]

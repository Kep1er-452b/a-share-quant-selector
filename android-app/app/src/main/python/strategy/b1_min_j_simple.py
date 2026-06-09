"""
B1MinJSimple 策略 - 基于知行趋势过滤和 Min J 动态底部线
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from utils.technical import KDJ, REF, SUM, calculate_zhixing_trend
from utils.strategy_labels import is_invalid_stock_name


def calculate_min_j(df, j_valley_max=55, long_offset=10) -> pd.Series:
    """
    按原 Min J 指标思路确认历史 J 坑底。

    本项目行情数据最新日期在前；REF(J, 1) 是前一交易日，shift(1) 是后一交易日。
    因此最新一根 K 线自身不会被判断为坑底，但可以用今天的数据确认昨天的坑底。
    """
    j = pd.to_numeric(df["J"], errors="coerce")
    k = pd.to_numeric(df["K"], errors="coerce")
    d = pd.to_numeric(df["D"], errors="coerce")

    j_prev = REF(j, 1)
    j_next = j.shift(1)

    valley = (
        (j < j_prev) &
        (j < j_next) &
        (j < j_valley_max) &
        (j < d) &
        (j < k) &
        (k < d)
    ).fillna(False)

    result = df.copy()
    result["J_VALLEY"] = valley
    result["J_MASK"] = j.where(valley, 0).fillna(0)
    result["C_MASK"] = valley.astype(int)

    sum_j_short = SUM(result["J_MASK"], 28)
    count_short = SUM(result["C_MASK"], 28)

    sum_j_mid = SUM(result["J_MASK"], 57)
    count_mid = SUM(result["C_MASK"], 57)

    sum_j_long = SUM(result["J_MASK"], 114)
    count_long = SUM(result["C_MASK"], 114)

    val_short = sum_j_short / count_short.clip(lower=1)
    val_mid = sum_j_mid / count_mid.clip(lower=1)
    val_long = (sum_j_long / count_long.clip(lower=1)) + long_offset

    min_j = (val_short + val_mid + val_long) / 3.0
    return min_j.fillna(0).set_axis(df.index)


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
        result = df.copy()

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

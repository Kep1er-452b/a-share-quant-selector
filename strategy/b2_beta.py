"""
B2 选股 Beta 版策略
"""
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from utils.technical import COUNT, EMA, HHV, LLV, MA, REF, SUM, KDJ
from utils.strategy_labels import is_invalid_stock_name


class B2BetaStrategy(BaseStrategy):
    """B2 选股 Beta 版"""

    def __init__(self, params=None):
        default_params = {
            "J_MAX": 80,
            "J13_THRESHOLD": 13,
            "J13_LOOKBACK": 2,
            "MIN_GAIN_RATIO": 1.04,
            "VOLUP_RATIO": 1.01,
            "YANGYIN_RATIO_14": 1.2,
            "MV_MIN_BILLION": 50,
            "TOP_RANGE_WINDOW": 28,
            "TOP_RANGE_RATIO": 0.95,
            "FD15_VOL_RATIO": 1.15,
            "GOOD28_MAX_COUNT": 2,
            "PLRY_VOL_RATIO": 1.8,
            "PLRY_WINDOW": 28,
            "PLRY_MIN_COUNT": 2,
        }
        if params:
            default_params.update(params)
        super().__init__("B2选股Beta版", default_params)

    def calculate_indicators(self, df) -> pd.DataFrame:
        result = df.copy()

        ref_close_1 = result["ref_close_1"] if "ref_close_1" in result.columns else REF(result["close"], 1)
        ref_vol_1 = result["ref_vol_1"] if "ref_vol_1" in result.columns else REF(result["volume"], 1)

        result["JYZY_T"] = (result["close"] > result["open"]) & (result["close"] < ref_close_1)
        result["JYZY2_T"] = (result["close"] < result["open"]) & (result["close"] > ref_close_1)

        if "REAL_YANG" not in result.columns:
            result["REAL_YANG"] = (result["close"] > result["open"]) & ~(result["close"] < ref_close_1)
        if "REAL_YIN" not in result.columns:
            result["REAL_YIN"] = (result["close"] < result["open"]) & ~(result["close"] > ref_close_1)

        if not {"K", "D", "J"}.issubset(result.columns):
            kdj_df = KDJ(result, n=9, m1=3, m2=3)
            result["K"] = kdj_df["K"]
            result["D"] = kdj_df["D"]
            result["J"] = kdj_df["J"]

        result["J_OK"] = result["J"] <= self.params["J_MAX"]
        result["J13_OK"] = COUNT(result["J"] <= self.params["J13_THRESHOLD"], self.params["J13_LOOKBACK"]) >= 1

        result["ZF4_OK"] = result["close"] >= ref_close_1 * self.params["MIN_GAIN_RATIO"]
        result["VOLUP_OK"] = result["volume"] > self.params["VOLUP_RATIO"] * ref_vol_1
        result["UPSHADOW"] = result["high"] - np.maximum(result["close"], result["open"])
        result["UP_OK"] = result["UPSHADOW"] <= (result["close"] - ref_close_1) / 2

        real_yang_volume = result["volume"] * result["REAL_YANG"].astype(int)
        real_yin_volume = result["volume"] * result["REAL_YIN"].astype(int)
        result["VOL_YANG"] = result["VOL_REAL_YANG_14"] if "VOL_REAL_YANG_14" in result.columns else SUM(real_yang_volume, 14)
        result["VOL_YIN"] = result["VOL_REAL_YIN_14"] if "VOL_REAL_YIN_14" in result.columns else SUM(real_yin_volume, 14)
        result["YANGYIN_OK"] = result["VOL_YANG"] > self.params["YANGYIN_RATIO_14"] * result["VOL_YIN"]

        mv_min = self.params["MV_MIN_BILLION"] * 1e8
        market_cap = pd.to_numeric(result.get("market_cap", 0), errors="coerce").fillna(0)
        result["MV"] = market_cap / 1e8
        result["MVOK"] = market_cap >= max(mv_min, 1e8)

        top_window = self.params["TOP_RANGE_WINDOW"]
        if top_window == 28 and {"OPEN_LLV_28", "OPEN_HHV_28"}.issubset(result.columns):
            open_llv = result["OPEN_LLV_28"]
            open_hhv = result["OPEN_HHV_28"]
        else:
            open_llv = LLV(result["open"], top_window)
            open_hhv = HHV(result["open"], top_window)
        result["VAR_O85"] = open_llv + self.params["TOP_RANGE_RATIO"] * (open_hhv - open_llv)
        result["TOP150"] = result["open"] >= result["VAR_O85"]
        result["FD15"] = (
            (result["close"] < ref_close_1) &
            (result["close"] <= result["open"]) &
            (result["volume"] >= self.params["FD15_VOL_RATIO"] * ref_vol_1)
        )
        result["CNT28"] = COUNT(result["TOP150"] & result["FD15"], top_window)
        result["GOOD28"] = result["CNT28"] <= self.params["GOOD28_MAX_COUNT"]

        result["AVG40"] = result["AVG_VOLUME_40"] if "AVG_VOLUME_40" in result.columns else MA(result["volume"], 40)
        plry_window = self.params["PLRY_WINDOW"]
        result["PLRY"] = (
            (result["volume"] > self.params["PLRY_VOL_RATIO"] * ref_vol_1) &
            (result["close"] > result["open"]) &
            (result["volume"] > result["AVG40"])
        )
        result["PLRY_CNT"] = COUNT(result["PLRY"], plry_window) >= self.params["PLRY_MIN_COUNT"]
        result["PLRY_FIRST"] = result["PLRY"] & ~REF(result["PLRY"], 1).fillna(False).astype(bool)
        result["PLRY_CONT"] = result["PLRY"] & REF(result["PLRY"], 1).fillna(False).astype(bool)
        result["CNT_FIRST"] = COUNT(result["PLRY_FIRST"], plry_window)
        result["CNT_CONT"] = COUNT(result["PLRY_CONT"], plry_window)
        result["SUM_OK"] = (result["CNT_FIRST"] + result["CNT_CONT"]) >= self.params["PLRY_MIN_COUNT"]

        result["A1"] = (
            result["PLRY_CNT"] &
            result["YANGYIN_OK"] &
            result["J_OK"] &
            result["MVOK"] &
            result["GOOD28"] &
            result["SUM_OK"] &
            result["ZF4_OK"] &
            result["VOLUP_OK"] &
            result["J13_OK"] &
            result["UP_OK"]
        )

        result["WL"] = EMA(EMA(result["close"], 10), 10)
        if {"short_term_trend", "bull_bear_line"}.issubset(result.columns):
            result["WL"] = result["short_term_trend"]
            result["YL"] = result["bull_bear_line"]
        else:
            result["YL"] = (
                MA(result["close"], 14) +
                MA(result["close"], 28) +
                MA(result["close"], 57) +
                MA(result["close"], 114)
            ) / 4

        result["B2_SIGNAL"] = result["A1"] & (result["WL"] > result["YL"]) & (result["close"] > result["YL"])
        return result

    def select_stocks(self, df, stock_name='') -> list:
        if df.empty:
            return []

        if stock_name and is_invalid_stock_name(stock_name):
            return []

        latest = df.iloc[0]
        if latest.get("volume", 0) <= 0 or pd.isna(latest.get("close")):
            return []

        if not bool(latest.get("B2_SIGNAL", False)):
            return []

        reasons = []
        if bool(latest.get("J13_OK", False)):
            reasons.append("J值短期触底")
        if bool(latest.get("ZF4_OK", False)):
            reasons.append("当日涨幅达标")
        if bool(latest.get("VOLUP_OK", False)):
            reasons.append("微放量")
        if bool(latest.get("YANGYIN_OK", False)):
            reasons.append("14日阳量占优")
        if bool(latest.get("PLRY_CNT", False)):
            reasons.append("倍量阳次数达标")
        if bool(latest.get("GOOD28", False)):
            reasons.append("高位放量阴受控")
        if bool(latest.get("SUM_OK", False)):
            reasons.append("首量/连量累计达标")
        if bool(latest.get("MVOK", False)) and self.params["MV_MIN_BILLION"] > 0:
            reasons.append("市值达标")

        return [{
            "date": latest["date"],
            "close": round(float(latest["close"]), 2),
            "J": round(float(latest["J"]), 2),
            "market_cap": round(float(latest["market_cap"]) / 1e8, 2) if pd.notna(latest.get("market_cap")) else 0,
            "reasons": reasons or ["满足 B2 选股条件"],
            "category": "b2_beta",
            "wl": round(float(latest["WL"]), 2),
            "yl": round(float(latest["YL"]), 2),
            "yangyin_ratio_14": round(
                float(latest["VOL_YANG"]) / float(latest["VOL_YIN"]) if float(latest["VOL_YIN"]) > 0 else 999.0,
                2,
            ),
        }]

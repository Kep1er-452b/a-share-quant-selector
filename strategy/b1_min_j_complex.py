"""
B1MinJComplex 策略 - B1(V2.42B) 条件 + 动态 Min J
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from strategy.b1_min_j_simple import calculate_min_j
from utils.technical import COUNT, HHV, KDJ, LLV, MA, REF, SMA, SUM
from utils.strategy_labels import is_invalid_stock_name


class B1MinJComplexStrategy(BaseStrategy):
    """B1MinJComplex 策略"""

    def __init__(self, params=None):
        default_params = {
            "MIN_HISTORY_DAYS": 160,
            "J_VALLEY_MAX": 55,
            "LONG_OFFSET": 10,
            "MV_MIN_BILLION": 50,
            "YANGYIN_RATIO_57": 1.25,
            "YANGYIN_RATIO_14": 2.25,
            "PLRY_VOL_RATIO": 1.95,
            "HALF_DOWN_VOL_RATIO": 0.5,
            "TOP_RANGE_RATIO": 0.95,
            "FD15_VOL_RATIO": 1.2,
            "B1_TREND_TOLERANCE": 0.985,
        }
        if params:
            default_params.update(params)
        super().__init__("B1MinJComplex", default_params)

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

        result["MIN_J"] = calculate_min_j(
            result,
            j_valley_max=self.params["J_VALLEY_MAX"],
            long_offset=self.params["LONG_OFFSET"],
        )
        result["J_OK"] = result["J"] <= result["MIN_J"]

        result["VOL_YANG1"] = SUM(result["volume"] * result["REAL_YANG"].astype(int), 57)
        result["VOL_YIN1"] = SUM(result["volume"] * result["REAL_YIN"].astype(int), 57)
        result["VOL_YANG2"] = SUM(result["volume"] * result["REAL_YANG"].astype(int), 14)
        result["VOL_YIN2"] = SUM(result["volume"] * result["REAL_YIN"].astype(int), 14)

        result["YANGYIN_OK1"] = result["VOL_YANG1"] > self.params["YANGYIN_RATIO_57"] * result["VOL_YIN1"]
        result["YANGYIN_OK2"] = result["VOL_YANG2"] > self.params["YANGYIN_RATIO_14"] * result["VOL_YIN2"]

        mv_min = self.params["MV_MIN_BILLION"] * 1e8
        market_cap = pd.to_numeric(result.get("market_cap", 0), errors="coerce").fillna(0)
        result["MV"] = market_cap / 1e8
        result["MVOK"] = market_cap >= mv_min

        o_llv = LLV(result["open"], 21)
        o_hhv = HHV(result["open"], 21)
        result["O85"] = o_llv + self.params["TOP_RANGE_RATIO"] * (o_hhv - o_llv)
        result["TOP150"] = result["open"] >= result["O85"]
        result["FD15"] = (
            (result["close"] < ref_close_1) &
            (result["close"] <= result["open"]) &
            (result["volume"] >= self.params["FD15_VOL_RATIO"] * ref_vol_1)
        )
        result["CNT28"] = COUNT(result["TOP150"] & result["FD15"], 21)
        result["GOOD28"] = result["CNT28"] <= 0

        result["AVG40"] = MA(result["volume"], 40)
        result["PLRY"] = (
            (result["volume"] > self.params["PLRY_VOL_RATIO"] * ref_vol_1) &
            (result["close"] > result["open"]) &
            (result["volume"] > result["AVG40"])
        )
        result["PLRY_CNT"] = (COUNT(result["PLRY"], 14) >= 2) | (COUNT(result["PLRY"], 57) >= 4)
        result["PLRY_FIRST"] = result["PLRY"] & ~REF(result["PLRY"], 1).fillna(False).astype(bool)
        result["PLRY_CONT"] = result["PLRY"] & REF(result["PLRY"], 1).fillna(False).astype(bool)
        result["PRE_NOT_REALYIN"] = ~REF(result["REAL_YIN"], 1).fillna(False).astype(bool)
        result["HALF_DOWN"] = (
            result["PRE_NOT_REALYIN"] &
            (result["close"] < ref_close_1) &
            (result["volume"] <= self.params["HALF_DOWN_VOL_RATIO"] * ref_vol_1)
        )
        result["CNT_FIRST"] = COUNT(result["PLRY_FIRST"], 57)
        result["CNT_CONT"] = COUNT(result["PLRY_CONT"], 57)
        result["CNT_HALF"] = COUNT(result["HALF_DOWN"], 57)
        result["THREE_SUM_OK"] = (result["CNT_FIRST"] + result["CNT_CONT"] + result["CNT_HALF"]) >= 4

        result["MAXVOL28"] = HHV(result["volume"], 28)
        result["MAX28_BAD"] = (result["volume"] == result["MAXVOL28"]) & result["REAL_YIN"]
        result["MAX28_OK"] = COUNT(result["MAX28_BAD"], 28) == 0

        branch_1 = (
            result["PLRY_CNT"] &
            result["YANGYIN_OK1"] &
            result["J_OK"] &
            result["MVOK"] &
            result["GOOD28"] &
            result["THREE_SUM_OK"] &
            result["MAX28_OK"]
        )
        branch_2 = (
            result["PLRY_CNT"] &
            result["YANGYIN_OK2"] &
            result["J_OK"] &
            result["MVOK"] &
            result["GOOD28"] &
            result["MAX28_OK"]
        )
        result["A1"] = branch_1 | branch_2

        result["HMSHORTWL"] = SMA(SMA(result["close"], 40, 4), 100, 50)
        result["HMLONGYL"] = 0.5 * (
            0.2 * MA(result["close"], 12) +
            0.3 * MA(result["close"], 24) +
            0.3 * MA(result["close"], 52) +
            0.2 * MA(result["close"], 108)
        ) + 0.5 * (
            0.4 * MA(result["close"], 20) +
            0.25 * MA(result["close"], 40) +
            0.25 * MA(result["close"], 80) +
            0.1 * MA(result["close"], 160)
        )

        tolerance = self.params["B1_TREND_TOLERANCE"]
        result["B1_MIN_J_COMPLEX_SIGNAL"] = (
            (result["HMSHORTWL"] >= result["HMLONGYL"] * tolerance) &
            (result["close"] >= result["HMLONGYL"] * tolerance) &
            result["A1"]
        )

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

        reasons = []
        if bool(latest.get("J_OK", False)):
            reasons.append("J值跌破动态Min J")
        if bool(latest.get("PLRY_CNT", False)):
            reasons.append("批量量入成立")
        if bool(latest.get("YANGYIN_OK1", False)):
            reasons.append("57日阳量优势")
        if bool(latest.get("YANGYIN_OK2", False)):
            reasons.append("14日阳量优势")
        if bool(latest.get("GOOD28", False)):
            reasons.append("近21日无高位放大量阴")
        if bool(latest.get("THREE_SUM_OK", False)):
            reasons.append("首量/连量/半缩量累计达标")
        if bool(latest.get("MAX28_OK", False)):
            reasons.append("近28日最大量非实阴")

        return [{
            "date": latest["date"],
            "close": round(float(latest["close"]), 2),
            "J": round(float(latest["J"]), 2),
            "MIN_J": round(float(latest["MIN_J"]), 2),
            "market_cap": round(float(latest["market_cap"]) / 1e8, 2) if pd.notna(latest.get("market_cap")) else 0,
            "reasons": reasons or ["满足 B1MinJComplex 条件"],
            "category": "b1_min_j_complex",
            "yangyin_ratio_57": round(
                float(latest["VOL_YANG1"]) / float(latest["VOL_YIN1"]) if float(latest["VOL_YIN1"]) > 0 else 999.0,
                2,
            ),
            "yangyin_ratio_14": round(
                float(latest["VOL_YANG2"]) / float(latest["VOL_YIN2"]) if float(latest["VOL_YIN2"]) > 0 else 999.0,
                2,
            ),
            "hm_short": round(float(latest["HMSHORTWL"]), 2),
            "hm_long": round(float(latest["HMLONGYL"]), 2),
        }]

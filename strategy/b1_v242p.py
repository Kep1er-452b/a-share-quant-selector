"""
B1 (V2.42P) 策略 - 通达信公式 Python 实现
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from utils.technical import COUNT, HHV, KDJ, LLV, MA, REF, SMA, SUM
from utils.strategy_labels import is_invalid_stock_name


B1_V242P_DEFAULT_PARAMS = {
    "J_MAX": 13,
    "MV_MIN_BILLION": 50,
    "YANGYIN_RATIO_28": 2.25,
    "YANGYIN_RATIO_14": 2.75,
    "PLRY_VOL_RATIO": 1.95,
    "HALF_DOWN_VOL_RATIO": 0.5,
    "TOP_RANGE_RATIO": 0.95,
    "FD15_VOL_RATIO": 1.2,
    "B1_TREND_TOLERANCE": 0.985,
}


def b1_v242p_default_params(extra=None) -> dict:
    params = dict(B1_V242P_DEFAULT_PARAMS)
    if extra:
        params.update(extra)
    return params


def apply_b1_v242p_signal(result: pd.DataFrame, params, j_ok_series=None) -> pd.DataFrame:
    """Refresh B1(V2.42P) signal columns from already-computed base indicators."""
    result["J_OK"] = j_ok_series if j_ok_series is not None else result["J"] <= params["J_MAX"]

    branch_1 = (
        result["PLRY_CNT"] &
        result["YANGYIN_OK1"] &
        result["J_OK"] &
        result["MVOK"] &
        result["GOOD28"] &
        result["THREE_SUM_OK"] &
        result["MAX14_OK"]
    )
    branch_2 = (
        result["PLRY_CNT"] &
        result["YANGYIN_OK2"] &
        result["J_OK"] &
        result["MVOK"] &
        result["GOOD28"] &
        result["MAX14_OK"]
    )
    result["A1"] = branch_1 | branch_2

    tolerance = params["B1_TREND_TOLERANCE"]
    result["B1_V242P_SIGNAL"] = (
        (result["HMSHORTWL"] >= result["HMLONGYL"] * tolerance) &
        (result["close"] >= result["HMLONGYL"] * tolerance) &
        result["A1"]
    )
    return result


def calculate_b1_v242p_indicators(df, params, j_ok_series=None) -> pd.DataFrame:
    """计算 B1(V2.42P) 条件列。

    j_ok_series 允许 B1MinJComplex 用动态 Min J 替换固定 J 阈值。
    """
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

    real_yang_volume = result["volume"] * result["REAL_YANG"].astype(int)
    real_yin_volume = result["volume"] * result["REAL_YIN"].astype(int)
    result["VOL_YANG1"] = result["VOL_REAL_YANG_28"] if "VOL_REAL_YANG_28" in result.columns else SUM(real_yang_volume, 28)
    result["VOL_YIN1"] = result["VOL_REAL_YIN_28"] if "VOL_REAL_YIN_28" in result.columns else SUM(real_yin_volume, 28)
    result["VOL_YANG2"] = result["VOL_REAL_YANG_14"] if "VOL_REAL_YANG_14" in result.columns else SUM(real_yang_volume, 14)
    result["VOL_YIN2"] = result["VOL_REAL_YIN_14"] if "VOL_REAL_YIN_14" in result.columns else SUM(real_yin_volume, 14)

    result["YANGYIN_OK1"] = result["VOL_YANG1"] > params["YANGYIN_RATIO_28"] * result["VOL_YIN1"]
    result["YANGYIN_OK2"] = result["VOL_YANG2"] > params["YANGYIN_RATIO_14"] * result["VOL_YIN2"]

    mv_min = params["MV_MIN_BILLION"] * 1e8
    market_cap = pd.to_numeric(result.get("market_cap", 0), errors="coerce").fillna(0)
    result["MV"] = market_cap / 1e8
    result["MVOK"] = market_cap >= mv_min

    o_llv = result["OPEN_LLV_21"] if "OPEN_LLV_21" in result.columns else LLV(result["open"], 21)
    o_hhv = result["OPEN_HHV_21"] if "OPEN_HHV_21" in result.columns else HHV(result["open"], 21)
    result["O85"] = o_llv + params["TOP_RANGE_RATIO"] * (o_hhv - o_llv)
    result["TOP15O"] = result["open"] >= result["O85"]
    result["FD15"] = (
        (result["close"] < ref_close_1) &
        (result["close"] <= result["open"]) &
        (result["volume"] >= params["FD15_VOL_RATIO"] * ref_vol_1)
    )
    result["CNT28"] = COUNT(result["TOP15O"] & result["FD15"], 21)
    result["GOOD28"] = result["CNT28"] <= 0

    result["AVG40"] = result["AVG_VOLUME_40"] if "AVG_VOLUME_40" in result.columns else MA(result["volume"], 40)
    result["PLRY"] = (
        (result["volume"] > params["PLRY_VOL_RATIO"] * ref_vol_1) &
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
        (result["volume"] <= params["HALF_DOWN_VOL_RATIO"] * ref_vol_1)
    )
    result["CNT_FIRST"] = COUNT(result["PLRY_FIRST"], 28)
    result["CNT_CONT"] = COUNT(result["PLRY_CONT"], 28)
    result["CNT_HALF"] = COUNT(result["HALF_DOWN"], 28)
    result["THREE_SUM_OK"] = (result["CNT_FIRST"] + result["CNT_CONT"] + result["CNT_HALF"]) >= 3

    result["MAXVOL14"] = HHV(result["volume"], 14)
    result["MAX14_BAD"] = (result["volume"] == result["MAXVOL14"]) & result["REAL_YIN"]
    result["MAX14_OK"] = COUNT(result["MAX14_BAD"], 14) == 0

    if "HMSHORTWL" not in result.columns:
        result["HMSHORTWL"] = SMA(SMA(result["close"], 40, 4), 100, 50)
    if "HMLONGYL" not in result.columns:
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

    return apply_b1_v242p_signal(result, params, j_ok_series=j_ok_series)


def build_b1_v242p_signal(latest, category="b1_v242p", fallback_reason="满足 B1(V2.42P) 条件") -> dict:
    reasons = []
    if bool(latest.get("PLRY_CNT", False)):
        reasons.append("批量量入成立")
    if bool(latest.get("YANGYIN_OK1", False)):
        reasons.append("28日阳量优势")
    if bool(latest.get("YANGYIN_OK2", False)):
        reasons.append("14日阳量优势")
    if bool(latest.get("GOOD28", False)):
        reasons.append("近21日无高位放大量阴")
    if bool(latest.get("THREE_SUM_OK", False)):
        reasons.append("首量/连量/半缩量累计达标")
    if bool(latest.get("MAX14_OK", False)):
        reasons.append("近14日最大量非实阴")

    return {
        "date": latest["date"],
        "close": round(float(latest["close"]), 2),
        "J": round(float(latest["J"]), 2),
        "market_cap": round(float(latest["market_cap"]) / 1e8, 2) if pd.notna(latest.get("market_cap")) else 0,
        "reasons": reasons or [fallback_reason],
        "category": category,
        "yangyin_ratio_28": round(
            float(latest["VOL_YANG1"]) / float(latest["VOL_YIN1"]) if float(latest["VOL_YIN1"]) > 0 else 999.0,
            2,
        ),
        "yangyin_ratio_14": round(
            float(latest["VOL_YANG2"]) / float(latest["VOL_YIN2"]) if float(latest["VOL_YIN2"]) > 0 else 999.0,
            2,
        ),
        "hm_short": round(float(latest["HMSHORTWL"]), 2),
        "hm_long": round(float(latest["HMLONGYL"]), 2),
    }


class B1V242PStrategy(BaseStrategy):
    """B1 (V2.42P) 策略"""

    def __init__(self, params=None):
        super().__init__("B1 (V2.42P)", b1_v242p_default_params(params))

    def calculate_indicators(self, df) -> pd.DataFrame:
        return calculate_b1_v242p_indicators(df, self.params)

    def select_stocks(self, df, stock_name='') -> list:
        if df.empty:
            return []

        if stock_name and is_invalid_stock_name(stock_name):
            return []

        latest = df.iloc[0]
        if latest.get("volume", 0) <= 0 or pd.isna(latest.get("close")):
            return []

        if not bool(latest.get("B1_V242P_SIGNAL", False)):
            return []

        return [build_b1_v242p_signal(latest)]

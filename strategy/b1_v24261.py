"""
B1 (V2.42.61) 策略 - 通达信公式 Python 实现
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.base_strategy import BaseStrategy
from utils.strategy_labels import is_invalid_stock_name
from utils.technical import COUNT, HHV, KDJ, LLV, MA, REF, SMA, SUM


B1_V24261_DEFAULT_PARAMS = {
    "J_MAX": 13,
    "MV_MIN_BILLION": 50,
    "YANGYIN_RATIO_28": 1.6,
    "YANGYIN_RATIO_14": 2.2,
    "YANGYIN_RATIO_57": 1.2,
    "PLRY_VOL_RATIO": 1.95,
    "HALF_DOWN_VOL_RATIO": 0.5,
    "TOP_RANGE_RATIO": 0.95,
    "FD15_VOL_RATIO": 1.2,
    "B1_TREND_TOLERANCE": 0.995,
}


def b1_v24261_default_params(extra=None) -> dict:
    params = dict(B1_V24261_DEFAULT_PARAMS)
    if extra:
        params.update(extra)
    return params


def ensure_b1_v24261_trend(result: pd.DataFrame) -> pd.DataFrame:
    if "HMSHORTWL" not in result.columns:
        result["HMSHORTWL"] = SMA(SMA(result["close"], 40, 4), 100, 50)
    if "HMLONGYL" not in result.columns:
        result["HMLONGYL"] = 0.5 * (
            0.2 * MA(result["close"], 12)
            + 0.3 * MA(result["close"], 24)
            + 0.3 * MA(result["close"], 52)
            + 0.2 * MA(result["close"], 108)
        ) + 0.5 * (
            0.4 * MA(result["close"], 20)
            + 0.25 * MA(result["close"], 40)
            + 0.25 * MA(result["close"], 80)
            + 0.1 * MA(result["close"], 160)
        )
    return result


def apply_b1_v24261_signal(result: pd.DataFrame, params, j_ok_series=None) -> pd.DataFrame:
    result["J_OK"] = j_ok_series if j_ok_series is not None else result["J"] <= params["J_MAX"]
    common = (
        result["PLRY_CNT"]
        & result["YANGYIN_OK3"]
        & result["J_OK"]
        & result["MVOK"]
        & result["GOOD28"]
        & result["MAX14_OK"]
    )
    result["A1"] = (
        common & result["YANGYIN_OK1"] & result["THREE_SUM_OK"]
    ) | (
        common & result["YANGYIN_OK2"]
    )

    tolerance = params["B1_TREND_TOLERANCE"]
    result["B1_V24261_SIGNAL"] = (
        (result["HMSHORTWL"] >= result["HMLONGYL"] * tolerance)
        & (result["close"] >= result["HMLONGYL"] * tolerance)
        & result["A1"]
    )
    return result


def build_b1_v24261_signal(
    latest,
    category="b1_v24261",
    fallback_reason="满足 B1(V2.42.61) 条件",
) -> dict:
    reasons = []
    if bool(latest.get("PLRY_CNT", False)):
        reasons.append("批量量入成立")
    if bool(latest.get("YANGYIN_OK1", False)):
        reasons.append("28日阳量优势")
    if bool(latest.get("YANGYIN_OK2", False)):
        reasons.append("14日阳量优势")
    if bool(latest.get("YANGYIN_OK3", False)):
        reasons.append("57日阳量优势")
    if bool(latest.get("GOOD28", False)):
        reasons.append("近14日无高位放大量阴")
    if bool(latest.get("THREE_SUM_OK", False)):
        reasons.append("首量/连量/半缩量累计达标")
    if bool(latest.get("MAX14_OK", False)):
        reasons.append("近14日最大量非实阴")

    return {
        "date": latest["date"],
        "close": round(float(latest["close"]), 2),
        "J": round(float(latest["J"]), 2),
        "market_cap": round(float(latest["MV"]), 2),
        "reasons": reasons or [fallback_reason],
        "category": category,
        "yangyin_ratio_28": _volume_ratio(latest, "VOL_YANG1", "VOL_YIN1"),
        "yangyin_ratio_14": _volume_ratio(latest, "VOL_YANG2", "VOL_YIN2"),
        "yangyin_ratio_57": _volume_ratio(latest, "VOL_YANG3", "VOL_YIN3"),
        "hm_short": round(float(latest["HMSHORTWL"]), 2),
        "hm_long": round(float(latest["HMLONGYL"]), 2),
    }


def _volume_ratio(latest, numerator, denominator):
    denominator_value = float(latest[denominator])
    if denominator_value <= 0:
        return 999.0
    return round(float(latest[numerator]) / denominator_value, 2)


class B1V24261Strategy(BaseStrategy):
    """B1 (V2.42.61) 策略。"""

    def __init__(self, params=None):
        super().__init__("B1 (V2.42.61)", b1_v24261_default_params(params))

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
        real_yang_volume = result["volume"] * result["REAL_YANG"].astype(int)
        real_yin_volume = result["volume"] * result["REAL_YIN"].astype(int)
        for window, suffix in ((28, "1"), (14, "2"), (57, "3")):
            yang_column = f"VOL_REAL_YANG_{window}"
            yin_column = f"VOL_REAL_YIN_{window}"
            result[f"VOL_YANG{suffix}"] = (
                result[yang_column] if yang_column in result.columns else SUM(real_yang_volume, window)
            )
            result[f"VOL_YIN{suffix}"] = (
                result[yin_column] if yin_column in result.columns else SUM(real_yin_volume, window)
            )

        result["YANGYIN_OK1"] = (
            result["VOL_YANG1"] > self.params["YANGYIN_RATIO_28"] * result["VOL_YIN1"]
        )
        result["YANGYIN_OK2"] = (
            result["VOL_YANG2"] > self.params["YANGYIN_RATIO_14"] * result["VOL_YIN2"]
        )
        result["YANGYIN_OK3"] = (
            result["VOL_YANG3"] > self.params["YANGYIN_RATIO_57"] * result["VOL_YIN3"]
        )

        # 本项目 CSV 仅保存总市值，沿用现有 B1/B2 对通达信 CAPITAL 的统一映射。
        market_cap = pd.to_numeric(result.get("market_cap", 0), errors="coerce").fillna(0)
        result["MV"] = market_cap / 1e8
        result["MVOK"] = result["MV"] >= self.params["MV_MIN_BILLION"]

        open_llv = result["OPEN_LLV_21"] if "OPEN_LLV_21" in result.columns else LLV(result["open"], 21)
        open_hhv = result["OPEN_HHV_21"] if "OPEN_HHV_21" in result.columns else HHV(result["open"], 21)
        result["O85"] = open_llv + self.params["TOP_RANGE_RATIO"] * (open_hhv - open_llv)
        result["TOP15O"] = result["open"] >= result["O85"]
        result["FD15"] = (
            (result["close"] < ref_close_1)
            & (result["close"] <= result["open"])
            & (result["volume"] >= self.params["FD15_VOL_RATIO"] * ref_vol_1)
        )
        result["CNT28"] = COUNT(result["TOP15O"] & result["FD15"], 14)
        result["GOOD28"] = result["CNT28"] <= 0

        result["AVG40"] = (
            result["AVG_VOLUME_40"] if "AVG_VOLUME_40" in result.columns else MA(result["volume"], 40)
        )
        result["PLRY"] = (
            (result["volume"] > self.params["PLRY_VOL_RATIO"] * ref_vol_1)
            & (result["close"] > result["open"])
            & (result["volume"] > result["AVG40"])
        )
        result["PLRY_CNT"] = (COUNT(result["PLRY"], 14) >= 2) | (COUNT(result["PLRY"], 57) >= 3)
        previous_plry = REF(result["PLRY"], 1).fillna(False).astype(bool)
        result["PLRY_FIRST"] = result["PLRY"] & ~previous_plry
        result["PLRY_CONT"] = result["PLRY"] & previous_plry
        result["PRE_NOT_REALYIN"] = ~REF(result["REAL_YIN"], 1).fillna(False).astype(bool)
        result["HALF_DOWN"] = (
            result["PRE_NOT_REALYIN"]
            & (result["close"] < ref_close_1)
            & (result["volume"] <= self.params["HALF_DOWN_VOL_RATIO"] * ref_vol_1)
        )
        result["CNT_FIRST"] = COUNT(result["PLRY_FIRST"], 28)
        result["CNT_CONT"] = COUNT(result["PLRY_CONT"], 28)
        result["CNT_HALF"] = COUNT(result["HALF_DOWN"], 28)
        result["THREE_SUM_OK"] = (
            result["CNT_FIRST"] + result["CNT_CONT"] + result["CNT_HALF"]
        ) >= 3

        result["MAXVOL14"] = HHV(result["volume"], 14)
        result["MAX14_BAD"] = (result["volume"] == result["MAXVOL14"]) & result["REAL_YIN"]
        result["MAX14_OK"] = COUNT(result["MAX14_BAD"], 14) == 0

        result = ensure_b1_v24261_trend(result)
        return apply_b1_v24261_signal(result, self.params)

    def select_stocks(self, df, stock_name="") -> list:
        if df.empty or (stock_name and is_invalid_stock_name(stock_name)):
            return []

        latest = df.iloc[0]
        if latest.get("volume", 0) <= 0 or pd.isna(latest.get("close")):
            return []
        if not bool(latest.get("B1_V24261_SIGNAL", False)):
            return []

        return [build_b1_v24261_signal(latest)]

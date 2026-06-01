"""
Runtime custom formula strategy.
"""
from __future__ import annotations

import pandas as pd

from strategy.base_strategy import BaseStrategy
from utils.formula_engine import FormulaError, compile_formula
from utils.strategy_labels import is_invalid_stock_name


FORMULA_STRATEGY_NAME = "FormulaStrategy"
FORMULA_DISPLAY_NAME = "条件公式选股"


class FormulaStrategy(BaseStrategy):
    """Evaluate a user-provided formula against the latest bar."""

    runtime_only = True

    def __init__(self, params=None):
        default_params = {
            "formula": "",
            "label": FORMULA_DISPLAY_NAME,
        }
        if params:
            default_params.update(params)
        super().__init__(FORMULA_DISPLAY_NAME, default_params)
        self.formula = str(self.params.get("formula") or "").strip()
        self.label = str(self.params.get("label") or FORMULA_DISPLAY_NAME).strip() or FORMULA_DISPLAY_NAME
        self._compiled_formula = compile_formula(self.formula) if self.formula else None

    def calculate_indicators(self, df) -> pd.DataFrame:
        result = df.copy()
        if not self._compiled_formula:
            result["FORMULA_MATCH"] = False
            return result
        result["FORMULA_MATCH"] = self._compiled_formula.evaluate(result)
        return result

    def select_stocks(self, df, stock_name="") -> list:
        if is_invalid_stock_name(stock_name) or df.empty or "FORMULA_MATCH" not in df.columns:
            return []

        latest = df.iloc[0]
        if not bool(latest.get("FORMULA_MATCH", False)):
            return []

        market_cap_yi = round(float(latest.get("market_cap", 0) or 0) / 1e8, 2)
        ref_volume = latest.get("ref_vol_1")
        volume_ratio = None
        if ref_volume not in (None, 0) and pd.notna(ref_volume):
            volume_ratio = round(float(latest.get("volume", 0) or 0) / float(ref_volume), 2)

        signal = {
            "category": "formula_match",
            "signal": self.label,
            "formula": self.formula,
            "close": round(float(latest.get("close", 0) or 0), 2),
            "J": round(float(latest.get("J", 0) or 0), 2),
            "market_cap": market_cap_yi,
            "reasons": [self.label, self.formula[:80]],
        }
        if volume_ratio is not None:
            signal["volume_ratio"] = volume_ratio
        return [signal]


def build_formula_params(formula: str, label: str | None = None) -> dict:
    formula_text = str(formula or "").strip()
    if not formula_text:
        raise FormulaError("公式不能为空")
    compile_formula(formula_text)
    return {
        "formula": formula_text,
        "label": str(label or FORMULA_DISPLAY_NAME).strip() or FORMULA_DISPLAY_NAME,
    }

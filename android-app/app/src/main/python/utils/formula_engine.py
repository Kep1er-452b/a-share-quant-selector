"""
Safe evaluator for a small Tongdaxin-like stock selection formula language.
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from functools import reduce
from typing import Any, Callable

import numpy as np
import pandas as pd

from utils.technical import COUNT, EMA, EXIST, HHV, LLV, MA, REF, SMA, SUM, prepare_selection_features


class FormulaError(ValueError):
    """Raised when a formula cannot be parsed or evaluated safely."""


_LOGICAL_REPLACEMENTS = (
    (re.compile(r"\bAND\b", re.IGNORECASE), "and"),
    (re.compile(r"\bOR\b", re.IGNORECASE), "or"),
    (re.compile(r"\bNOT\b", re.IGNORECASE), "not"),
)


_COLUMN_ALIASES = {
    "O": "open",
    "OPEN": "open",
    "H": "high",
    "HIGH": "high",
    "L": "low",
    "LOW": "low",
    "C": "close",
    "CLOSE": "close",
    "V": "volume",
    "VOL": "volume",
    "VOLUME": "volume",
    "AMO": "amount",
    "AMOUNT": "amount",
    "TURNOVER": "turnover",
    "CAP": "market_cap",
    "MARKET_CAP": "market_cap",
    "K": "K",
    "D": "D",
    "J": "J",
}


_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_COMPARE_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
}

_ALLOWED_FUNCTION_NAMES = {
    "MA",
    "EMA",
    "LLV",
    "HHV",
    "SMA",
    "REF",
    "EXIST",
    "COUNT",
    "SUM",
    "ABS",
    "MAX",
    "MIN",
    "CROSS",
    "BETWEEN",
}


def _preprocess_formula(source: str) -> str:
    text = str(source or "").strip()
    if not text:
        raise FormulaError("公式不能为空")
    if len(text) > 2000:
        raise FormulaError("公式长度不能超过 2000 个字符")

    text = re.sub(r"//.*", "", text)
    text = text.replace("&&", " AND ").replace("||", " OR ")
    text = re.sub(r"(?<![<>=!])!(?!=)", " not ", text)
    text = re.sub(r"(?<![<>=!])=(?!=)", "==", text)
    for pattern, replacement in _LOGICAL_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _ensure_series(value: Any, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.reindex(index)
    if isinstance(value, np.ndarray):
        if value.ndim != 1:
            raise FormulaError("公式函数只能返回一维序列")
        if value.size != len(index):
            raise FormulaError("公式函数返回长度与行情数据不一致")
        return pd.Series(value, index=index)
    return pd.Series([value] * len(index), index=index)


def _as_bool_series(value: Any, index: pd.Index) -> pd.Series:
    return _ensure_series(value, index).fillna(False).astype(bool)


def _elementwise_reduce(args: tuple[Any, ...], index: pd.Index, op: str) -> pd.Series:
    if not args:
        raise FormulaError(f"{op} 至少需要 1 个参数")
    frame = pd.concat([_ensure_series(arg, index) for arg in args], axis=1)
    if op == "MAX":
        return frame.max(axis=1)
    return frame.min(axis=1)


def _cross(left: Any, right: Any, index: pd.Index) -> pd.Series:
    left_series = _ensure_series(left, index)
    right_series = _ensure_series(right, index)
    return (left_series > right_series) & (REF(left_series, 1) <= REF(right_series, 1))


def _between(value: Any, lower: Any, upper: Any, index: pd.Index) -> pd.Series:
    series = _ensure_series(value, index)
    return (series >= lower) & (series <= upper)


@dataclass(frozen=True)
class CompiledFormula:
    source: str
    normalized_source: str
    tree: ast.Expression

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        evaluator = _FormulaEvaluator(df)
        value = evaluator.eval(self.tree.body)
        return _as_bool_series(value, evaluator.index)


class _FormulaEvaluator:
    def __init__(self, df: pd.DataFrame):
        if df is None or df.empty:
            raise FormulaError("行情数据为空")
        self.df = df
        self.index = df.index
        self.columns = {str(column).upper(): column for column in df.columns}
        self.functions = {
            "MA": MA,
            "EMA": EMA,
            "LLV": LLV,
            "HHV": HHV,
            "SMA": SMA,
            "REF": REF,
            "EXIST": EXIST,
            "COUNT": COUNT,
            "SUM": SUM,
            "ABS": abs,
            "MAX": lambda *args: _elementwise_reduce(args, self.index, "MAX"),
            "MIN": lambda *args: _elementwise_reduce(args, self.index, "MIN"),
            "CROSS": lambda left, right: _cross(left, right, self.index),
            "BETWEEN": lambda value, lower, upper: _between(value, lower, upper, self.index),
        }

    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise FormulaError("公式中只允许数字和布尔常量")

        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)

        if isinstance(node, ast.BinOp):
            op_func = _BINARY_OPS.get(type(node.op))
            if op_func is None:
                raise FormulaError("不支持的算术运算符")
            return op_func(self.eval(node.left), self.eval(node.right))

        if isinstance(node, ast.UnaryOp):
            operand = self.eval(node.operand)
            if isinstance(node.op, ast.Not):
                return ~_as_bool_series(operand, self.index)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return operand
            raise FormulaError("不支持的一元运算符")

        if isinstance(node, ast.BoolOp):
            values = [_as_bool_series(self.eval(item), self.index) for item in node.values]
            if isinstance(node.op, ast.And):
                return reduce(operator.and_, values)
            if isinstance(node.op, ast.Or):
                return reduce(operator.or_, values)
            raise FormulaError("不支持的逻辑运算符")

        if isinstance(node, ast.Compare):
            left = self.eval(node.left)
            comparisons = []
            for op_node, comparator in zip(node.ops, node.comparators):
                op_func = _COMPARE_OPS.get(type(op_node))
                if op_func is None:
                    raise FormulaError("不支持的比较运算符")
                right = self.eval(comparator)
                comparisons.append(_as_bool_series(op_func(left, right), self.index))
                left = right
            return reduce(operator.and_, comparisons)

        if isinstance(node, ast.Call):
            return self._eval_call(node)

        raise FormulaError(f"不支持的公式语法: {type(node).__name__}")

    def _resolve_name(self, name: str) -> Any:
        normalized = name.upper()
        if normalized == "TRUE":
            return True
        if normalized == "FALSE":
            return False

        column_name = _COLUMN_ALIASES.get(normalized)
        if column_name and column_name in self.df.columns:
            return pd.to_numeric(self.df[column_name], errors="coerce")

        if normalized in self.columns:
            return pd.to_numeric(self.df[self.columns[normalized]], errors="coerce")

        raise FormulaError(f"未知字段或变量: {name}")

    def _eval_call(self, node: ast.Call) -> Any:
        if node.keywords:
            raise FormulaError("公式函数不支持关键字参数")
        if not isinstance(node.func, ast.Name):
            raise FormulaError("公式函数名无效")
        function_name = node.func.id.upper()
        function = self.functions.get(function_name)
        if function is None:
            raise FormulaError(f"不支持的函数: {node.func.id}")
        args = [self.eval(arg) for arg in node.args]
        try:
            return function(*args)
        except FormulaError:
            raise
        except Exception as exc:
            raise FormulaError(f"{function_name} 调用失败: {exc}") from exc


def _validate_node(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _validate_node(node.body)
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            raise FormulaError("公式中只允许数字和布尔常量")
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINARY_OPS:
            raise FormulaError("不支持的算术运算符")
        _validate_node(node.left)
        _validate_node(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            raise FormulaError("不支持的一元运算符")
        _validate_node(node.operand)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise FormulaError("不支持的逻辑运算符")
        for value in node.values:
            _validate_node(value)
        return
    if isinstance(node, ast.Compare):
        for op_node in node.ops:
            if type(op_node) not in _COMPARE_OPS:
                raise FormulaError("不支持的比较运算符")
        _validate_node(node.left)
        for comparator in node.comparators:
            _validate_node(comparator)
        return
    if isinstance(node, ast.Call):
        if node.keywords or not isinstance(node.func, ast.Name):
            raise FormulaError("公式函数调用无效")
        if node.func.id.upper() not in _ALLOWED_FUNCTION_NAMES:
            raise FormulaError(f"不支持的函数: {node.func.id}")
        for arg in node.args:
            _validate_node(arg)
        return
    raise FormulaError(f"不支持的公式语法: {type(node).__name__}")


def compile_formula(source: str) -> CompiledFormula:
    normalized = _preprocess_formula(source)
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"公式语法错误: {exc.msg}") from exc
    _validate_node(tree)
    return CompiledFormula(source=str(source or "").strip(), normalized_source=normalized, tree=tree)


def evaluate_formula(source: str, df: pd.DataFrame) -> pd.Series:
    prepared_df = prepare_selection_features(df)
    return compile_formula(source).evaluate(prepared_df)

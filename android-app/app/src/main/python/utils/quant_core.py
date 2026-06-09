"""ctypes wrapper for the optional C quant core."""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path
from typing import Callable

import numpy as np


class QuantCoreUnavailable(RuntimeError):
    """Raised when the optional C core cannot serve a requested operation."""


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build" / "quant_core"
_LIB: ctypes.CDLL | None = None
_LOAD_ERROR: str | None = None

_DOUBLE_1D = np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS")
_INT8_1D = np.ctypeslib.ndpointer(dtype=np.int8, ndim=1, flags="C_CONTIGUOUS")


def _library_path() -> Path:
    override = os.environ.get("QUANT_CORE_LIBRARY")
    if override:
        return Path(override)
    system = platform.system()
    if system == "Darwin":
        return BUILD_DIR / "libquant_core.dylib"
    if system == "Windows":
        return BUILD_DIR / "quant_core.dll"
    return BUILD_DIR / "libquant_core.so"


def _configure(lib: ctypes.CDLL) -> None:
    int64 = ctypes.c_int64
    cint = ctypes.c_int
    lib.qc_rolling_mean_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_rolling_mean_forward.restype = cint
    lib.qc_rolling_sum_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_rolling_sum_forward.restype = cint
    lib.qc_rolling_min_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_rolling_min_forward.restype = cint
    lib.qc_rolling_max_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_rolling_max_forward.restype = cint
    lib.qc_count_forward.argtypes = [_INT8_1D, int64, cint, _DOUBLE_1D]
    lib.qc_count_forward.restype = cint
    lib.qc_exist_forward.argtypes = [_INT8_1D, int64, cint, _INT8_1D]
    lib.qc_exist_forward.restype = cint
    lib.qc_ref_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_ref_forward.restype = cint
    lib.qc_ema_forward.argtypes = [_DOUBLE_1D, int64, cint, _DOUBLE_1D]
    lib.qc_ema_forward.restype = cint
    lib.qc_sma_tdx_forward.argtypes = [_DOUBLE_1D, int64, cint, cint, _DOUBLE_1D]
    lib.qc_sma_tdx_forward.restype = cint
    lib.qc_kdj_ascending.argtypes = [
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        int64,
        cint,
        cint,
        cint,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
    ]
    lib.qc_kdj_ascending.restype = cint
    lib.qc_zhixing_trend_forward.argtypes = [
        _DOUBLE_1D,
        int64,
        cint,
        cint,
        cint,
        cint,
        _DOUBLE_1D,
        _DOUBLE_1D,
    ]
    lib.qc_zhixing_trend_forward.restype = cint
    lib.qc_prepare_selection_features_forward.argtypes = [
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        int64,
        cint,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _INT8_1D,
        _INT8_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
        _DOUBLE_1D,
    ]
    lib.qc_prepare_selection_features_forward.restype = cint


def _disabled() -> bool:
    return os.environ.get("QUANT_CORE_DISABLE") == "1"


def _library() -> ctypes.CDLL:
    global _LIB, _LOAD_ERROR
    if _disabled():
        raise QuantCoreUnavailable("quant core disabled by QUANT_CORE_DISABLE=1")
    if _LIB is not None:
        return _LIB
    path = _library_path()
    if not path.exists():
        _LOAD_ERROR = f"shared library not found: {path}"
        raise QuantCoreUnavailable(_LOAD_ERROR)
    try:
        lib = ctypes.CDLL(str(path))
        _configure(lib)
    except OSError as exc:
        _LOAD_ERROR = str(exc)
        raise QuantCoreUnavailable(str(exc)) from exc
    _LIB = lib
    _LOAD_ERROR = None
    return lib


def available() -> bool:
    try:
        _library()
    except QuantCoreUnavailable:
        return False
    return True


def load_error() -> str | None:
    return _LOAD_ERROR


def _as_float64(values) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64)


def _as_bool_i8(values) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.int8)


def _empty_float(length: int) -> np.ndarray:
    return np.empty(int(length), dtype=np.float64)


def _check(status: int, name: str) -> None:
    if status != 0:
        raise QuantCoreUnavailable(f"{name} failed with status {status}")


def _rolling(values, window: int, c_name: str) -> np.ndarray:
    arr = _as_float64(values)
    out = _empty_float(arr.size)
    func: Callable = getattr(_library(), c_name)
    _check(func(arr, arr.size, int(window), out), c_name)
    return out


def rolling_mean_forward(values, window: int) -> np.ndarray:
    return _rolling(values, window, "qc_rolling_mean_forward")


def rolling_sum_forward(values, window: int) -> np.ndarray:
    return _rolling(values, window, "qc_rolling_sum_forward")


def rolling_min_forward(values, window: int) -> np.ndarray:
    return _rolling(values, window, "qc_rolling_min_forward")


def rolling_max_forward(values, window: int) -> np.ndarray:
    return _rolling(values, window, "qc_rolling_max_forward")


def count_forward(values, window: int) -> np.ndarray:
    arr = _as_bool_i8(values)
    out = _empty_float(arr.size)
    _check(_library().qc_count_forward(arr, arr.size, int(window), out), "qc_count_forward")
    return out


def exist_forward(values, window: int) -> np.ndarray:
    arr = _as_bool_i8(values)
    out = np.empty(arr.size, dtype=np.int8)
    _check(_library().qc_exist_forward(arr, arr.size, int(window), out), "qc_exist_forward")
    return out.astype(bool)


def ref_forward(values, periods: int) -> np.ndarray:
    arr = _as_float64(values)
    out = _empty_float(arr.size)
    _check(_library().qc_ref_forward(arr, arr.size, int(periods), out), "qc_ref_forward")
    return out


def ema_forward(values, span: int) -> np.ndarray:
    arr = _as_float64(values)
    if np.isnan(arr).any():
        raise QuantCoreUnavailable("EMA NaN semantics are delegated to pandas fallback")
    out = _empty_float(arr.size)
    _check(_library().qc_ema_forward(arr, arr.size, int(span), out), "qc_ema_forward")
    return out


def sma_tdx_forward(values, period: int, weight: int) -> np.ndarray:
    arr = _as_float64(values)
    out = _empty_float(arr.size)
    _check(
        _library().qc_sma_tdx_forward(arr, arr.size, int(period), int(weight), out),
        "qc_sma_tdx_forward",
    )
    return out


def kdj_ascending(close, low, high, period: int = 9, m1: int = 3, m2: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    close_arr = _as_float64(close)
    low_arr = _as_float64(low)
    high_arr = _as_float64(high)
    if close_arr.size != low_arr.size or close_arr.size != high_arr.size:
        raise ValueError("KDJ arrays must have identical lengths")
    if np.isnan(close_arr).any() or np.isnan(low_arr).any() or np.isnan(high_arr).any():
        raise QuantCoreUnavailable("KDJ NaN semantics are delegated to pandas fallback")
    k = _empty_float(close_arr.size)
    d = _empty_float(close_arr.size)
    j = _empty_float(close_arr.size)
    _check(
        _library().qc_kdj_ascending(
            close_arr,
            low_arr,
            high_arr,
            close_arr.size,
            int(period),
            int(m1),
            int(m2),
            k,
            d,
            j,
        ),
        "qc_kdj_ascending",
    )
    return k, d, j


def zhixing_trend_forward(close, m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114) -> tuple[np.ndarray, np.ndarray]:
    close_arr = _as_float64(close)
    if np.isnan(close_arr).any():
        raise QuantCoreUnavailable("trend NaN semantics are delegated to pandas fallback")
    short = _empty_float(close_arr.size)
    bull = _empty_float(close_arr.size)
    _check(
        _library().qc_zhixing_trend_forward(
            close_arr,
            close_arr.size,
            int(m1),
            int(m2),
            int(m3),
            int(m4),
            short,
            bull,
        ),
        "qc_zhixing_trend_forward",
    )
    return short, bull


def selection_features_forward(
    open_,
    high,
    low,
    close,
    volume,
    *,
    include_trend: bool = True,
) -> dict[str, np.ndarray]:
    open_arr = _as_float64(open_)
    high_arr = _as_float64(high)
    low_arr = _as_float64(low)
    close_arr = _as_float64(close)
    volume_arr = _as_float64(volume)
    length = close_arr.size
    if not (open_arr.size == high_arr.size == low_arr.size == volume_arr.size == length):
        raise ValueError("selection feature arrays must have identical lengths")
    if any(np.isnan(arr).any() for arr in (open_arr, high_arr, low_arr, close_arr, volume_arr)):
        raise QuantCoreUnavailable("selection feature NaN semantics are delegated to pandas fallback")

    ref_close = _empty_float(length)
    ref_volume = _empty_float(length)
    real_yang = np.empty(length, dtype=np.int8)
    real_yin = np.empty(length, dtype=np.int8)
    k = _empty_float(length)
    d = _empty_float(length)
    j = _empty_float(length)
    short = _empty_float(length)
    bull = _empty_float(length)
    _check(
        _library().qc_prepare_selection_features_forward(
            open_arr,
            high_arr,
            low_arr,
            close_arr,
            volume_arr,
            length,
            int(include_trend),
            ref_close,
            ref_volume,
            real_yang,
            real_yin,
            k,
            d,
            j,
            short,
            bull,
        ),
        "qc_prepare_selection_features_forward",
    )
    result = {
        "ref_close_1": ref_close,
        "ref_vol_1": ref_volume,
        "REAL_YANG": real_yang.astype(bool),
        "REAL_YIN": real_yin.astype(bool),
        "K": k,
        "D": d,
        "J": j,
    }
    if include_trend:
        result["short_term_trend"] = short
        result["bull_bear_line"] = bull
    return result

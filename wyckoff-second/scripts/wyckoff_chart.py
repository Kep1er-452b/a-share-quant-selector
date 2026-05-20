#!/usr/bin/env python3
"""Create a Chinese-annotated Wyckoff chart from OHLCV CSV data.

The detector is intentionally heuristic. Use it for a first pass, then let the
agent override labels/ranges with --annotations when the chart evidence calls
for a more nuanced Wyckoff read.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import textwrap
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

try:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.dates as mdates
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    MPL_AVAILABLE = True
except ModuleNotFoundError:
    mdates = None
    fm = None
    plt = None
    mticker = None
    MPL_AVAILABLE = False


COLUMN_ALIASES = {
    "date": ["date", "datetime", "time", "日期", "交易日期", "时间"],
    "open": ["open", "开盘", "开盘价"],
    "high": ["high", "最高", "最高价"],
    "low": ["low", "最低", "最低价"],
    "close": ["close", "收盘", "收盘价", "adj_close", "复权收盘"],
    "volume": ["volume", "vol", "成交量", "成交额", "amount"],
}


@dataclass
class Event:
    term: str
    date: str
    price: float
    reason: str
    kind: str = "event"
    confidence: str = "medium"


@dataclass
class RangeBox:
    kind: str
    start: str
    end: str
    low: float
    high: float
    label: str


@dataclass
class Phase:
    label: str
    start: str
    end: str


def find_chinese_font() -> str | None:
    candidates = [
        "PingFang",
        "Heiti",
        "Songti",
        "Hiragino Sans GB",
        "Noto Sans CJK",
        "Noto Serif CJK",
        "Source Han Sans",
        "SimHei",
        "Microsoft YaHei",
        "WenQuanYi",
        "Arial Unicode",
    ]
    if fm is not None:
        for font in fm.fontManager.ttflist:
            haystack = f"{font.name} {font.fname}"
            if any(token.lower() in haystack.lower() for token in candidates):
                return font.fname
    font_dirs = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
    ]
    for root in font_dirs:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            haystack = str(path)
            if any(token.lower().replace(" ", "") in haystack.lower().replace(" ", "") for token in candidates):
                return str(path)
    return None


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower_map = {str(col).strip().lower(): col for col in df.columns}
    rename: dict[Any, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key in lower_map:
                rename[lower_map[key]] = target
                break
    df = df.rename(columns=rename)
    required = {"date", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV缺少必要列: {', '.join(sorted(missing))}")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "open" not in df.columns:
        df["open"] = df["close"]
    if "high" not in df.columns:
        df["high"] = df[["open", "close"]].max(axis=1)
    if "low" not in df.columns:
        df["low"] = df[["open", "close"]].min(axis=1)
    if "volume" not in df.columns:
        df["volume"] = np.nan
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        raise ValueError("CSV解析后没有可用行情行")
    return df


def load_prices(path: str, lookback: int) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="gb18030")
    df = normalize_columns(df)
    df["ma50"] = df["close"].rolling(50, min_periods=1).mean()
    df["ma200"] = df["close"].rolling(200, min_periods=1).mean()
    df["vol_ma20"] = df["volume"].rolling(20, min_periods=1).mean()
    return df.tail(lookback).copy().reset_index(drop=True)


def safe_ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def local_extrema(values: pd.Series, window: int, mode: str) -> list[int]:
    result: list[int] = []
    arr = values.to_numpy()
    for i in range(window, len(arr) - window):
        chunk = arr[i - window : i + window + 1]
        if mode == "min" and arr[i] == np.nanmin(chunk):
            result.append(i)
        if mode == "max" and arr[i] == np.nanmax(chunk):
            result.append(i)
    return result


def date_str(df: pd.DataFrame, idx: int) -> str:
    return pd.Timestamp(df.loc[idx, "date"]).strftime("%Y-%m-%d")


def price_at(df: pd.DataFrame, idx: int, col: str = "close") -> float:
    return float(df.loc[idx, col])


def dense_close_band(df: pd.DataFrame, start: int, end: int) -> tuple[float, float]:
    section = df.iloc[max(0, start) : max(start + 2, end + 1)]["close"].dropna()
    if len(section) < 5:
        section = df["close"].dropna()
    low = float(section.quantile(0.15))
    high = float(section.quantile(0.85))
    if math.isclose(low, high):
        pad = float(section.mean()) * 0.02 if len(section) else 1.0
        low, high = low - pad, high + pad
    return low, high


def has_volume(df: pd.DataFrame) -> bool:
    return df["volume"].notna().sum() >= max(20, len(df) // 5)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["spread"] = (out["high"] - out["low"]).abs()
    out["spread_ma20"] = out["spread"].rolling(20, min_periods=1).mean()
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=1).mean()
    out["vol_ratio"] = safe_ratio(out["volume"], out["vol_ma20"]).fillna(1.0)
    out["prev20"] = out["close"].shift(20)
    out["down_progress"] = safe_ratio(out["prev20"] - out["close"], out["prev20"]).fillna(0)
    out["up_progress"] = safe_ratio(out["close"] - out["prev20"], out["prev20"]).fillna(0)
    out["spread_ratio"] = safe_ratio(out["spread"], out["spread_ma20"]).fillna(1.0)
    return out


def detect_accumulation(df: pd.DataFrame) -> tuple[list[Event], list[RangeBox], list[Phase], str]:
    d = enrich(df)
    n = len(d)
    usable_end = max(30, int(n * 0.88))
    low_rank = 1 - d["low"].rank(pct=True)
    score = low_rank * 2.2 + d["down_progress"].clip(lower=0) * 4 + d["spread_ratio"] * 0.25
    if has_volume(d):
        score += d["vol_ratio"].clip(upper=4) * 0.35
    sc = int(score.iloc[:usable_end].idxmax())
    ar_window = d.iloc[sc + 1 : min(n, sc + 45)]
    if ar_window.empty:
        raise ValueError("not enough data after SC candidate")
    ar = int(ar_window["high"].idxmax())
    st_candidates = [i for i in local_extrema(d["low"], 5, "min") if ar < i < min(n, ar + 90)]
    if st_candidates:
        sc_low = price_at(d, sc, "low")
        st = min(st_candidates, key=lambda i: abs(price_at(d, i, "low") - sc_low))
    else:
        st = int(d.iloc[ar + 1 : min(n, ar + 70)]["low"].idxmin()) if ar + 2 < n else ar
    phase_b_start = min(st + 1, n - 1)
    phase_b_end_guess = min(n - 1, max(phase_b_start + 20, int((phase_b_start + n - 1) * 0.68)))
    band_low, band_high = dense_close_band(d, phase_b_start, phase_b_end_guess)

    spring = None
    for i in range(max(st + 1, phase_b_start), n):
        returned = d.loc[i, "low"] < band_low and d.loc[i, "close"] > band_low
        fast_reclaim = d.loc[i, "low"] < band_low and d["close"].iloc[i : min(n, i + 4)].max() > band_low
        if returned or fast_reclaim:
            spring = i
    sos = None
    search_from = spring or st
    for i in range(search_from + 1, n):
        strong_close = d.loc[i, "close"] > band_high
        volume_ok = (not has_volume(d)) or d.loc[i, "vol_ratio"] >= 1.05
        if strong_close and volume_ok:
            sos = i
            break
    lps = None
    if sos is not None and sos + 3 < n:
        candidates = [i for i in local_extrema(d["low"], 4, "min") if sos < i < n]
        above_danger = [i for i in candidates if d.loc[i, "low"] > band_low and d.loc[i, "close"] >= band_low]
        if above_danger:
            lps = above_danger[0]

    events = [
        Event("SC", date_str(d, sc), price_at(d, sc, "low"), "恐慌下跌出现大幅波动，CM开始接住公众抛盘。", confidence="medium"),
        Event("AR", date_str(d, ar), price_at(d, ar, "high"), "恐慌后的自动反弹，帮助画出交易区间上沿。", confidence="medium"),
        Event("ST", date_str(d, st), price_at(d, st, "low"), "二次测试回到低位附近，检验供应是否已经减弱。", confidence="medium"),
    ]
    if spring is not None:
        events.append(Event("Spring", date_str(d, spring), price_at(d, spring, "low"), "价格刺破支撑后收回区间，像压下的弹簧测试浮动供应。", confidence="medium"))
    if sos is not None:
        events.append(Event("SOS/JAC", date_str(d, sos), price_at(d, sos, "close"), "收盘越过区间上沿，需求试图跳过小溪。", confidence="medium"))
    if lps is not None:
        events.append(Event("LPS", date_str(d, lps), price_at(d, lps, "low"), "突破后的回踩没有破坏结构，供应暂时枯竭。", confidence="medium"))

    end_idx = sos if sos is not None else n - 1
    boxes = [RangeBox("accumulation", date_str(d, sc), date_str(d, end_idx), band_low, band_high, "吸筹区")]
    phases: list[Phase] = [
        Phase("Phase A", date_str(d, sc), date_str(d, st)),
    ]
    if spring is not None:
        phases.append(Phase("Phase B", date_str(d, min(st + 1, n - 1)), date_str(d, spring)))
        phases.append(Phase("Phase C", date_str(d, spring), date_str(d, sos or min(n - 1, spring + 15))))
    else:
        phases.append(Phase("Phase B", date_str(d, min(st + 1, n - 1)), date_str(d, sos or n - 1)))
    if sos is not None:
        phases.append(Phase("Phase D", date_str(d, sos), date_str(d, lps or n - 1)))
    if lps is not None and lps < n - 3:
        phases.append(Phase("Phase E", date_str(d, lps), date_str(d, n - 1)))
    summary = "倾向吸筹/再吸筹：低位停止行为后进入交易区间，重点观察供应是否持续枯竭以及突破后的回踩质量。"
    return events, boxes, phases, summary


def detect_distribution(df: pd.DataFrame) -> tuple[list[Event], list[RangeBox], list[Phase], str]:
    d = enrich(df)
    n = len(d)
    usable_end = max(30, int(n * 0.88))
    high_rank = d["high"].rank(pct=True)
    score = high_rank * 2.0 + d["up_progress"].clip(lower=0) * 3 + d["spread_ratio"] * 0.25
    if has_volume(d):
        score += d["vol_ratio"].clip(upper=4) * 0.35
    bc = int(score.iloc[:usable_end].idxmax())
    ar_window = d.iloc[bc + 1 : min(n, bc + 45)]
    if ar_window.empty:
        raise ValueError("not enough data after BC candidate")
    ar = int(ar_window["low"].idxmin())
    st_candidates = [i for i in local_extrema(d["high"], 5, "max") if ar < i < min(n, ar + 90)]
    if st_candidates:
        bc_high = price_at(d, bc, "high")
        st = min(st_candidates, key=lambda i: abs(price_at(d, i, "high") - bc_high))
    else:
        st = int(d.iloc[ar + 1 : min(n, ar + 70)]["high"].idxmax()) if ar + 2 < n else ar
    phase_b_start = min(st + 1, n - 1)
    phase_b_end_guess = min(n - 1, max(phase_b_start + 20, int((phase_b_start + n - 1) * 0.68)))
    band_low, band_high = dense_close_band(d, phase_b_start, phase_b_end_guess)

    ut = None
    for i in range(max(st + 1, phase_b_start), n):
        returned = d.loc[i, "high"] > band_high and d.loc[i, "close"] < band_high
        fast_fail = d.loc[i, "high"] > band_high and d["close"].iloc[i : min(n, i + 4)].min() < band_high
        if returned or fast_fail:
            ut = i
    sow = None
    search_from = ut or st
    for i in range(search_from + 1, n):
        weak_close = d.loc[i, "close"] < band_low
        volume_ok = (not has_volume(d)) or d.loc[i, "vol_ratio"] >= 1.0
        if weak_close and volume_ok:
            sow = i
            break
    lpsy = None
    if sow is not None and sow + 3 < n:
        candidates = [i for i in local_extrema(d["high"], 4, "max") if sow < i < n]
        below_resistance = [i for i in candidates if d.loc[i, "high"] < band_high or d.loc[i, "close"] < band_high]
        if below_resistance:
            lpsy = below_resistance[0]

    events = [
        Event("BC", date_str(d, bc), price_at(d, bc, "high"), "高位抢购把公众需求推到高潮，CM有条件派发。", confidence="medium"),
        Event("AR/SOW", date_str(d, ar), price_at(d, ar, "low"), "买盘退潮后的快速回落，画出交易区间下沿。", confidence="medium"),
        Event("ST", date_str(d, st), price_at(d, st, "high"), "反弹测试前高，观察需求是否已经缩短。", confidence="medium"),
    ]
    if ut is not None:
        events.append(Event("UT/UTAD", date_str(d, ut), price_at(d, ut, "high"), "上冲阻力后回到区间，说明突破可能只是诱多。", confidence="medium"))
    if sow is not None:
        events.append(Event("SOW/破冰", date_str(d, sow), price_at(d, sow, "close"), "跌破区间下沿，供应开始取得控制权。", confidence="medium"))
    if lpsy is not None:
        events.append(Event("LPSY", date_str(d, lpsy), price_at(d, lpsy, "high"), "破位后的反弹无力，需求不能夺回阻力。", confidence="medium"))

    end_idx = sow if sow is not None else n - 1
    boxes = [RangeBox("distribution", date_str(d, bc), date_str(d, end_idx), band_low, band_high, "派发区")]
    phases: list[Phase] = [Phase("Phase A", date_str(d, bc), date_str(d, st))]
    if ut is not None:
        phases.append(Phase("Phase B", date_str(d, min(st + 1, n - 1)), date_str(d, ut)))
        phases.append(Phase("Phase C", date_str(d, ut), date_str(d, sow or min(n - 1, ut + 15))))
    else:
        phases.append(Phase("Phase B", date_str(d, min(st + 1, n - 1)), date_str(d, sow or n - 1)))
    if sow is not None:
        phases.append(Phase("Phase D", date_str(d, sow), date_str(d, lpsy or n - 1)))
    if lpsy is not None and lpsy < n - 3:
        phases.append(Phase("Phase E", date_str(d, lpsy), date_str(d, n - 1)))
    summary = "倾向派发/再派发：高位需求缩短后进入交易区间，重点观察供应是否能打穿冰线以及反弹是否无力。"
    return events, boxes, phases, summary


def choose_structure(df: pd.DataFrame) -> dict[str, Any]:
    d = enrich(df)
    last = float(d["close"].iloc[-1])
    try:
        acc = detect_accumulation(d)
    except Exception:
        acc = ([], [], [], "")
    try:
        dist = detect_distribution(d)
    except Exception:
        dist = ([], [], [], "")

    def structure_score(result: tuple[list[Event], list[RangeBox], list[Phase], str], mode: str) -> float:
        events, boxes, _phases, _summary = result
        if not events or not boxes:
            return -999
        terms = " ".join(e.term for e in events)
        box = boxes[0]
        score = 0.0
        if mode == "accumulation":
            score += 2.0 if "SOS" in terms else 0.0
            score += 1.0 if "Spring" in terms else 0.0
            score += 2.0 if last > box.high else 0.0
            score -= 1.5 if last < box.low else 0.0
        else:
            score += 2.0 if "SOW" in terms or "破冰" in terms else 0.0
            score += 1.0 if "UT" in terms else 0.0
            score += 2.0 if last < box.low else 0.0
            score -= 1.5 if last > box.high else 0.0
        score += min(len(events), 6) * 0.08
        return score

    acc_bias = structure_score(acc, "accumulation")
    dist_bias = structure_score(dist, "distribution")
    if dist_bias > acc_bias and dist[0]:
        events, boxes, phases, summary = dist
        mode = "distribution"
    elif acc[0]:
        events, boxes, phases, summary = acc
        mode = "accumulation"
    else:
        events, boxes, phases, summary = [], [], [], "结构证据不足：先标出趋势和均线，等待更清楚的交易区间。"
        mode = "unclear"
    return {
        "mode": mode,
        "summary": summary,
        "events": [asdict(x) for x in events],
        "ranges": [asdict(x) for x in boxes],
        "phases": [asdict(x) for x in phases],
        "checks": {
            "rows": int(len(d)),
            "has_volume": bool(has_volume(d)),
            "start": date_str(d, 0),
            "end": date_str(d, len(d) - 1),
        },
    }


def merge_annotations(auto: dict[str, Any], annotations_path: str | None) -> dict[str, Any]:
    if not annotations_path:
        return auto
    with open(annotations_path, "r", encoding="utf-8") as f:
        manual = json.load(f)
    merged = dict(auto)
    for key in ["mode", "summary", "events", "ranges", "phases"]:
        if key in manual:
            merged[key] = manual[key]
    merged["manual_override"] = True
    return merged


def wrap_label(text: str, width: int = 18, max_lines: int | None = None) -> str:
    """Wrap Chinese/English labels into predictable short lines."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return ""
    lines = textwrap.wrap(
        normalized,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
    )
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("，。；;,. ") + "..."
    return "\n".join(lines)


def draw_chart(df: pd.DataFrame, analysis: dict[str, Any], output: str, title: str | None) -> None:
    if not MPL_AVAILABLE:
        draw_chart_pillow(df, analysis, output, title)
        return
    font_path = find_chinese_font()
    if font_path:
        font = fm.FontProperties(fname=font_path)
        plt.rcParams["font.family"] = font.get_name()
    plt.rcParams["axes.unicode_minus"] = False

    volume_available = has_volume(df)
    if volume_available:
        fig, (ax, vol_ax) = plt.subplots(
            2, 1, figsize=(18, 10.5), sharex=True, gridspec_kw={"height_ratios": [4.4, 1.0]}
        )
    else:
        fig, ax = plt.subplots(1, 1, figsize=(18, 9.5))
        vol_ax = None
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.09, hspace=0.06)

    dates = pd.to_datetime(df["date"])
    ax.plot(dates, df["close"], color="black", lw=1.6, label="收盘价")
    ax.plot(dates, df["ma50"], color="#1f77b4", lw=1.1, ls="--", label="MA50")
    ax.plot(dates, df["ma200"], color="#d62728", lw=1.1, ls="--", label="MA200")

    for box in analysis.get("ranges", []):
        start = pd.to_datetime(box["start"])
        end = pd.to_datetime(box["end"])
        color = "#8fd19e" if box.get("kind") == "accumulation" else "#f2a0a0"
        ax.fill_between([start, end], float(box["low"]), float(box["high"]), color=color, alpha=0.24)
        ax.text(start, float(box["high"]), f" {box.get('label', '')}", color="#333333", va="bottom", fontsize=12)

    y_min = float(np.nanmin(df["low"]))
    y_max = float(np.nanmax(df["high"]))
    y_pad = (y_max - y_min) * 0.12 or y_max * 0.05 or 1.0
    ax.set_ylim(y_min - y_pad, y_max + y_pad)
    for idx, phase in enumerate(analysis.get("phases", [])):
        start = pd.to_datetime(phase["start"])
        end = pd.to_datetime(phase["end"])
        ax.axvline(start, color="black", ls="--", lw=2.0, alpha=0.75)
        mid = start + (end - start) / 2
        phase_y = y_max + y_pad * (0.62 if idx % 2 == 0 else 0.42)
        phase_label = wrap_label(phase["label"], width=9, max_lines=2)
        ax.text(
            mid,
            phase_y,
            phase_label,
            color="#b30000",
            ha="center",
            va="center",
            fontsize=15,
            fontweight="bold",
        )
    if analysis.get("phases"):
        ax.axvline(pd.to_datetime(analysis["phases"][-1]["end"]), color="black", ls="--", lw=1.4, alpha=0.45)

    offsets = [(45, 54), (65, -64), (-130, 55), (-135, -72), (88, 88), (-155, 94), (120, -118), (-180, -118)]
    for idx, event in enumerate(analysis.get("events", [])):
        x = pd.to_datetime(event["date"])
        y = float(event["price"])
        reason = event.get("reason", "")
        label = wrap_label(f"{event['term']}：{reason}", width=18, max_lines=3)
        dx, dy = offsets[idx % len(offsets)]
        event_pos = dates.searchsorted(x)
        if event_pos > len(dates) * 0.82 and dx > 0:
            dx = -abs(dx) - 24
        elif event_pos < len(dates) * 0.18 and dx < 0:
            dx = abs(dx) + 24
        y_ratio = (y - y_min) / ((y_max - y_min) or 1.0)
        if y_ratio > 0.78 and dy > 0:
            dy = -abs(dy)
        elif y_ratio < 0.22 and dy < 0:
            dy = abs(dy)
        ax.scatter([x], [y], s=48, color="#111111", zorder=5)
        ax.annotate(
            label,
            xy=(x, y),
            xytext=(dx, dy),
            textcoords="offset points",
            arrowprops={"arrowstyle": "->", "lw": 1.0, "color": "#333333"},
            bbox={"boxstyle": "round,pad=0.28", "fc": "white", "ec": "#777777", "alpha": 0.9},
            fontsize=10.5,
            ha="left" if dx >= 0 else "right",
            va="center",
            annotation_clip=False,
        )

    chart_title = title or "威科夫二世：市场结构读图"
    latest = df.iloc[-1]
    title_line = (
        f"{chart_title}\n"
        f"末日收盘 {float(latest['close']):.2f} | MA50 {float(latest['ma50']):.2f} | MA200 {float(latest['ma200']):.2f}"
    )
    fig.suptitle(title_line, fontsize=18, fontweight="bold", x=0.07, y=0.985, ha="left")
    ax.set_ylabel("价格")
    ax.grid(True, color="#d8d8d8", lw=0.7, alpha=0.6)
    ax.legend(loc="upper left", frameon=False, ncols=3)

    if volume_available and vol_ax is not None:
        colors = np.where(df["close"] >= df["open"], "#5aa469", "#c44e52")
        vol_ax.bar(dates, df["volume"], color=colors, alpha=0.35, width=1.0, label="成交量")
        if "vol_ma20" in df.columns:
            vol_ax.plot(dates, df["vol_ma20"], color="#555555", lw=1.1, label="20日均量")
        vol_ax.set_ylabel("成交量\n(亿股)")
        if mticker is not None:
            vol_ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _pos: f"{value / 1e8:.2f}"))
        vol_ax.grid(True, axis="y", color="#e2e2e2", lw=0.6, alpha=0.6)
        vol_ax.legend(loc="upper left", frameon=False, fontsize=9)

    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.text(
        0.01,
        0.02,
        "注：威科夫判断强调供求与行为证据，不构成投资建议。",
        fontsize=9,
        color="#555555",
    )
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def load_pil_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = find_chinese_font()
    if font_path:
        try:
            return ImageFont.truetype(font_path, size=size)
        except Exception:
            pass
    for path in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_dashed_line(draw: ImageDraw.ImageDraw, xy: tuple[float, float, float, float], fill: str, width: int = 2, dash: int = 10) -> None:
    x1, y1, x2, y2 = xy
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    steps = int(length // dash)
    for i in range(0, steps, 2):
        start = i / steps
        end = min((i + 1) / steps, 1)
        draw.line((x1 + (x2 - x1) * start, y1 + (y2 - y1) * start, x1 + (x2 - x1) * end, y1 + (y2 - y1) * end), fill=fill, width=width)


def draw_polyline(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], fill: str, width: int = 2, dashed: bool = False) -> None:
    clean = [(x, y) for x, y in points if np.isfinite(x) and np.isfinite(y)]
    if len(clean) < 2:
        return
    for a, b in zip(clean, clean[1:]):
        if dashed:
            draw_dashed_line(draw, (a[0], a[1], b[0], b[1]), fill=fill, width=width)
        else:
            draw.line((a[0], a[1], b[0], b[1]), fill=fill, width=width)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=4)
    return box[2] - box[0], box[3] - box[1]


def parse_date_index(df: pd.DataFrame) -> dict[str, int]:
    return {pd.Timestamp(x).strftime("%Y-%m-%d"): i for i, x in enumerate(pd.to_datetime(df["date"]))}


def draw_chart_pillow(df: pd.DataFrame, analysis: dict[str, Any], output: str, title: str | None) -> None:
    width, height = 1800, 1120
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = load_pil_font(34)
    big_font = load_pil_font(30)
    label_font = load_pil_font(22)
    small_font = load_pil_font(18)

    left, right = 110, width - 70
    top, price_bottom = 130, 820
    volume_top, volume_bottom = 865, 1010
    n = len(df)
    if n < 2:
        raise ValueError("绘图至少需要两行行情数据")
    y_min = float(np.nanmin(df["low"]))
    y_max = float(np.nanmax(df["high"]))
    y_pad = (y_max - y_min) * 0.12 or y_max * 0.05 or 1.0
    y_min -= y_pad
    y_max += y_pad

    def x_at(i: int) -> float:
        return left + (right - left) * i / max(n - 1, 1)

    def y_at(value: float) -> float:
        return price_bottom - (value - y_min) * (price_bottom - top) / max(y_max - y_min, 1e-9)

    dates = pd.to_datetime(df["date"])
    date_to_i = parse_date_index(df)

    for frac in np.linspace(0, 1, 6):
        y = top + (price_bottom - top) * frac
        draw.line((left, y, right, y), fill=(218, 218, 218, 170), width=1)
        value = y_max - (y_max - y_min) * frac
        draw.text((18, y - 12), f"{value:.2f}", fill="#444444", font=small_font)
    draw.rectangle((left, top, right, price_bottom), outline="#222222", width=2)

    for box in analysis.get("ranges", []):
        start_i = date_to_i.get(str(box["start"]), 0)
        end_i = date_to_i.get(str(box["end"]), n - 1)
        color = (143, 209, 158, 64) if box.get("kind") == "accumulation" else (242, 160, 160, 64)
        x1, x2 = x_at(start_i), x_at(end_i)
        y1, y2 = y_at(float(box["high"])), y_at(float(box["low"]))
        draw.rectangle((x1, y1, x2, y2), fill=color, outline=None)
        draw.text((x1 + 8, y1 - 28), box.get("label", ""), fill="#333333", font=label_font)

    close_points = [(x_at(i), y_at(float(v))) for i, v in enumerate(df["close"])]
    ma50_points = [(x_at(i), y_at(float(v))) for i, v in enumerate(df["ma50"])]
    ma200_points = [(x_at(i), y_at(float(v))) for i, v in enumerate(df["ma200"])]
    draw_polyline(draw, close_points, "#111111", width=4)
    draw_polyline(draw, ma50_points, "#1f77b4", width=3, dashed=True)
    draw_polyline(draw, ma200_points, "#d62728", width=3, dashed=True)

    phase_y = top + 28
    for phase in analysis.get("phases", []):
        start_i = date_to_i.get(str(phase["start"]), 0)
        end_i = date_to_i.get(str(phase["end"]), n - 1)
        x = x_at(start_i)
        draw_dashed_line(draw, (x, top, x, price_bottom), fill="#000000", width=4, dash=16)
        mid = x_at(max(0, min(n - 1, int((start_i + end_i) / 2))))
        label = str(phase["label"])
        tw, th = text_size(draw, label, big_font)
        draw.text((mid - tw / 2, phase_y - th / 2), label, fill="#b30000", font=big_font)
    if analysis.get("phases"):
        end_i = date_to_i.get(str(analysis["phases"][-1]["end"]), n - 1)
        draw_dashed_line(draw, (x_at(end_i), top, x_at(end_i), price_bottom), fill="#333333", width=2, dash=14)

    offsets = [
        (42, 44),
        (48, -88),
        (-260, 48),
        (-260, -92),
        (55, 110),
        (-270, 110),
        (80, -145),
        (-300, -145),
        (120, 20),
        (-340, 20),
    ]
    occupied: list[tuple[float, float, float, float]] = []

    def overlaps(box: tuple[float, float, float, float]) -> bool:
        x1, y1, x2, y2 = box
        for ox1, oy1, ox2, oy2 in occupied:
            if x1 < ox2 and x2 > ox1 and y1 < oy2 and y2 > oy1:
                return True
        return False

    for idx, event in enumerate(analysis.get("events", [])):
        i = date_to_i.get(str(event["date"]))
        if i is None:
            continue
        x, y = x_at(i), y_at(float(event["price"]))
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill="#111111")
        label = wrap_label(f"{event['term']}：{event.get('reason', '')}", width=16)
        tw, th = text_size(draw, label, small_font)
        chosen = None
        for shift in range(len(offsets)):
            dx, dy = offsets[(idx + shift) % len(offsets)]
            tx = min(max(12, x + dx), width - tw - 24)
            ty = min(max(top + 10, y + dy), price_bottom - th - 10)
            candidate = (tx - 8, ty - 6, tx + tw + 8, ty + th + 8)
            if not overlaps(candidate):
                chosen = (tx, ty, candidate)
                break
        if chosen is None:
            dx, dy = offsets[idx % len(offsets)]
            tx = min(max(12, x + dx), width - tw - 24)
            ty = min(max(top + 10, y + dy + idx * 18), price_bottom - th - 10)
            chosen = (tx, ty, (tx - 8, ty - 6, tx + tw + 8, ty + th + 8))
        tx, ty, label_box = chosen
        occupied.append(label_box)
        draw.line((x, y, tx, ty + th / 2), fill="#333333", width=2)
        draw.rectangle(label_box, fill=(255, 255, 255, 226), outline="#777777")
        draw.multiline_text((tx, ty), label, fill="#111111", font=small_font, spacing=4)

    if has_volume(df):
        max_vol = float(np.nanmax(df["volume"])) or 1.0
        draw.rectangle((left, volume_top, right, volume_bottom), outline="#333333", width=1)
        for i, row in df.iterrows():
            x = x_at(i)
            bar_w = max(1, (right - left) / n * 0.68)
            bar_h = float(row["volume"]) / max_vol * (volume_bottom - volume_top)
            color = (90, 164, 105, 150) if row["close"] >= row["open"] else (196, 78, 82, 150)
            draw.rectangle((x - bar_w / 2, volume_bottom - bar_h, x + bar_w / 2, volume_bottom), fill=color)
        draw.text((18, volume_top + 8), "成交量", fill="#444444", font=small_font)

    tick_count = min(8, n)
    for i in np.linspace(0, n - 1, tick_count, dtype=int):
        label = pd.Timestamp(dates.iloc[i]).strftime("%Y-%m-%d")
        x = x_at(int(i))
        draw.text((x - 48, volume_bottom + 18), label, fill="#444444", font=small_font)

    chart_title = title or "威科夫二世：市场结构读图"
    summary = str(analysis.get("summary", ""))
    draw.text((left, 28), chart_title, fill="#111111", font=title_font)
    draw.text((left, 74), wrap_label(summary, width=58), fill="#333333", font=label_font)
    legend_x = right - 420
    draw.line((legend_x, 52, legend_x + 42, 52), fill="#111111", width=4)
    draw.text((legend_x + 52, 40), "收盘价", fill="#111111", font=small_font)
    draw_dashed_line(draw, (legend_x + 150, 52, legend_x + 192, 52), fill="#1f77b4", width=3)
    draw.text((legend_x + 202, 40), "MA50", fill="#111111", font=small_font)
    draw_dashed_line(draw, (legend_x + 282, 52, legend_x + 324, 52), fill="#d62728", width=3)
    draw.text((legend_x + 334, 40), "MA200", fill="#111111", font=small_font)

    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Wyckoff Chinese annotated chart from CSV.")
    parser.add_argument("csv", help="OHLCV CSV file")
    parser.add_argument("--output", "-o", default="wyckoff_chart.png", help="Output PNG path")
    parser.add_argument("--lookback", type=int, default=500, help="Rows to plot after sorting by date")
    parser.add_argument("--annotations", help="Optional JSON with events/ranges/phases to override auto detection")
    parser.add_argument("--analysis-output", help="Optional JSON path for detected/merged analysis")
    parser.add_argument("--title", help="Chart title")
    args = parser.parse_args()

    df = load_prices(args.csv, args.lookback)
    analysis = merge_annotations(choose_structure(df), args.annotations)
    output = str(Path(args.output).expanduser())
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    draw_chart(df, analysis, output, args.title)

    if args.analysis_output:
        analysis_path = Path(args.analysis_output).expanduser()
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
    print(json.dumps({"output": os.path.abspath(output), "analysis": analysis}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

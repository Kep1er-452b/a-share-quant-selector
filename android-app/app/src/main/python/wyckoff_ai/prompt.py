"""Prompt assembly for DeepSeek Wyckoff analysis."""

from __future__ import annotations

import json
from pathlib import Path

from .data import ANALYSIS_ROWS, compact_csv


PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "wyckoff_analyzer_v1.md"


def load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "你是专业的威科夫行情图分析师。只输出 JSON，不输出 Markdown。"


def build_messages(symbol: str, stock_name: str, df) -> list[dict[str, str]]:
    latest_date = df["date"].iloc[-1].strftime("%Y-%m-%d") if len(df) else ""
    csv_payload = compact_csv(df)
    latest = df.iloc[-1]
    close_min = float(df["close"].min())
    close_max = float(df["close"].max())
    recent_60 = df.tail(60)
    payload = {
        "symbol": symbol,
        "stock_name": stock_name,
        "latest_date": latest_date,
        "rows_sent": int(len(df)),
        "analysis_focus": f"重点分析最近 {ANALYSIS_ROWS} 个交易日，但可参考全部已发送数据。",
        "columns": ["date", "open", "high", "low", "close", "volume", "ma50", "ma200", "vol_ratio", "pct_change"],
        "summary": {
            "latest_close": round(float(latest["close"]), 4),
            "latest_ma50": round(float(latest["ma50"]), 4),
            "latest_ma200": round(float(latest["ma200"]), 4),
            "sent_close_min": round(close_min, 4),
            "sent_close_max": round(close_max, 4),
            "recent_60_close_min": round(float(recent_60["close"].min()), 4),
            "recent_60_close_max": round(float(recent_60["close"].max()), 4),
        },
    }
    adjustment_repairs = getattr(df, "attrs", {}).get("adjustment_repairs") or []
    if adjustment_repairs:
        payload["data_quality"] = {
            "adjustment_repairs": adjustment_repairs,
            "note": "本地 CSV 存在疑似除权/复权口径断层，威科夫分析已使用临时连续复权价格，不改变源 CSV。",
        }
    user_content = (
        "请基于下面本地 CSV 清洗后的真实行情数据做威科夫结构分析。"
        "只能引用数据中真实存在的日期；ranges/phases/events 的日期都不能使用周末或节假日。"
        "mode 只能逐字使用固定枚举: accumulation/distribution/markup/markdown/reaccumulation/redistribution/unclear。"
        "事件价格必须贴近当天 OHLC。"
        "证据不足时请输出 unclear，不要强行凑 Phase 或事件。"
        "请输出接近专业人工读图的长分析，不要只给摘要；summary_text 至少覆盖背景、事件链、价位、确认条件和失效条件。"
        "必须返回非空 JSON 对象。\n\n"
        f"元数据 JSON:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"OHLCV CSV:\n{csv_payload}"
    )
    return [
        {"role": "system", "content": load_system_prompt()},
        {"role": "user", "content": user_content},
    ]

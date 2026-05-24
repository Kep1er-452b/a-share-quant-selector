"""End-to-end Wyckoff AI analysis pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from utils.csv_manager import CSVManager
from utils.stock_exporter import resolve_stock_query

from .client import DEEPSEEK_MODEL, DeepSeekWyckoffClient, resolve_deepseek_api_key
from .data import load_stock_csv, model_frame
from .naming import build_wyckoff_output_paths, get_latest_data_date
from .prompt import build_messages
from .renderer import render_chart
from .schema import validate_analysis


class WyckoffPipelineError(RuntimeError):
    """Raised when a Wyckoff run fails."""


def has_deepseek_config(config: dict | None = None) -> bool:
    return bool(resolve_deepseek_api_key(config))


class WyckoffPipeline:
    def __init__(
        self,
        config: dict | None = None,
        data_dir: str = "data",
        output_dir: str | Path = "outputs/wyckoff",
    ):
        self.config = config or {}
        self.data_dir = data_dir
        self.output_dir = Path(output_dir)
        self.csv_manager = CSVManager(data_dir)

    def _resolve_stock(self, query: str) -> dict[str, Any]:
        match = resolve_stock_query(query, data_dir=self.data_dir)
        if not match:
            raise WyckoffPipelineError(f"未找到匹配股票: {query}")
        code = CSVManager.validate_stock_code(match["code"])
        csv_path = self.csv_manager.get_stock_path(code, create_dirs=False)
        if not csv_path.exists():
            raise WyckoffPipelineError(f"本地 CSV 不存在: {code}")
        return {**match, "code": code, "csv_path": csv_path}

    def analyze_stock(self, query: str, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        def emit(step: str, message: str, progress_pct: int) -> None:
            if progress_callback:
                progress_callback({
                    "step": step,
                    "message": message,
                    "progress_pct": progress_pct,
                })

        query = str(query or "").strip()
        if not query:
            raise WyckoffPipelineError("请输入股票代码、名称或拼音")

        emit("resolve", "正在解析股票代码/名称，并定位本地 CSV。", 5)
        stock = self._resolve_stock(query)
        emit("load_csv", f"已匹配 {stock['code']} {stock.get('name') or ''}，正在读取本地行情。", 12)
        df = load_stock_csv(stock["csv_path"])
        emit("indicators", "正在标准化 OHLCV，并计算 MA50、MA200、成交量比率。", 22)
        recent = model_frame(df)
        data_date = get_latest_data_date(df)
        run_started_at = datetime.now()
        generated_at = run_started_at.strftime("%Y-%m-%d %H:%M:%S")
        emit("prepare_outputs", f"数据日期 {data_date}，正在准备输出文件路径。", 30)
        paths = build_wyckoff_output_paths(
            symbol=stock["code"],
            stock_name=stock.get("name"),
            data_date=data_date,
            output_dir=self.output_dir,
            run_timestamp=run_started_at,
        )
        for path in paths.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        api_key = resolve_deepseek_api_key(self.config)
        if not api_key:
            raise WyckoffPipelineError("未配置 DeepSeek API Key，请在 config/config_local.yaml 或 DEEPSEEK_API_KEY 中配置")

        emit("build_prompt", f"正在压缩最近 {len(recent)} 根日线，构建威科夫分析上下文。", 38)
        messages = build_messages(stock["code"], stock.get("name") or "", recent)
        timeout_seconds = float(self.config.get("wyckoff_ai", {}).get("timeout_seconds", 90))
        client = DeepSeekWyckoffClient(api_key=api_key, timeout_seconds=timeout_seconds)
        emit("deepseek", "DeepSeek 正在分析供需背景、阶段、关键事件与后续场景。", 48)
        raw_payload, raw_text = client.analyze(messages)
        emit("validate", "已收到模型 JSON，正在校验日期、事件价格与结构字段。", 76)
        analysis = validate_analysis(raw_payload, recent)

        title = (
            f"{stock.get('name') or stock['code']}({stock['code']}) "
            f"{data_date} 威科夫结构: {analysis.get('mode')} / {analysis.get('current_phase')}"
        )
        emit("render", "结构校验通过，正在调用本地可信渲染器生成 PNG 图表。", 86)
        chart_path = render_chart(stock["csv_path"], analysis, paths["chart_path"], title)

        result = {
            "success": True,
            "generated_at": generated_at,
            "model": DEEPSEEK_MODEL,
            "stock": {
                "code": stock["code"],
                "name": stock.get("name") or "",
                "board": stock.get("board") or "",
            },
            "data": {
                "latest_date": data_date,
                "rows_total": int(len(df)),
                "rows_sent": int(len(recent)),
            },
            "analysis": analysis,
            "analysis_text": self._format_analysis_text(analysis),
            "paths": {
                **paths,
                "chart_path": chart_path,
            },
        }
        emit("save", "正在保存分析 JSON、PNG 图表与调试记录。", 96)
        Path(paths["analysis_path"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        debug_payload = {
            "generated_at": generated_at,
            "model": DEEPSEEK_MODEL,
            "stock": result["stock"],
            "raw_model_json": raw_payload,
            "raw_model_text": raw_text,
        }
        Path(paths["debug_path"]).write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("done", "威科夫分析完成，图表与分析文件已保存。", 100)
        return result

    @staticmethod
    def _format_analysis_text(analysis: dict[str, Any]) -> str:
        lines = [
            f"结构判断：{analysis.get('mode', 'unclear')}",
            f"当前阶段：{analysis.get('current_phase', 'unclear')}",
            "",
        ]
        background = str(analysis.get("background_text") or "").strip()
        if background:
            lines.extend(["背景判断：", background, ""])

        lines.extend(["综合分析：", str(analysis.get("summary_text") or "").strip()])

        events = analysis.get("events") or []
        if events:
            lines.append("")
            lines.append("关键事件：")
            for item in events:
                lines.append(f"- {item['date']} {item['term']} @{item['price']}: {item['reason']}")

        key_levels = analysis.get("key_levels") or []
        if key_levels:
            lines.append("")
            lines.append("关键价位：")
            for item in key_levels:
                meaning = f"：{item.get('meaning')}" if item.get("meaning") else ""
                lines.append(f"- {item.get('price')}: {item.get('label')}{meaning}")

        scenarios = analysis.get("scenarios") or []
        if scenarios:
            lines.append("")
            lines.append("后续场景：")
            for item in scenarios:
                if isinstance(item, dict):
                    label = item.get("name") or item.get("title") or "scenario"
                    desc = item.get("description") or item.get("condition") or item
                    lines.append(f"- {label}: {desc}")
                else:
                    lines.append(f"- {item}")
        conclusion = str(analysis.get("conclusion_text") or "").strip()
        if conclusion:
            lines.append("")
            lines.append(f"一句话结论：{conclusion}")
        risk_note = analysis.get("risk_note")
        if risk_note:
            lines.append("")
            lines.append(str(risk_note))
        return "\n".join(lines).strip()

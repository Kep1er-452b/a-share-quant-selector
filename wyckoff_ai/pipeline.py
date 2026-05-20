"""End-to-end Wyckoff AI analysis pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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

    def analyze_stock(self, query: str) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise WyckoffPipelineError("请输入股票代码、名称或拼音")

        stock = self._resolve_stock(query)
        df = load_stock_csv(stock["csv_path"])
        recent = model_frame(df)
        data_date = get_latest_data_date(df)
        paths = build_wyckoff_output_paths(
            symbol=stock["code"],
            stock_name=stock.get("name"),
            data_date=data_date,
            output_dir=self.output_dir,
        )
        for path in paths.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        api_key = resolve_deepseek_api_key(self.config)
        if not api_key:
            raise WyckoffPipelineError("未配置 DeepSeek API Key，请在 config/config_local.yaml 或 DEEPSEEK_API_KEY 中配置")

        messages = build_messages(stock["code"], stock.get("name") or "", recent)
        timeout_seconds = float(self.config.get("wyckoff_ai", {}).get("timeout_seconds", 90))
        client = DeepSeekWyckoffClient(api_key=api_key, timeout_seconds=timeout_seconds)
        raw_payload, raw_text = client.analyze(messages)
        analysis = validate_analysis(raw_payload, recent)

        title = (
            f"{stock.get('name') or stock['code']}({stock['code']}) "
            f"{data_date} 威科夫结构: {analysis.get('mode')} / {analysis.get('current_phase')}"
        )
        chart_path = render_chart(stock["csv_path"], analysis, paths["chart_path"], title)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        Path(paths["analysis_path"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        debug_payload = {
            "generated_at": generated_at,
            "model": DEEPSEEK_MODEL,
            "stock": result["stock"],
            "raw_model_json": raw_payload,
            "raw_model_text": raw_text,
        }
        Path(paths["debug_path"]).write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    @staticmethod
    def _format_analysis_text(analysis: dict[str, Any]) -> str:
        lines = [
            f"结构判断: {analysis.get('mode', 'unclear')}",
            f"当前阶段: {analysis.get('current_phase', 'unclear')}",
            "",
            str(analysis.get("summary_text") or "").strip(),
        ]
        events = analysis.get("events") or []
        if events:
            lines.append("")
            lines.append("关键事件:")
            for item in events:
                lines.append(f"- {item['date']} {item['term']} @{item['price']}: {item['reason']}")
        scenarios = analysis.get("scenarios") or []
        if scenarios:
            lines.append("")
            lines.append("后续场景:")
            for item in scenarios:
                if isinstance(item, dict):
                    label = item.get("name") or item.get("title") or "scenario"
                    desc = item.get("description") or item.get("condition") or item
                    lines.append(f"- {label}: {desc}")
                else:
                    lines.append(f"- {item}")
        risk_note = analysis.get("risk_note")
        if risk_note:
            lines.append("")
            lines.append(str(risk_note))
        return "\n".join(lines).strip()

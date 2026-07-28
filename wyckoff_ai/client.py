"""DeepSeek client for structured Wyckoff analysis."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from utils.local_config import load_local_config_file

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"


class WyckoffClientError(RuntimeError):
    """Raised when the model cannot be called or parsed."""


def resolve_deepseek_api_key(config: dict | None = None) -> str | None:
    env_token = os.getenv("DEEPSEEK_API_KEY")
    if env_token:
        return env_token.strip()
    local_config = load_local_config_file()
    token = local_config.get("wyckoff_ai", {}).get("deepseek_api_key")
    if token:
        return str(token).strip()
    return None


def parse_json_content(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise WyckoffClientError("模型返回为空")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        payload = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            raise WyckoffClientError("模型未返回合法 JSON") from exc
    if not isinstance(payload, dict):
        raise WyckoffClientError("模型 JSON 根节点必须是对象")
    return payload


class DeepSeekWyckoffClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        timeout_seconds: float = 180.0,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise WyckoffClientError("未配置 DeepSeek API Key")

    def _create_completion(
        self,
        client,
        messages: list[dict[str, str]],
        *,
        use_json_mode: bool,
        thinking_enabled: bool,
    ):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "extra_body": {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}},
            "max_tokens": 6144,
        }
        if thinking_enabled:
            kwargs["reasoning_effort"] = "max"
            kwargs["max_tokens"] = 8192
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    def analyze(self, messages: list[dict[str, str]]) -> tuple[dict[str, Any], str]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise WyckoffClientError("当前环境未安装 openai，请先安装 requirements.txt 中的依赖") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        attempts = [
            ("json_mode", True, False, messages),
            (
                "json_mode_retry",
                True,
                False,
                messages + [{"role": "user", "content": "上一次可能返回为空。请只返回一个非空 JSON 对象，不要输出 Markdown。"}],
            ),
            (
                "plain_json_retry",
                False,
                False,
                messages + [{"role": "user", "content": "请只输出 JSON 对象本身。不要解释，不要 Markdown，不要代码块。"}],
            ),
        ]
        errors: list[str] = []
        for name, use_json_mode, thinking_enabled, attempt_messages in attempts:
            try:
                response = self._create_completion(
                    client,
                    attempt_messages,
                    use_json_mode=use_json_mode,
                    thinking_enabled=thinking_enabled,
                )
            except Exception as exc:
                errors.append(f"{name}: API 调用失败: {exc}")
                continue

            choices = getattr(response, "choices", None) or []
            if not choices:
                errors.append(f"{name}: API 返回 choices 为空")
                continue
            message = getattr(choices[0], "message", None)
            if message is None:
                errors.append(f"{name}: API 返回 message 为空")
                continue
            content = message.content or ""
            if not content.strip():
                reasoning = getattr(message, "reasoning_content", None)
                reason_hint = f"，reasoning_content 长度 {len(reasoning or '')}" if reasoning else ""
                errors.append(f"{name}: 模型 content 为空{reason_hint}")
                continue
            try:
                payload = parse_json_content(content)
            except Exception as exc:
                errors.append(f"{name}: JSON 解析失败: {exc}")
                continue
            return payload, content

        raise WyckoffClientError("DeepSeek 未返回可用 JSON；" + "；".join(errors[-3:]))

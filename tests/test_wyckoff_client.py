from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from wyckoff_ai.client import DeepSeekWyckoffClient, WyckoffClientError, parse_json_content


def test_parse_json_content_uses_first_valid_object_when_text_contains_multiple_blocks():
    payload = parse_json_content('说明文字 {"mode":"unclear"} 其他片段 {"ignored": true}')

    assert payload == {"mode": "unclear"}


def test_deepseek_client_empty_choices_becomes_client_error(monkeypatch):
    client = DeepSeekWyckoffClient(api_key="test")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda **kwargs: object()))

    class EmptyResponse:
        choices = []

    monkeypatch.setattr(client, "_create_completion", lambda *args, **kwargs: EmptyResponse())

    with pytest.raises(WyckoffClientError, match="choices"):
        client.analyze([{"role": "user", "content": "只返回 JSON"}])

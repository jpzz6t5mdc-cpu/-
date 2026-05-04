"""Anthropic SDKの薄いラッパー。Prompt Cachingを各役割のsystem promptに適用する。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 8000


@dataclass
class CallResult:
    text: str
    raw: anthropic.types.Message


class Client:
    """Anthropicクライアントのラッパー。同じ役割のsystem promptをcache hitさせる。"""

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS):
        load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEYが設定されていません。.envファイルか環境変数で指定してください。"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def call(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> CallResult:
        """1回のmessages.create呼び出し。systemにcache_controlを付ける。"""
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return CallResult(text=text, raw=response)

    def call_with_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> anthropic.types.Message:
        """tool use対応の呼び出し。Implementerフェーズで使う。"""
        return self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            tools=tools,
        )

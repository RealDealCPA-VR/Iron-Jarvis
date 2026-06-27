"""Provider-agnostic LLM interface (§6).

The Model Router and Agent Runtime speak only this vocabulary; concrete vendors
(Anthropic, browser-session providers, the offline mock) implement ``LLMAdapter``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    role: str  # "user" | "assistant" | "tool"
    content: str = ""
    tool_call_id: str | None = None  # for role == "tool"
    name: str | None = None  # tool name, for role == "tool"
    #: present on assistant turns that requested tool use (so multi-step tool
    #: loops can be replayed faithfully to vendors that require it).
    tool_calls: list["ToolCall"] = field(default_factory=list)
    #: optional image parts on a user turn (multimodal). Each is
    #: ``{"data_b64": <base64>, "media_type": "image/png"|"image/jpeg"|...}``.
    images: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_use" | "max_tokens"
    #: token accounting for this completion (0/0 for the offline mock).
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMAdapter(ABC):
    provider: str = ""
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        ...

    def capabilities(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "tool_use": True}

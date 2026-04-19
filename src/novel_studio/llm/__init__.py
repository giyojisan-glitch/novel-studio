"""LLM Provider 抽象层。

Provider 回答一个问题："给定一个 step（prompt + schema），获取 LLM 响应的 JSON dict。"

当前实现：
- HumanQueueProvider: MVP 模式——写 prompt 到 queue 文件，等 Claude Code 会话里的人响应
- StubProvider: 测试用——按 step_id 返回预设 JSON
- AnthropicProvider: V2 目标——真调 Claude API（骨架已备，实现待填）
"""
from __future__ import annotations

from .base import BaseProvider, ProviderResult
from .human_queue import HumanQueueProvider
from .stub import StubProvider
from .anthropic import AnthropicProvider
from .factory import get_provider

__all__ = [
    "BaseProvider",
    "ProviderResult",
    "HumanQueueProvider",
    "StubProvider",
    "AnthropicProvider",
    "get_provider",
]

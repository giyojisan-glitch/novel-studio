"""Provider 工厂：按环境/参数返回合适的 provider 实例。"""
from __future__ import annotations

import os

from .base import BaseProvider
from .human_queue import HumanQueueProvider
from .stub import StubProvider
from .anthropic import AnthropicProvider


def get_provider(name: str | None = None) -> BaseProvider:
    """按名字返回 provider。

    - 优先级：函数参数 > 环境变量 NOVEL_STUDIO_PROVIDER > 默认 human_queue
    - 识别：'human_queue' / 'stub' / 'anthropic'
    """
    name = name or os.getenv("NOVEL_STUDIO_PROVIDER", "human_queue")
    name = name.lower()

    if name == "human_queue":
        return HumanQueueProvider()
    if name == "stub":
        return StubProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"未知 provider: {name}（可选：human_queue / stub / anthropic）")

"""AnthropicProvider — V2 目标：真调 Claude API。

骨架已备，实现待填。需要：
- 依赖：`anthropic` Python SDK
- 环境变量：`ANTHROPIC_API_KEY`
- JSON 解析容错：LLM 可能返回 markdown 代码块包裹的 JSON / 被截断的 JSON / 字段缺失

当前状态：**占位实现**。request 时抛 NotImplementedError，让用户明确知道这里还没做。
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import BaseProvider, ProviderResult


class AnthropicProvider(BaseProvider):
    """真调 Claude API 的 provider。当前为占位。"""

    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._cache: dict[str, dict] = {}

    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        """TODO(V2.1): 真调 Anthropic API，解析 JSON 响应，存到 self._cache[step_id]。

        实现提示：
        1. 用 `anthropic.Anthropic(api_key=...).messages.create(...)` 调模型
        2. 模型响应可能带 markdown 代码块，需要用 read_response 的 _strip_code_fence 清理
        3. JSON 解析失败时，自动重试（最多 3 次）给 LLM "你上次返回的不是合法 JSON" 的 hint
        4. 持久化缓存到 pdir/llm_cache/{step_id}.json 便于断点续跑
        5. 支持 schema 验证——prompt 里已经给出 schema，这里要求输出严格符合
        """
        if not self.api_key:
            raise NotImplementedError(
                "AnthropicProvider 还没实现。设置 ANTHROPIC_API_KEY 并在这里加 anthropic SDK 调用。"
            )
        raise NotImplementedError("AnthropicProvider.request() 骨架——V2.1 填充。")

    def query(self, step_id: str, pdir: Path) -> ProviderResult:
        if step_id in self._cache:
            return ProviderResult(ready=True, data=self._cache[step_id])
        return ProviderResult(ready=False)

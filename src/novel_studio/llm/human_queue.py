"""HumanQueueProvider — MVP 默认模式。

把 prompt dump 到 projects/{ts}/queue/，等人（或 Claude Code 会话）在 responses/ 写 JSON。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import BaseProvider, ProviderResult
from ..utils import write_prompt, read_response


class HumanQueueProvider(BaseProvider):
    """当前默认 provider。要求 pdir/queue 和 pdir/responses 目录存在。"""

    name = "human_queue"

    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        """把 prompt 写到 queue 文件。不等待，立即返回。"""
        write_prompt(pdir, step_id, prompt)

    def query(self, step_id: str, pdir: Path) -> ProviderResult:
        """检查 responses/{step_id}.response.json 是否存在。"""
        try:
            data = read_response(pdir, step_id)
        except json.JSONDecodeError as e:
            return ProviderResult(ready=True, data=None, error=f"JSON 解析失败: {e}")
        except Exception as e:
            return ProviderResult(ready=True, data=None, error=f"读取失败: {e}")

        if data is None:
            return ProviderResult(ready=False)
        return ProviderResult(ready=True, data=data)

    def reset(self, step_id: str, pdir: Path) -> None:
        """清理指定 step 的 queue + response 文件（重试用）。"""
        for sub, suffix in (("queue", ".prompt.md"), ("responses", ".response.json")):
            p = pdir / sub / f"{step_id}{suffix}"
            if p.exists():
                p.unlink()

    def has_pending_request(self, step_id: str, pdir: Path) -> bool:
        """HumanQueue: prompt 已 dump 但 response 还没写 = 等人响应。"""
        prompt_exists = (pdir / "queue" / f"{step_id}.prompt.md").exists()
        response_exists = (pdir / "responses" / f"{step_id}.response.json").exists()
        return prompt_exists and not response_exists

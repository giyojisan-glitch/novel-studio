"""AnthropicProvider — V2.1 实现：真调 Claude API。

设计：
- **Output-compatible with HumanQueue**: 响应写到 `pdir/responses/{step_id}.response.json`
  这样 engine 层不用区分两种 provider（都是 file-based），并且支持**断点续跑**（重跑 init 时
  已有响应不会重调 API）
- **2 层重试**：
  1. API 错误（rate limit / 5xx / timeout）→ exponential backoff，3 次
  2. JSON 解析失败（LLM 包 markdown、截断、乱加解释）→ 追加提示重试，2 次
- **环境变量**：
  - `ANTHROPIC_API_KEY`（必需）
  - `NOVEL_STUDIO_MODEL`（可选，默认 `claude-sonnet-4-6`）
  - `NOVEL_STUDIO_MAX_TOKENS`（可选，默认 8192）

Schema 验证仍在 engine 层做——Provider 只保证"给你一份合法的 JSON"。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .base import BaseProvider, ProviderResult
from ..utils import write_prompt, read_response


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_API_RETRIES = 3
DEFAULT_MAX_JSON_RETRIES = 2


class AnthropicProvider(BaseProvider):
    """调用 Anthropic Claude API 的自动 provider。响应写到 file system，兼容 HumanQueue。"""

    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int | None = None,
        max_api_retries: int = DEFAULT_MAX_API_RETRIES,
        max_json_retries: int = DEFAULT_MAX_JSON_RETRIES,
    ):
        self.model = model or os.getenv("NOVEL_STUDIO_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.max_tokens = int(max_tokens or os.getenv("NOVEL_STUDIO_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self.max_api_retries = max_api_retries
        self.max_json_retries = max_json_retries
        self._client = None  # lazy init

    @property
    def client(self):
        """懒加载：第一次真用到时才创建 Anthropic 客户端，否则 import 也 OK。"""
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY 未设置。要用 AnthropicProvider 必须设置此环境变量。"
                )
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        """调 API 拿响应、写到 responses/{step_id}.response.json。

        如果响应文件已存在（断点续跑场景），直接跳过，不重复调用。
        """
        resp_path = pdir / "responses" / f"{step_id}.response.json"
        if resp_path.exists():
            return  # 断点续跑：已有响应不重调

        # 同时把 prompt 写到 queue/ 便于调试与观察（和 HumanQueue 一致）
        write_prompt(pdir, step_id, prompt)

        # 真调 API + 2 层重试
        data = self._call_with_retries(prompt)

        # 写到 response 文件
        resp_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def query(self, step_id: str, pdir: Path) -> ProviderResult:
        """读响应文件——和 HumanQueue 一样。"""
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
        """清理指定 step 的文件，让下次 request 重新调 API。"""
        for sub, suffix in (("queue", ".prompt.md"), ("responses", ".response.json")):
            p = pdir / sub / f"{step_id}{suffix}"
            if p.exists():
                p.unlink()

    # -------- 内部：真正的 API 调用 + 重试 --------

    def _call_with_retries(self, prompt: str) -> Any:
        """两层重试：JSON 解析失败 → 带 hint 重调；API 错误 → 指数退避。

        返回值：解析好的 dict 或 list（L4_adversarial 是 list）。
        """
        current_prompt = prompt
        last_error: Exception | None = None

        for json_attempt in range(self.max_json_retries + 1):
            raw_text = self._call_api_with_backoff(current_prompt)
            try:
                return self._parse_json(raw_text)
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                # 追加错误提示，让 LLM 下次规范输出
                current_prompt = (
                    prompt
                    + "\n\n【系统反馈】上一次响应不是合法 JSON："
                    + f"{str(e)[:150]}\n"
                    + "请只输出严格的 JSON，不要 markdown 代码块包裹，不要解释文字。"
                )

        raise RuntimeError(
            f"AnthropicProvider JSON 解析重试 {self.max_json_retries} 次仍失败：{last_error}"
        )

    def _call_api_with_backoff(self, prompt: str) -> str:
        """调 API；遇到 rate limit / 5xx / timeout 指数退避重试。"""
        import anthropic

        for api_attempt in range(self.max_api_retries):
            try:
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                # content 是 list，通常只有一个 TextBlock
                return "".join(
                    getattr(block, "text", "")
                    for block in msg.content
                    if getattr(block, "type", "") == "text"
                )
            except (
                anthropic.APIStatusError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            ) as e:
                if api_attempt == self.max_api_retries - 1:
                    raise RuntimeError(
                        f"Anthropic API 调用重试 {self.max_api_retries} 次仍失败：{e}"
                    ) from e
                # 指数退避：1s, 2s, 4s
                time.sleep(2 ** api_attempt)

        raise RuntimeError("unreachable")  # for type checker

    @staticmethod
    def _parse_json(text: str) -> Any:
        """剥离 markdown 包裹，然后 json.loads。失败时 raise 让上层处理。"""
        text = text.strip()
        # 剥离开头 ```json / ``` 和结尾 ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        return json.loads(text)

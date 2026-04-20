"""AnthropicProvider — V2.1 实现：真调 Claude API。

设计：
- **Output-compatible with HumanQueue**: 响应写到 `pdir/responses/{step_id}.response.json`
  这样 engine 层不用区分两种 provider（都是 file-based），并且支持**断点续跑**（重跑 init 时
  已有响应不会重调 API）
- **3 层重试**：
  1. API 错误（rate limit / 5xx / timeout）→ exponential backoff，3 次
  2. JSON 解析失败 → json5 lenient fallback
  3. 仍解析失败 + content-heavy step（L3/L4_scrubber）→ raw text 兜底包装
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
        self._last_raw_text: str = ""  # 最后一次 API 返回的 raw text（兜底用）

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

        断点续跑：response 已存在直接 skip。
        Raw-text 兜底：JSON 全失败时，content-heavy step（L3/L4_scrubber）把 raw text
        包装成最小合法 JSON，避免一次 LLM 偷懒废掉整本书。
        """
        resp_path = pdir / "responses" / f"{step_id}.response.json"
        if resp_path.exists():
            return  # 断点续跑

        write_prompt(pdir, step_id, prompt)

        try:
            data = self._call_with_retries(prompt)
        except RuntimeError:
            # 兜底：content-heavy step 接受 raw text 包装
            if _is_content_heavy_step(step_id) and self._last_raw_text:
                data = _wrap_raw_text_as_json(step_id, self._last_raw_text)
            else:
                raise

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
        for sub, suffix in (("queue", ".prompt.md"), ("responses", ".response.json")):
            p = pdir / sub / f"{step_id}{suffix}"
            if p.exists():
                p.unlink()

    # -------- 内部：真正的 API 调用 + 重试 --------

    def _call_with_retries(self, prompt: str) -> Any:
        """JSON 重试：先尝试 strict，失败用 json5 lenient，再失败带 hint 重发。"""
        current_prompt = prompt
        last_error: Exception | None = None

        for json_attempt in range(self.max_json_retries + 1):
            raw_text = self._call_api_with_backoff(current_prompt)
            try:
                return self._parse_json(raw_text)
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                current_prompt = (
                    prompt
                    + "\n\n【系统反馈】上一次响应不是合法 JSON："
                    + f"{str(e)[:150]}\n"
                    + "请只输出严格的 JSON。第一个字符必须是 `{` 或 `[`。"
                    + "不要 markdown 包裹，不要解释文字，不要直接写正文——必须是 JSON 对象。"
                )

        raise RuntimeError(
            f"AnthropicProvider JSON 解析重试 {self.max_json_retries} 次仍失败：{last_error}"
        )

    def _call_api_with_backoff(self, prompt: str) -> str:
        import anthropic

        for api_attempt in range(self.max_api_retries):
            try:
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(
                    getattr(block, "text", "")
                    for block in msg.content
                    if getattr(block, "type", "") == "text"
                )
                self._last_raw_text = text  # 兜底用
                return text
            except (
                anthropic.APIStatusError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            ) as e:
                if api_attempt == self.max_api_retries - 1:
                    raise RuntimeError(
                        f"Anthropic API 调用重试 {self.max_api_retries} 次仍失败：{e}"
                    ) from e
                time.sleep(2 ** api_attempt)

        raise RuntimeError("unreachable")

    @staticmethod
    def _parse_json(text: str) -> Any:
        """剥离 markdown 包裹，解析 JSON（用 json5 兜 lenient）。"""
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import json5
            return json5.loads(text)


# ============================================================
# Raw-text 兜底辅助：避免 LLM 偷懒返纯小说时整本书废掉
# ============================================================


_L3_CHAPTER_EXACT = re.compile(r"^L3_\d+$")
_L4_SCRUBBER_EXACT = re.compile(r"^L4_scrubber_\d+$")


def _is_content_heavy_step(step_id: str) -> bool:
    """content-heavy = LLM 极易返纯文本不裹 JSON 的 step（章节正文/润色）。

    严格匹配：排除 L3_N_audit_logic 这类后缀 step。
    """
    return bool(_L3_CHAPTER_EXACT.match(step_id) or _L4_SCRUBBER_EXACT.match(step_id))


def _wrap_raw_text_as_json(step_id: str, raw_text: str) -> dict:
    """把 LLM 偷懒返回的纯文本包装成对应 step 的最小合法 JSON。"""
    cn_count = len(re.findall(r"[\u4e00-\u9fff]", raw_text))

    if step_id.startswith("L3_"):
        idx = int(step_id.split("_")[1])
        return {
            "index": idx,
            "content": raw_text,
            "word_count": cn_count,
            "revision": 0,
        }
    if step_id.startswith("L4_scrubber_"):
        idx = int(step_id.split("_")[2])
        return {
            "index": idx,
            "content": raw_text,
            "polish_notes": ["raw text fallback (LLM 未返回结构化响应)"],
            "adversarial_cuts": [],
            "revision": 0,
        }
    return {}

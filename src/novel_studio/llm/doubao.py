"""DoubaoProvider — 火山引擎豆包（OpenAI 兼容接口）。

架构镜像 AnthropicProvider：
- **Output-compatible with HumanQueue**: 响应写到 `pdir/responses/{step_id}.response.json`
- **3 层重试**：API 错误 → exp backoff → JSON lenient parse → raw-text 兜底（content-heavy step）
- **环境变量**：
  - `DOUBAO_API_KEY`（必需）
  - `DOUBAO_MODEL`（可选，默认 `doubao-seed-2.0-pro`；也可填 endpoint ID 如 `ep-20240601-xxx`）
  - `DOUBAO_BASE_URL`（可选，默认火山 endpoint）
  - `NOVEL_STUDIO_MAX_TOKENS`（可选，默认 8192）

Creativity 档位 → temperature（0.3 / 0.7 / 1.0），真温度真 sampling。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .base import BaseProvider, ProviderResult
from .anthropic import _is_content_heavy_step, _wrap_raw_text_as_json
from ..utils import write_prompt, read_response


DEFAULT_MODEL = "doubao-seed-2.0-pro"
# 火山 Coding Plan 订阅专用 OpenAI 兼容 endpoint（非 /api/v3，后者不走订阅会扣额外费用）
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_API_RETRIES = 3
DEFAULT_MAX_JSON_RETRIES = 2


class DoubaoProvider(BaseProvider):
    """调用豆包（火山引擎）OpenAI 兼容接口。响应写到 file system，兼容 HumanQueue。"""

    name = "doubao"

    # 创意档位 → temperature 映射（和 AnthropicProvider 一致）
    _CREATIVITY_TEMPERATURE = {
        "strict": 0.3,
        "balanced": 0.7,
        "creative": 1.0,
    }

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        max_api_retries: int = DEFAULT_MAX_API_RETRIES,
        max_json_retries: int = DEFAULT_MAX_JSON_RETRIES,
    ):
        self.model = model or os.getenv("DOUBAO_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("DOUBAO_API_KEY")
        self.base_url = base_url or os.getenv("DOUBAO_BASE_URL", DEFAULT_BASE_URL)
        self.max_tokens = int(max_tokens or os.getenv("NOVEL_STUDIO_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self.max_api_retries = max_api_retries
        self.max_json_retries = max_json_retries
        self._client = None
        self._last_raw_text: str = ""

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "DOUBAO_API_KEY 未设置。要用 DoubaoProvider 必须设置此环境变量（或写到 .env）。"
                )
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _temperature_for_creativity(self) -> float:
        return self._CREATIVITY_TEMPERATURE.get(self.creativity, 0.7)

    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        resp_path = pdir / "responses" / f"{step_id}.response.json"
        if resp_path.exists():
            return

        write_prompt(pdir, step_id, prompt)

        try:
            data = self._call_with_retries(prompt)
        except RuntimeError:
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
            f"DoubaoProvider JSON 解析重试 {self.max_json_retries} 次仍失败：{last_error}"
        )

    def _call_api_with_backoff(self, prompt: str) -> str:
        import openai

        temperature = self._temperature_for_creativity()

        for api_attempt in range(self.max_api_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.choices[0].message.content or ""
                self._last_raw_text = text
                return text
            except (
                openai.APIStatusError,
                openai.APIConnectionError,
                openai.APITimeoutError,
            ) as e:
                if api_attempt == self.max_api_retries - 1:
                    raise RuntimeError(
                        f"Doubao API 调用重试 {self.max_api_retries} 次仍失败：{e}"
                    ) from e
                time.sleep(2 ** api_attempt)

        raise RuntimeError("unreachable")

    @staticmethod
    def _parse_json(text: str) -> Any:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import json5
            return json5.loads(text)

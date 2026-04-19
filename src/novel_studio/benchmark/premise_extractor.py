"""Premise extractor: read a full short story, produce a NOVEL-Studio-compatible premise."""
from __future__ import annotations

from pathlib import Path

from ..llm import AnthropicProvider
from .prompts import premise_extractor_prompt


def extract_premise(
    original_text: str,
    provider: AnthropicProvider | None = None,
) -> str:
    """调 LLM 把一篇原创短篇逆向提取成 150 字 premise。

    Returns: premise markdown text.
    """
    provider = provider or AnthropicProvider()
    prompt = premise_extractor_prompt(original_text)

    # Reuse the retry/parse loop but expect PLAIN TEXT (markdown), not JSON.
    # We do a minimal direct call to bypass the JSON parser.
    import anthropic

    for api_attempt in range(provider.max_api_retries):
        try:
            msg = provider.client.messages.create(
                model=provider.model,
                max_tokens=provider.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                getattr(block, "text", "")
                for block in msg.content
                if getattr(block, "type", "") == "text"
            )
            return text.strip()
        except (
            anthropic.APIStatusError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ) as e:
            if api_attempt == provider.max_api_retries - 1:
                raise RuntimeError(f"Premise extraction failed: {e}") from e
            import time
            time.sleep(2 ** api_attempt)

    raise RuntimeError("unreachable")


def extract_premise_from_file(path: Path, provider: AnthropicProvider | None = None) -> str:
    """从文件读原文 → 提取 premise。"""
    text = Path(path).read_text(encoding="utf-8")
    return extract_premise(text, provider=provider)

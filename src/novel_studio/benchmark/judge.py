"""LLM-as-judge: compare original vs generated, return 6-dim scores + overall verdict."""
from __future__ import annotations

import json

from ..llm import AnthropicProvider
from .prompts import judge_prompt
from .schemas import (
    BenchmarkVerdict,
    DimensionScore,
    DIMENSION_WEIGHTS,
    PASS_THRESHOLD,
    compute_overall,
)


def judge_similarity(
    case_name: str,
    original_text: str,
    generated_text: str,
    provider: AnthropicProvider | None = None,
) -> BenchmarkVerdict:
    """调 Sonnet 做 6 维度相似度评分，加权得出总分与 pass/fail。"""
    provider = provider or AnthropicProvider()
    prompt = judge_prompt(original_text, generated_text)

    # 走 provider 的 2-tier retry（API error + bad JSON）
    raw_data = provider._call_with_retries(prompt)

    # 容错：如果 LLM 返回的顶层是 dict 就取它，是 list 就把它当成 dimension_scores
    if isinstance(raw_data, list):
        payload = {"dimension_scores": raw_data, "notes": ""}
    elif isinstance(raw_data, dict):
        payload = raw_data
    else:
        raise ValueError(f"Unexpected judge payload type: {type(raw_data)}")

    # 解析每个维度
    dim_scores = []
    for d in payload.get("dimension_scores", []):
        if d.get("dimension") in DIMENSION_WEIGHTS:
            dim_scores.append(DimensionScore(**d))

    # 如果法官漏掉了某个维度，补一个 0.5 placeholder（避免崩）
    seen = {d.dimension for d in dim_scores}
    for dim in DIMENSION_WEIGHTS:
        if dim not in seen:
            dim_scores.append(DimensionScore(
                dimension=dim,  # type: ignore[arg-type]
                score=0.5,
                rationale="(judge omitted this dimension; default 0.5)",
            ))

    overall = compute_overall(dim_scores)
    return BenchmarkVerdict(
        case_name=case_name,
        dimension_scores=dim_scores,
        overall_score=overall,
        passed=(overall >= PASS_THRESHOLD),
        judge_model=provider.model,
        notes=payload.get("notes", ""),
    )

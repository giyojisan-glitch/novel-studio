"""Schemas for benchmark framework."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


# Dimension keys and weights. Sum = 1.0.
# Rationale in docs/INSPIRATION_MAP.md (benchmark section).
DIMENSION_WEIGHTS: dict[str, float] = {
    "plot_structure":  0.25,  # 三幕/转折/关键事件
    "character_core":  0.20,  # 主角 want / need / wound
    "world_anchors":   0.15,  # 硬规则 / 设定保留
    "tone":            0.15,  # 冷峻 / 爽文 / 悲情
    "ending_vector":   0.15,  # 结局方向性
    "key_scenes":      0.10,  # 关键画面 / 物件
}

PASS_THRESHOLD = 0.70


Dimension = Literal[
    "plot_structure", "character_core", "world_anchors",
    "tone", "ending_vector", "key_scenes",
]


class DimensionScore(BaseModel):
    """单个维度的评分 + 解释。"""
    dimension: Dimension
    score: float = Field(ge=0.0, le=1.0)
    rationale: str                        # 法官解释为什么打这个分
    alignments: list[str] = Field(default_factory=list)   # 原文与生成对上的点
    divergences: list[str] = Field(default_factory=list)  # 原文与生成差异


class BenchmarkCase(BaseModel):
    """一个测试案例：原文 + 提取的 premise + 生成的小说。"""
    name: str                              # 文件名 stem，如 "zhao_xue"
    original_path: Path
    premise_path: Optional[Path] = None   # 提取出的 premise 文件路径
    premise_text: str = ""                 # premise 内容
    generated_path: Optional[Path] = None # NOVEL-Studio 生成稿路径
    generated_text: str = ""               # 生成稿内容
    original_word_count: int = 0
    generated_word_count: int = 0


class BenchmarkVerdict(BaseModel):
    """单个案例的最终裁定。"""
    case_name: str
    dimension_scores: list[DimensionScore]
    overall_score: float = Field(ge=0.0, le=1.0)
    passed: bool
    judge_model: str                       # 记录用哪个模型判的（可复核）
    notes: str = ""                        # 法官综合点评

    def scores_by_dim(self) -> dict[str, float]:
        return {d.dimension: d.score for d in self.dimension_scores}


def compute_overall(dimension_scores: list[DimensionScore]) -> float:
    """按 DIMENSION_WEIGHTS 加权平均。"""
    total = 0.0
    for ds in dimension_scores:
        weight = DIMENSION_WEIGHTS.get(ds.dimension, 0.0)
        total += ds.score * weight
    return round(total, 4)

"""Benchmark framework — TDD for novel generation.

Use real human-written short stories as "expected output". Reverse-extract
a ~150-word premise from each story, run it through NOVEL-Studio, and
LLM-judge the generated novel against the original on 6 weighted dimensions.

Pass threshold: overall score >= 0.70 (70% fidelity to the original).
"""
from .schemas import (
    BenchmarkCase,
    DimensionScore,
    BenchmarkVerdict,
    DIMENSION_WEIGHTS,
    PASS_THRESHOLD,
)
from .premise_extractor import extract_premise
from .judge import judge_similarity
from .runner import run_single, run_batch

__all__ = [
    "BenchmarkCase",
    "DimensionScore",
    "BenchmarkVerdict",
    "DIMENSION_WEIGHTS",
    "PASS_THRESHOLD",
    "extract_premise",
    "judge_similarity",
    "run_single",
    "run_batch",
]

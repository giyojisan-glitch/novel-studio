"""Tests for benchmark framework (schemas, prompts, judge parsing, runner wiring)."""
from __future__ import annotations

import pytest
from pathlib import Path

from novel_studio.benchmark.schemas import (
    BenchmarkCase,
    DimensionScore,
    BenchmarkVerdict,
    DIMENSION_WEIGHTS,
    PASS_THRESHOLD,
    compute_overall,
)
from novel_studio.benchmark.prompts import (
    premise_extractor_prompt,
    judge_prompt,
)


# ---------- Dimension weights invariants ----------


class TestDimensionWeights:
    def test_weights_sum_to_one(self):
        total = sum(DIMENSION_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6, f"weights must sum to 1.0, got {total}"

    def test_all_dimensions_have_positive_weight(self):
        for d, w in DIMENSION_WEIGHTS.items():
            assert w > 0, f"{d} has non-positive weight"

    def test_pass_threshold_sensible(self):
        assert 0.5 <= PASS_THRESHOLD <= 0.9


# ---------- compute_overall ----------


class TestComputeOverall:
    def test_perfect_score(self):
        ds = [DimensionScore(dimension=d, score=1.0, rationale="p")
              for d in DIMENSION_WEIGHTS]
        assert compute_overall(ds) == 1.0

    def test_zero_score(self):
        ds = [DimensionScore(dimension=d, score=0.0, rationale="p")
              for d in DIMENSION_WEIGHTS]
        assert compute_overall(ds) == 0.0

    def test_weighted_average(self):
        # plot_structure 权重 25%，score=1.0，其他都 0 → 总分应为 0.25
        ds = [DimensionScore(
            dimension="plot_structure", score=1.0, rationale="p")]
        ds += [DimensionScore(dimension=d, score=0.0, rationale="p")
               for d in DIMENSION_WEIGHTS if d != "plot_structure"]
        assert compute_overall(ds) == 0.25

    def test_partial_missing_dimension(self):
        """只给部分维度：未提供的维度默认不计分。"""
        ds = [DimensionScore(
            dimension="plot_structure", score=0.8, rationale="p")]
        # 只有 25% 权重，score 0.8 → 0.2
        assert compute_overall(ds) == 0.2


# ---------- Schema roundtrip ----------


class TestBenchmarkSchemas:
    def test_dimension_score_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            DimensionScore(dimension="plot_structure", score=1.5, rationale="p")
        with pytest.raises(ValueError):
            DimensionScore(dimension="plot_structure", score=-0.1, rationale="p")

    def test_verdict_roundtrip(self):
        v = BenchmarkVerdict(
            case_name="test",
            dimension_scores=[
                DimensionScore(dimension=d, score=0.7, rationale="ok")
                for d in DIMENSION_WEIGHTS
            ],
            overall_score=0.7,
            passed=True,
            judge_model="claude-sonnet-4-6",
            notes="ok",
        )
        blob = v.model_dump_json()
        v2 = BenchmarkVerdict.model_validate_json(blob)
        assert v2.passed is True
        assert len(v2.dimension_scores) == len(DIMENSION_WEIGHTS)

    def test_scores_by_dim_helper(self):
        v = BenchmarkVerdict(
            case_name="t", overall_score=0.7, passed=True,
            judge_model="m",
            dimension_scores=[
                DimensionScore(dimension="plot_structure", score=0.9, rationale="p"),
                DimensionScore(dimension="tone", score=0.5, rationale="p"),
            ],
        )
        d = v.scores_by_dim()
        assert d["plot_structure"] == 0.9
        assert d["tone"] == 0.5


# ---------- Prompts: sanity checks ----------


class TestPrompts:
    def test_extractor_prompt_contains_original(self):
        p = premise_extractor_prompt("一个林晚的故事")
        assert "林晚" in p
        assert "120-200 字" in p or "120" in p

    def test_extractor_prompt_forbids_leaking_ending(self):
        p = premise_extractor_prompt("abc")
        assert "不要泄露" in p or "剧情" in p

    def test_judge_prompt_has_schema_and_both_texts(self):
        p = judge_prompt("original text", "generated text")
        assert "original text" in p
        assert "generated text" in p
        assert "plot_structure" in p
        assert "character_core" in p
        assert "0.70" in p  # pass threshold mention

    def test_judge_prompt_long_text_truncated(self):
        long_text = "x" * 20000
        p = judge_prompt(long_text, long_text)
        # prompt 总长应远小于原文两倍 (都被截断到 8000)
        assert len(p) < 20000


# ---------- Judge response parsing (isolated from API) ----------


class TestJudgeResponseParsing:
    """Test judge_similarity behavior with mocked provider responses."""

    def test_valid_response_produces_verdict(self, monkeypatch):
        from novel_studio.benchmark import judge as judge_mod
        from novel_studio.llm import AnthropicProvider

        provider = AnthropicProvider(api_key="fake")

        # Mock the provider's internal _call_with_retries to return canned JSON
        def mock_call(prompt):
            return {
                "dimension_scores": [
                    {"dimension": d, "score": 0.8, "rationale": "ok",
                     "alignments": ["a"], "divergences": ["d"]}
                    for d in DIMENSION_WEIGHTS
                ],
                "notes": "overall good",
            }
        monkeypatch.setattr(provider, "_call_with_retries", mock_call)

        v = judge_mod.judge_similarity(
            case_name="t", original_text="orig", generated_text="gen",
            provider=provider,
        )
        assert v.case_name == "t"
        assert v.overall_score == 0.8
        assert v.passed is True
        assert v.notes == "overall good"
        assert len(v.dimension_scores) == 6

    def test_missing_dimensions_filled_with_default(self, monkeypatch):
        from novel_studio.benchmark import judge as judge_mod
        from novel_studio.llm import AnthropicProvider

        provider = AnthropicProvider(api_key="fake")

        def mock_call(prompt):
            # Only 2 dimensions returned
            return {
                "dimension_scores": [
                    {"dimension": "plot_structure", "score": 0.9, "rationale": "good"},
                    {"dimension": "tone", "score": 0.6, "rationale": "ok"},
                ],
                "notes": "partial",
            }
        monkeypatch.setattr(provider, "_call_with_retries", mock_call)

        v = judge_mod.judge_similarity(
            case_name="t", original_text="o", generated_text="g", provider=provider,
        )
        # 缺失的 4 维度应该被补成 0.5
        assert len(v.dimension_scores) == 6
        scores = v.scores_by_dim()
        assert scores["plot_structure"] == 0.9
        assert scores["tone"] == 0.6
        # 缺失的补 0.5
        for d in DIMENSION_WEIGHTS:
            if d not in ("plot_structure", "tone"):
                assert scores[d] == 0.5

    def test_list_response_accepted(self, monkeypatch):
        """法官有时直接返回 list 而不是 dict，判官要兼容。"""
        from novel_studio.benchmark import judge as judge_mod
        from novel_studio.llm import AnthropicProvider

        provider = AnthropicProvider(api_key="fake")

        def mock_call(prompt):
            # 顶层返回 list（而不是 {"dimension_scores": [...]}）
            return [
                {"dimension": d, "score": 0.75, "rationale": "p"}
                for d in DIMENSION_WEIGHTS
            ]
        monkeypatch.setattr(provider, "_call_with_retries", mock_call)

        v = judge_mod.judge_similarity(
            case_name="t", original_text="o", generated_text="g", provider=provider,
        )
        assert v.overall_score == 0.75
        assert v.passed is True


# ---------- Premise extractor (isolated) ----------


class TestPremiseExtractor:
    def test_extract_premise_calls_client(self, monkeypatch):
        from novel_studio.benchmark import premise_extractor as pe
        from novel_studio.llm import AnthropicProvider

        provider = AnthropicProvider(api_key="fake")

        # Mock the full Anthropic client to return a canned message
        class _Block:
            def __init__(self, t): self.text = t; self.type = "text"
        class _Msg:
            def __init__(self, t): self.content = [_Block(t)]
        class _FakeClient:
            def __init__(self): self.messages = self
            def create(self, **kwargs):
                return _Msg("1. **主角**：林晚\n2. **核心冲突**：...")
        provider._client = _FakeClient()

        result = pe.extract_premise("某短篇原文", provider=provider)
        assert "林晚" in result
        assert "主角" in result

    def test_extract_premise_from_file(self, tmp_path, monkeypatch):
        from novel_studio.benchmark import premise_extractor as pe
        from novel_studio.llm import AnthropicProvider

        provider = AnthropicProvider(api_key="fake")
        monkeypatch.setattr(pe, "extract_premise",
                            lambda text, provider=None: f"premise of: {text[:20]}")

        f = tmp_path / "story.md"
        f.write_text("这是一个故事的内容。", encoding="utf-8")
        result = pe.extract_premise_from_file(f, provider=provider)
        assert "premise of:" in result


# ---------- Runner: chapter-count heuristic ----------


class TestRunnerHeuristics:
    def test_chapter_count_short_story(self):
        from novel_studio.benchmark.runner import _guess_chapter_count
        assert _guess_chapter_count(500) == 1     # small → 1 chapter
        assert _guess_chapter_count(3000) == 3    # typical 3-chapter short
        assert _guess_chapter_count(15000) == 10  # very long → capped at 10

    def test_chapter_count_never_zero(self):
        from novel_studio.benchmark.runner import _guess_chapter_count
        assert _guess_chapter_count(1) >= 1
        assert _guess_chapter_count(0) >= 1

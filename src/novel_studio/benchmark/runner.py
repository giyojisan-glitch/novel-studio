"""Benchmark runner: orchestrate extract → generate → judge → report."""
from __future__ import annotations

import re
import time
from pathlib import Path

from ..engine import advance
from ..llm import AnthropicProvider
from ..state import NovelState, UserInput
from ..utils import load_state, save_state
from .judge import judge_similarity
from .premise_extractor import extract_premise_from_file
from .schemas import BenchmarkCase, BenchmarkVerdict


BENCHMARKS_ROOT = Path(__file__).parent.parent.parent.parent / "benchmarks"


def _chinese_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _guess_chapter_count(word_count: int, words_per_chapter: int = 1000) -> int:
    """根据原文字数决定要让 NOVEL-Studio 生成几章。"""
    # Aim for similar total length
    n = max(1, min(10, round(word_count / words_per_chapter)))
    return n


def run_single(
    original_path: Path,
    provider: AnthropicProvider | None = None,
    pipeline_version: str = "v1",
    genre: str = "科幻",
    max_steps: int = 100,
) -> tuple[BenchmarkCase, BenchmarkVerdict]:
    """跑单个 case：原文 → premise → 生成 → 评估。

    Returns: (case, verdict)
    """
    provider = provider or AnthropicProvider()
    original_path = Path(original_path)
    original_text = original_path.read_text(encoding="utf-8")
    original_wc = _chinese_chars(original_text)

    case_name = original_path.stem
    BENCHMARKS_ROOT.mkdir(exist_ok=True)
    for sub in ("premises", "generated", "reports", "projects"):
        (BENCHMARKS_ROOT / sub).mkdir(exist_ok=True)

    # Step 1: extract premise
    premise_path = BENCHMARKS_ROOT / "premises" / f"{case_name}.md"
    if not premise_path.exists():
        premise_text = extract_premise_from_file(original_path, provider=provider)
        premise_path.write_text(premise_text, encoding="utf-8")
    else:
        premise_text = premise_path.read_text(encoding="utf-8")

    # Step 2: run NOVEL-Studio generation
    chapter_count = _guess_chapter_count(original_wc)
    ts = time.strftime("%Y%m%d-%H%M%S")
    pdir = BENCHMARKS_ROOT / "projects" / f"{case_name}_{ts}"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "queue").mkdir(exist_ok=True)
    (pdir / "responses").mkdir(exist_ok=True)

    state = NovelState(
        user_input=UserInput(
            premise=premise_text,
            genre=genre,
            chapter_count=chapter_count,
            target_words_per_chapter=1000,
            pipeline_version=pipeline_version,  # type: ignore[arg-type]
        )
    )
    save_state(pdir, state)

    # 循环 advance 到 DONE 或卡死
    for _ in range(max_steps):
        result = advance(state, pdir, provider=provider)
        if result.get("status") == "completed":
            break

    if not state.completed:
        raise RuntimeError(f"Generation did not complete after {max_steps} steps for {case_name}")

    generated_text = state.final_markdown
    generated_wc = _chinese_chars(generated_text)

    # 复制生成稿到 benchmarks/generated/
    generated_path = BENCHMARKS_ROOT / "generated" / f"{case_name}.md"
    generated_path.write_text(generated_text, encoding="utf-8")

    case = BenchmarkCase(
        name=case_name,
        original_path=original_path,
        premise_path=premise_path,
        premise_text=premise_text,
        generated_path=generated_path,
        generated_text=generated_text,
        original_word_count=original_wc,
        generated_word_count=generated_wc,
    )

    # Step 3: judge similarity
    verdict = judge_similarity(
        case_name=case_name,
        original_text=original_text,
        generated_text=generated_text,
        provider=provider,
    )

    # Step 4: write report
    report_path = BENCHMARKS_ROOT / "reports" / f"{case_name}.md"
    report_path.write_text(_render_report(case, verdict), encoding="utf-8")

    return case, verdict


def run_batch(
    corpus_dir: Path,
    provider: AnthropicProvider | None = None,
    pipeline_version: str = "v1",
    genre: str = "科幻",
    pattern: str = "*.md",
) -> list[tuple[BenchmarkCase, BenchmarkVerdict]]:
    """批量跑整个目录下的所有短篇文件。"""
    provider = provider or AnthropicProvider()
    corpus_dir = Path(corpus_dir)
    files = sorted(corpus_dir.glob(pattern))
    results = []
    for f in files:
        # 跳过隐藏/模板文件
        if f.name.startswith("_") or f.name.startswith("."):
            continue
        try:
            case, verdict = run_single(
                f, provider=provider, pipeline_version=pipeline_version, genre=genre,
            )
            results.append((case, verdict))
        except Exception as e:
            # 单个失败不打断 batch
            print(f"[BENCHMARK FAIL] {f.name}: {e}")

    # 写总 summary
    if results:
        summary_path = BENCHMARKS_ROOT / "reports" / "_SUMMARY.md"
        summary_path.write_text(_render_summary(results), encoding="utf-8")

    return results


def _render_report(case: BenchmarkCase, verdict: BenchmarkVerdict) -> str:
    L = [f"# Benchmark Report · {case.name}"]
    L.append(f"\n**Overall Score**: {verdict.overall_score:.3f} / 1.000   "
             f"**{'✅ PASS' if verdict.passed else '❌ FAIL'}**")
    L.append(f"\n**Pass threshold**: 0.70   **Judge model**: {verdict.judge_model}\n")
    L.append(f"- 原文字数：{case.original_word_count}（{case.original_path.name}）")
    L.append(f"- 生成字数：{case.generated_word_count}（{case.generated_path.name if case.generated_path else '?'}）")
    L.append(f"- Premise：{case.premise_path.name if case.premise_path else '?'}\n")

    L.append("## 维度分数\n")
    L.append("| 维度 | 分数 | 权重 | 加权 |")
    L.append("|---|---|---|---|")
    from .schemas import DIMENSION_WEIGHTS
    for ds in verdict.dimension_scores:
        w = DIMENSION_WEIGHTS.get(ds.dimension, 0.0)
        L.append(f"| {ds.dimension} | {ds.score:.2f} | {w:.0%} | {ds.score * w:.3f} |")

    L.append("\n## 法官点评\n")
    if verdict.notes:
        L.append(f"> {verdict.notes}\n")

    L.append("\n## 分维度详情\n")
    for ds in verdict.dimension_scores:
        L.append(f"### {ds.dimension} ({ds.score:.2f})\n")
        L.append(f"**理由**：{ds.rationale}\n")
        if ds.alignments:
            L.append("**对齐点**：")
            for a in ds.alignments:
                L.append(f"- {a}")
            L.append("")
        if ds.divergences:
            L.append("**偏离点**：")
            for d in ds.divergences:
                L.append(f"- {d}")
            L.append("")

    return "\n".join(L)


def _render_summary(results: list[tuple[BenchmarkCase, BenchmarkVerdict]]) -> str:
    total = len(results)
    passed = sum(1 for _, v in results if v.passed)
    pass_rate = passed / total if total else 0.0

    L = ["# Benchmark Summary\n"]
    L.append(f"**Pass rate**: {passed} / {total} = **{pass_rate:.1%}**")
    L.append(f"**Average score**: {sum(v.overall_score for _, v in results) / total:.3f}\n")

    L.append("## 逐案结果\n")
    L.append("| 案例 | 总分 | 结论 | 原文字数 | 生成字数 |")
    L.append("|---|---|---|---|---|")
    for case, verdict in results:
        status = "✅" if verdict.passed else "❌"
        L.append(
            f"| {case.name} | {verdict.overall_score:.3f} | {status} | "
            f"{case.original_word_count} | {case.generated_word_count} |"
        )

    # 每维平均分
    L.append("\n## 分维度平均\n")
    L.append("| 维度 | 平均分 |")
    L.append("|---|---|")
    from .schemas import DIMENSION_WEIGHTS
    for dim in DIMENSION_WEIGHTS:
        scores = [
            ds.score for _, v in results for ds in v.dimension_scores
            if ds.dimension == dim
        ]
        if scores:
            avg = sum(scores) / len(scores)
            L.append(f"| {dim} | {avg:.3f} |")

    return "\n".join(L)

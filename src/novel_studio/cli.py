"""CLI 入口：init / step / status。"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from .state import NovelState, UserInput
from .utils import make_project_dir, save_state, load_state, queue_pending, resolve_input_file, INPUTS_ROOT, export_artifacts, ARTIFACTS_ROOT
from .engine import advance
from .slop_check import scan as slop_scan
from .llm import get_provider, AnthropicProvider
from .benchmark import run_single as bench_run_single, run_batch as bench_run_batch


console = Console()


def cmd_init(args):
    # 解析 premise：优先 --file，否则用位置参数
    if args.file:
        path = resolve_input_file(args.file)
        premise = path.read_text(encoding="utf-8").strip()
        source_note = f"📄 从文件读取：{path}"
    elif args.premise:
        premise = args.premise.strip()
        source_note = None
    else:
        console.print(f"[red]✗[/] 需要 premise（直接传字符串）或 --file（从 {INPUTS_ROOT}/ 读文件）")
        sys.exit(1)

    if len(premise) < 80:
        console.print(
            f"[yellow]⚠ premise 只有 {len(premise)} 字，AI 会大量脑补导致剧情散。[/]\n"
            f"[dim]建议 150+ 字，含：主角轮廓、核心冲突、世界锚点、基调、至少一个具体场景。[/]"
        )
        if not args.force:
            console.print(f"[dim]确认继续加 --force，或把详细 premise 写到 {INPUTS_ROOT}/ 下用 --file。[/]")
            sys.exit(1)

    pdir = make_project_dir(premise)
    state = NovelState(
        user_input=UserInput(
            premise=premise,
            genre=args.genre,
            chapter_count=args.chapters,
            target_words_per_chapter=args.words,
            pipeline_version="v2" if args.v2 else "v1",
        )
    )
    save_state(pdir, state)

    # 立即 dispatch L1
    provider = get_provider(args.provider)
    result = advance(state, pdir, provider=provider)

    console.print(Panel.fit(
        f"[bold cyan]NOVEL-Studio[/] 项目已创建\n\n"
        f"📁 [yellow]{pdir}[/]\n"
        f"📖 前提：{args.premise}\n"
        f"🎭 类型：{args.genre} · 章节数：{args.chapters} · 每章 {args.words} 字\n",
        title="✓ init"
    ))
    _print_status(state, pdir, result)
    print(str(pdir))  # 最后一行打印路径，便于 shell 捕获


def cmd_step(args):
    pdir = Path(args.project_dir)
    state = load_state(pdir)
    provider = get_provider(args.provider)
    result = advance(state, pdir, provider=provider)
    _print_status(state, pdir, result)


def cmd_status(args):
    pdir = Path(args.project_dir)
    state = load_state(pdir)
    pending = queue_pending(pdir)
    _print_status(state, pdir, {"status": "waiting" if pending else "idle", "step_ids": pending, "next_step": state.next_step})


def cmd_artifacts(args):
    pdir = Path(args.project_dir)
    state = load_state(pdir)
    adir = export_artifacts(state, pdir)
    console.print(f"[green]✓[/] 中间产物已导出：[cyan]{adir}[/]")
    for f in sorted(adir.glob("*.md")):
        size_kb = f.stat().st_size / 1024
        console.print(f"  • {f.name}  [dim]({size_kb:.1f} KB)[/]")


def cmd_benchmark_one(args):
    """单个 corpus 文件跑 benchmark：extract premise → generate → judge。"""
    path = Path(args.file)
    if not path.exists():
        console.print(f"[red]✗[/] 文件不存在：{path}")
        sys.exit(1)

    provider = AnthropicProvider()
    if not provider.api_key:
        console.print("[red]✗[/] benchmark 需要 ANTHROPIC_API_KEY。请设置后再跑。")
        sys.exit(1)

    console.print(f"[cyan]→[/] Benchmarking [bold]{path.name}[/]...")
    console.print(f"[dim]  pipeline={args.pipeline} · genre={args.genre}[/]")

    case, verdict = bench_run_single(
        path, provider=provider,
        pipeline_version=args.pipeline, genre=args.genre,
    )

    color = "green" if verdict.passed else "red"
    mark = "✅ PASS" if verdict.passed else "❌ FAIL"
    console.print(f"\n[bold {color}]{mark}[/]  总分 {verdict.overall_score:.3f} / 1.000  (阈值 0.70)")
    console.print(f"[dim]  judge: {verdict.judge_model}[/]\n")

    t = Table(show_header=True, header_style="bold")
    t.add_column("维度")
    t.add_column("分数", justify="right")
    t.add_column("权重", justify="right")
    t.add_column("理由", overflow="fold")
    from .benchmark.schemas import DIMENSION_WEIGHTS
    for ds in verdict.dimension_scores:
        w = DIMENSION_WEIGHTS.get(ds.dimension, 0.0)
        t.add_row(ds.dimension, f"{ds.score:.2f}", f"{w:.0%}", ds.rationale)
    console.print(t)

    if verdict.notes:
        console.print(f"\n[yellow]法官点评：[/]{verdict.notes}")

    from .benchmark.runner import BENCHMARKS_ROOT
    console.print(f"\n📄 详细报告：[cyan]{BENCHMARKS_ROOT / 'reports' / (case.name + '.md')}[/]")


def cmd_benchmark_batch(args):
    """批量跑 corpus 目录下所有 .md 文件。"""
    corpus = Path(args.corpus_dir)
    if not corpus.is_dir():
        console.print(f"[red]✗[/] 目录不存在：{corpus}")
        sys.exit(1)

    provider = AnthropicProvider()
    if not provider.api_key:
        console.print("[red]✗[/] benchmark 需要 ANTHROPIC_API_KEY。")
        sys.exit(1)

    from .benchmark.runner import _collect_source_files
    files = _collect_source_files(corpus, recursive=not args.no_recursive)
    if args.limit is not None:
        files = files[:args.limit]
    if not files:
        console.print(f"[yellow]⚠[/] {corpus}/ 下没有 .md / .txt 文件")
        sys.exit(0)

    console.print(f"[cyan]→[/] 批量跑 [bold]{len(files)}[/] 个案例（recursive={not args.no_recursive}）\n")

    results = bench_run_batch(
        corpus, provider=provider,
        pipeline_version=args.pipeline, genre=args.genre,
        recursive=not args.no_recursive, limit=args.limit,
    )

    if not results:
        console.print("[red]✗ 全部失败[/]")
        sys.exit(1)

    passed = sum(1 for _, v in results if v.passed)
    pass_rate = passed / len(results)
    avg = sum(v.overall_score for _, v in results) / len(results)

    color = "green" if pass_rate >= 0.70 else "yellow" if pass_rate >= 0.50 else "red"
    console.print(f"\n[bold {color}]Pass Rate: {passed}/{len(results)} = {pass_rate:.1%}[/]")
    console.print(f"[dim]Average score: {avg:.3f}[/]\n")

    t = Table(show_header=True, header_style="bold")
    t.add_column("案例")
    t.add_column("总分", justify="right")
    t.add_column("判定", justify="center")
    for case, verdict in results:
        status = "[green]✅[/]" if verdict.passed else "[red]❌[/]"
        t.add_row(case.name, f"{verdict.overall_score:.3f}", status)
    console.print(t)

    from .benchmark.runner import BENCHMARKS_ROOT
    console.print(f"\n📄 Summary: [cyan]{BENCHMARKS_ROOT / 'reports' / '_SUMMARY.md'}[/]")


def cmd_slop(args):
    """机械 slop 扫描：不调 LLM，仅用词表+正则。"""
    path = Path(args.file)
    if not path.exists():
        console.print(f"[red]✗[/] 文件不存在：{path}")
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    report = slop_scan(text)

    # 分数彩色显示
    if report.score < 2.0:
        color = "green"
        verdict = "✓ 干净"
    elif report.score < 4.0:
        color = "yellow"
        verdict = "⚠ 轻度 AI 味"
    elif report.score < 6.5:
        color = "bright_yellow"
        verdict = "⚠ 中度 AI 味"
    else:
        color = "red"
        verdict = "✗ 重度 AI 味"

    console.print(f"\n[bold {color}]Slop Score: {report.score:.2f} / 10.0   {verdict}[/]")
    console.print(f"[dim]  {path.name} · {report.stats['chinese_chars']} 中文字 · "
                  f"{report.stats['paragraphs']} 段 · {report.stats['sentences']} 句[/]\n")

    if not report.hits:
        console.print("[green]没有命中任何规则。[/]")
        return

    if args.verbose:
        console.print(report.detailed())
    else:
        # 简略：Top 10 扣分
        top = report.hits[:10]
        console.print(f"[bold]Top {len(top)} 命中：[/]")
        for h in top:
            console.print(f"  [yellow]•[/] {h}")
        if len(report.hits) > 10:
            console.print(f"  [dim]... 还有 {len(report.hits) - 10} 条。加 --verbose 看全部[/]")


def _print_status(state: NovelState, pdir: Path, result: dict):
    status = result.get("status")
    next_step = result.get("next_step", state.next_step)

    table = Table(title="进度", show_header=False, box=None)
    table.add_row("📋 next_step", f"[bold yellow]{next_step}[/]")
    table.add_row("📄 L1 骨架", "[green]✓[/]" if state.l1 else "[dim]·[/]")
    l2_cnt = len(state.l2)
    l3_cnt = len(state.l3)
    total = state.user_input.chapter_count
    table.add_row("📑 L2 章节梗概", f"[green]{l2_cnt}[/]/{total}")
    table.add_row("📝 L3 段落写作", f"[green]{l3_cnt}[/]/{total}")
    table.add_row("🔍 audit 次数", f"[cyan]{len(state.audit_history)}[/]")
    if state.audit_history:
        last = state.audit_history[-1]
        verdict = "[green]通过[/]" if last.passed else "[red]打回[/]"
        scores = ", ".join(f"{r.head}={r.score:.2f}" for r in last.reports)
        table.add_row("  └ 最近一次", f"{verdict} ({scores})")
    console.print(table)

    if status == "completed":
        console.print(Panel.fit(
            f"[bold green]🎉 完成[/]\n\n📄 输出：{result.get('output')}\n",
            title="✓ DONE"
        ))
        return

    if status in ("dumped", "advanced"):
        sids = result.get("step_ids", [])
        if sids:
            files = "\n".join(f"  • [cyan]queue/{sid}.prompt.md[/]" for sid in sids)
            console.print(Panel(
                f"[yellow]⏸ 等待 Claude 响应以下 prompt[/]：\n{files}\n\n"
                f"[dim]Claude：读 prompt 后写 JSON 到 [cyan]responses/{{step_id}}.response.json[/]，"
                f"然后再跑 [bold]novel-studio step {pdir}[/]。[/]",
                title="next ⤵"
            ))
    elif status == "waiting":
        sids = result.get("step_ids", [])
        files = "\n".join(f"  • [cyan]queue/{sid}.prompt.md[/]" for sid in sids)
        console.print(Panel(
            f"[yellow]⏸ 仍在等待响应[/]：\n{files}",
            title="waiting"
        ))


def main():
    parser = argparse.ArgumentParser(prog="novel-studio", description="一句话生成完整小说")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="新建项目，dump L1 骨架 prompt")
    p_init.add_argument("premise", nargs="?", default=None, help="直接传 premise 字符串（短则 AI 脑补多）")
    p_init.add_argument("--file", "-f", help="从 inputs/ 下的文件读取 premise（推荐，150+ 字）")
    p_init.add_argument("--genre", default="科幻", choices=["科幻", "悬疑", "武侠", "都市", "奇幻", "仙侠", "历史"])
    p_init.add_argument("--chapters", type=int, default=3)
    p_init.add_argument("--words", type=int, default=1000)
    p_init.add_argument("--force", action="store_true", help="绕过 premise 长度检查")
    p_init.add_argument("--v2", action="store_true",
                        help="启用 V2 pipeline（final_audit + L4 adversarial + L4 scrubber）")
    p_init.add_argument("--provider", default=None,
                        help="LLM provider: human_queue (默认) / anthropic / stub")
    p_init.set_defaults(func=cmd_init)

    p_step = sub.add_parser("step", help="读响应、推进到下一步")
    p_step.add_argument("project_dir")
    p_step.add_argument("--provider", default=None,
                        help="LLM provider: human_queue (默认) / anthropic / stub")
    p_step.set_defaults(func=cmd_step)

    p_status = sub.add_parser("status", help="查看当前项目进度")
    p_status.add_argument("project_dir")
    p_status.set_defaults(func=cmd_status)

    p_art = sub.add_parser("artifacts", help="回填 artifacts/ 中间产物（已有项目也能用）")
    p_art.add_argument("project_dir")
    p_art.set_defaults(func=cmd_artifacts)

    p_slop = sub.add_parser("slop", help="机械 slop 检测（不调 LLM，仅词表+正则）")
    p_slop.add_argument("file", help="要扫描的 markdown / 文本文件")
    p_slop.add_argument("-v", "--verbose", action="store_true", help="显示所有命中（默认只 Top 10）")
    p_slop.set_defaults(func=cmd_slop)

    p_bone = sub.add_parser("benchmark-one",
                             help="对单篇短篇跑 TDD 评估（extract→generate→judge）")
    p_bone.add_argument("file", help="原文 markdown 路径")
    p_bone.add_argument("--genre", default="科幻",
                        choices=["科幻", "悬疑", "武侠", "都市", "奇幻", "仙侠", "历史"])
    p_bone.add_argument("--pipeline", default="v1", choices=["v1", "v2"],
                        help="NOVEL-Studio pipeline 版本（v2 含 L4 润色，更贵）")
    p_bone.set_defaults(func=cmd_benchmark_one)

    p_ball = sub.add_parser("benchmark",
                             help="批量跑 corpus/ 下所有短篇（支持 .md/.txt 递归 + 自动 genre 推断）")
    p_ball.add_argument("corpus_dir", help="放原文的目录（如 benchmarks/corpus）")
    p_ball.add_argument("--genre", default=None,
                        choices=["科幻", "悬疑", "武侠", "都市", "奇幻", "仙侠", "历史"],
                        help="指定 genre（默认按子目录自动推断）")
    p_ball.add_argument("--pipeline", default="v1", choices=["v1", "v2"])
    p_ball.add_argument("--limit", type=int, default=None,
                        help="只跑前 N 篇（试水用）")
    p_ball.add_argument("--no-recursive", action="store_true",
                        help="只扫 corpus_dir 顶层，不进子目录")
    p_ball.set_defaults(func=cmd_benchmark_batch)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        console.print(f"[bold red]✗ 错误：[/]{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""NOVEL-Studio MCP server — 把多层小说管线暴露给 Claude Code 当工具。

工作流(provider="human_queue",对话内 Claude 充当 LLM):
1. novel_init(...) → 返回 project_dir + pending_prompt(L1 提示词内容)
2. Claude 读 pending_prompt → 思考 → 生成符合 schema 的 JSON
3. novel_step(project_dir, response={...}) → 写响应 + advance,返回下一步 pending_prompt
4. 循环 3 直至 done=True、final_path 出现

provider="doubao" / "anthropic" 时管线全自动;novel_step 不需要 response 参数。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass

from .engine import advance
from .llm import get_provider
from .state import NovelState, UserInput
from .utils import (
    PROJECTS_ROOT,
    load_state,
    make_project_dir,
    queue_pending,
    save_state,
)


mcp = FastMCP(
    "novel-studio",
    instructions=(
        "中文小说多层状态机管线。novel_init 起项目,novel_step 推进。"
        "默认 provider=human_queue:Claude 在对话里充当 LLM,每次返回的 pending_prompt "
        "就是当前要响应的提示词,你产出 JSON 后通过 novel_step(response=...) 提交并推进下一步。"
    ),
)


def _read_pending_prompt(pdir: Path, step_id: Optional[str]) -> Optional[str]:
    if not step_id:
        return None
    p = pdir / "queue" / f"{step_id}.prompt.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _envelope(state: NovelState, pdir: Path, result: dict) -> dict:
    pending = queue_pending(pdir)
    pending_step = pending[0] if pending else None
    novel_md = pdir / "novel.md"
    return {
        "project_dir": str(pdir),
        "status": result.get("status"),
        "next_step": result.get("next_step") or state.next_step,
        "pending_step": pending_step,
        "pending_steps_all": pending,
        "pending_prompt": _read_pending_prompt(pdir, pending_step),
        "done": state.completed or novel_md.exists(),
        "final_path": str(novel_md) if novel_md.exists() else None,
        "top_output": result.get("output"),
    }


@mcp.tool()
def novel_init(
    premise: str,
    genre: str = "科幻",
    chapters: int = 3,
    words_per_chapter: int = 1500,
    pipeline: str = "v6",
    provider: str = "human_queue",
    creativity: str = "balanced",
    language: str = "zh",
    scenes_per_chapter: int = 4,
) -> dict:
    """新建小说项目并触发首步(L1 骨架)。

    Args:
        premise: 一句话或一段前提(>=80 字推荐)。
        genre: 科幻/悬疑/武侠/都市/奇幻/仙侠/历史/日轻/志怪。
        chapters: 章节数。
        words_per_chapter: 每章目标字数。
        pipeline: v1/v2/v3/v4/v5/v6,推荐 v6(最完整防漂)。
        provider: human_queue(对话内 Claude 当 LLM,默认) / doubao / anthropic / stub。
        creativity: strict / balanced / creative。
        language: zh / ja。
        scenes_per_chapter: V4+ L2.5 每章场景数软目标。

    Returns:
        envelope dict,含 pending_prompt(若 human_queue)供 Claude 读后产出 JSON。
    """
    state = NovelState(
        user_input=UserInput(
            premise=premise.strip(),
            genre=genre,
            chapter_count=chapters,
            target_words_per_chapter=words_per_chapter,
            language=language,
            creativity=creativity,
            pipeline_version=pipeline,
            scenes_per_chapter_hint=scenes_per_chapter,
        )
    )
    pdir = make_project_dir(premise)
    save_state(pdir, state)
    prov = get_provider(provider)
    result = advance(state, pdir, provider=prov)
    state = load_state(pdir)
    return _envelope(state, pdir, result)


@mcp.tool()
def novel_step(
    project_dir: str,
    response: Optional[dict] = None,
    provider: str = "human_queue",
) -> dict:
    """推进项目一步。

    Args:
        project_dir: novel_init 返回的 project_dir。
        response: 当前 pending step 的 LLM 响应 JSON(human_queue 模式必填)。
                  必须严格匹配 pending_prompt 里给出的 schema。
        provider: 与 init 时一致。

    Returns:
        envelope dict;若 done=True 则 final_path 指向 novel.md。
    """
    pdir = Path(project_dir)
    if not (pdir / "state.json").exists():
        raise FileNotFoundError(f"项目不存在: {pdir}")

    if response is not None:
        pending = queue_pending(pdir)
        if not pending:
            raise ValueError("没有 pending step,response 无处写入")
        step_id = pending[0]
        target = pdir / "responses" / f"{step_id}.response.json"
        target.write_text(
            json.dumps(response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    state = load_state(pdir)
    prov = get_provider(provider)
    result = advance(state, pdir, provider=prov)
    state = load_state(pdir)
    return _envelope(state, pdir, result)


@mcp.tool()
def novel_status(project_dir: str) -> dict:
    """查项目当前状态(不推进)。"""
    pdir = Path(project_dir)
    if not (pdir / "state.json").exists():
        raise FileNotFoundError(f"项目不存在: {pdir}")
    state = load_state(pdir)
    pending = queue_pending(pdir)
    pending_step = pending[0] if pending else None
    novel_md = pdir / "novel.md"
    return {
        "project_dir": str(pdir),
        "next_step": state.next_step,
        "pending_step": pending_step,
        "pending_steps_all": pending,
        "pending_prompt": _read_pending_prompt(pdir, pending_step),
        "done": state.completed or novel_md.exists(),
        "final_path": str(novel_md) if novel_md.exists() else None,
        "premise": state.user_input.premise,
        "genre": state.user_input.genre,
        "chapter_count": state.user_input.chapter_count,
        "pipeline": state.user_input.pipeline_version,
        "creativity": state.user_input.creativity,
    }


@mcp.tool()
def novel_read_artifact(project_dir: str, name: str = "novel.md") -> str:
    """读项目内文件(novel.md / state.json / queue/*.prompt.md / responses/*.json 等)。

    Args:
        name: 相对 project_dir 的路径,禁逃逸。
    """
    pdir = Path(project_dir).resolve()
    target = (pdir / name).resolve()
    try:
        target.relative_to(pdir)
    except ValueError as e:
        raise ValueError(f"非法路径: {name}") from e
    if not target.exists():
        raise FileNotFoundError(f"不存在: {target}")
    return target.read_text(encoding="utf-8")


@mcp.tool()
def novel_list_projects(limit: int = 20) -> list[dict]:
    """列最近 limit 个项目(按 mtime 倒序)。"""
    if not PROJECTS_ROOT.exists():
        return []
    dirs = sorted(
        [p for p in PROJECTS_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out: list[dict] = []
    for d in dirs:
        try:
            state = load_state(d)
            novel_md = d / "novel.md"
            premise = state.user_input.premise
            out.append({
                "project_dir": str(d),
                "name": d.name,
                "premise": premise[:60] + ("..." if len(premise) > 60 else ""),
                "genre": state.user_input.genre,
                "pipeline": state.user_input.pipeline_version,
                "next_step": state.next_step,
                "done": state.completed or novel_md.exists(),
            })
        except Exception as e:
            out.append({"project_dir": str(d), "name": d.name, "error": str(e)})
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

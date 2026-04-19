"""Step engine：多层状态机推进的核心逻辑。

step 推进规则：
1. load state，根据 state.next_step 算出 _expected_prompts(next_step)
2. 若任一 prompt 还没 dump → dump 之，return waiting
3. 若有响应缺失 → return waiting
4. 全部响应到齐 → apply 到 state、聚合 audit、推进 next_step、立刻 dump 下一组
"""
from __future__ import annotations
import json
from pathlib import Path
from .state import (
    NovelState,
    L1Skeleton,
    L2ChapterOutline,
    L3ChapterDraft,
    L4PolishedChapter,
    AuditReport,
)
from . import prompts as P
from .audit import aggregate, should_force_pass, MAX_REVISION
from .utils import write_prompt, read_response, save_state, export_markdown, export_top, export_artifacts


# ====== expected prompts mapping ======


def expected_prompts(next_step: str) -> list[str]:
    """next_step → 该步需要哪些 prompt 文件。audit 步有 2 个 head 并行。"""
    if next_step == "finalize" or next_step == "DONE":
        return []
    if next_step.endswith("_audit"):
        base = next_step[:-len("_audit")]
        return [f"{base}_audit_logic", f"{base}_audit_pace"]
    return [next_step]


def build_prompt(state: NovelState, step_id: str) -> str:
    """根据 step_id 构造对应的 prompt 文本。"""
    if step_id == "L1":
        return P.l1_prompt(state)
    if step_id.startswith("L2_") and not step_id.endswith(("_logic", "_pace")):
        idx = int(step_id.split("_")[1])
        return P.l2_prompt(state, idx)
    if step_id.startswith("L3_") and not step_id.endswith(("_logic", "_pace")):
        idx = int(step_id.split("_")[1])
        return P.l3_prompt(state, idx)
    if step_id.endswith("_audit_logic") or step_id.endswith("_audit_pace"):
        head = "logic" if step_id.endswith("_logic") else "pace"
        body = step_id.rsplit("_audit_", 1)[0]
        if body == "L1":
            return P.audit_prompt(state, "L1", None, head)
        layer, idx_s = body.split("_", 1)
        return P.audit_prompt(state, layer, int(idx_s), head)
    raise ValueError(f"unknown step_id: {step_id}")


# ====== 应用响应 ======


def apply_responses(state: NovelState, step_ids: list[str], pdir: Path) -> None:
    """把响应 JSON 解析并写入 state 对应字段。"""
    if len(step_ids) == 1:
        sid = step_ids[0]
        data = read_response(pdir, sid)
        if sid == "L1":
            state.l1 = L1Skeleton(**data)
        elif sid.startswith("L2_"):
            outline = L2ChapterOutline(**data)
            _upsert_l2(state, outline)
        elif sid.startswith("L3_"):
            draft = L3ChapterDraft(**data)
            _upsert_l3(state, draft)
    else:
        # audit 一组：2 头 → 聚合
        reports = []
        for sid in step_ids:
            data = read_response(pdir, sid)
            reports.append(AuditReport(**data))
        layer, idx = _parse_audit_target(step_ids[0])
        verdict = aggregate(layer, idx, reports)
        state.audit_history.append(verdict)


def _upsert_l2(state: NovelState, outline: L2ChapterOutline) -> None:
    state.l2 = [c for c in state.l2 if c.index != outline.index] + [outline]
    state.l2.sort(key=lambda c: c.index)


def _upsert_l3(state: NovelState, draft: L3ChapterDraft) -> None:
    state.l3 = [d for d in state.l3 if d.index != draft.index] + [draft]
    state.l3.sort(key=lambda d: d.index)


def _parse_audit_target(audit_step_id: str) -> tuple[str, int | None]:
    body = audit_step_id.rsplit("_audit_", 1)[0]
    if body == "L1":
        return "L1", None
    layer, idx_s = body.split("_", 1)
    return layer, int(idx_s)


# ====== 决定下一步 ======


def decide_next(state: NovelState) -> str:
    """根据 state 当前状况返回下一个 step。"""
    cur = state.next_step
    total = state.user_input.chapter_count

    # 主 prompt 完成 → 进 audit
    if cur == "L1":
        return "L1_audit"
    if cur.startswith("L2_") and not cur.endswith("_audit"):
        return cur + "_audit"
    if cur.startswith("L3_") and not cur.endswith("_audit"):
        return cur + "_audit"

    # audit 完成 → 看 verdict
    if cur.endswith("_audit"):
        body = cur[:-len("_audit")]
        verdict = state.audit_history[-1]

        if body == "L1":
            current_rev = state.l1.revision
            if verdict.passed or should_force_pass(current_rev):
                return "L2_1"
            state.l1.revision += 1
            return "L1"  # 重写

        layer, idx_s = body.split("_", 1)
        idx = int(idx_s)
        if layer == "L2":
            cur_outline = next(c for c in state.l2 if c.index == idx)
            current_rev = cur_outline.revision
            if verdict.passed or should_force_pass(current_rev):
                if idx < total:
                    return f"L2_{idx + 1}"
                return "L3_1"
            cur_outline.revision += 1
            return f"L2_{idx}"
        if layer == "L3":
            cur_draft = next(d for d in state.l3 if d.index == idx)
            current_rev = cur_draft.revision
            if verdict.passed or should_force_pass(current_rev):
                if idx < total:
                    return f"L3_{idx + 1}"
                return "finalize"
            cur_draft.revision += 1
            return f"L3_{idx}"

    if cur == "finalize":
        return "DONE"

    raise ValueError(f"unknown next_step: {cur}")


# ====== 重试时清理对应 queue/response ======


def reset_step_files(pdir: Path, step_ids: list[str]) -> None:
    """删除给定 step 的 prompt 和 response 文件，让其重新 dump/响应。"""
    for sid in step_ids:
        for sub in ("queue", "responses"):
            p = pdir / sub / (
                f"{sid}.prompt.md" if sub == "queue" else f"{sid}.response.json"
            )
            if p.exists():
                p.unlink()


# ====== 主 advance ======


def advance(state: NovelState, pdir: Path) -> dict:
    """单步推进。返回状态字典：{status, step_ids, next_step, ...}"""
    # finalize 步：不需要 LLM，直接生成 MD
    if state.next_step == "finalize":
        state.final_markdown = export_markdown(state)
        # L4 透传：把 l3 拷到 l4
        state.l4 = [
            L4PolishedChapter(index=d.index, content=d.content) for d in state.l3
        ]
        # 项目根直接放 novel.md（不再套 output 子目录）
        local_md = pdir / "novel.md"
        local_md.write_text(state.final_markdown, encoding="utf-8")
        (pdir / "trace.json").write_text(
            json.dumps(state.trace, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        # 顶层 outputs/ 按标题命名，方便一眼找到
        top_md = export_top(state, pdir, state.final_markdown)
        state.next_step = "DONE"
        state.completed = True
        save_state(pdir, state)
        return {"status": "completed", "output": str(top_md), "project": str(local_md)}

    if state.next_step == "DONE":
        return {"status": "completed", "output": "（已完成）"}

    expected = expected_prompts(state.next_step)

    # 1) 是否需要 dump prompt
    missing_prompts = [
        sid for sid in expected if not (pdir / "queue" / f"{sid}.prompt.md").exists()
    ]
    if missing_prompts:
        for sid in missing_prompts:
            text = build_prompt(state, sid)
            write_prompt(pdir, sid, text)
        save_state(pdir, state)
        return {
            "status": "dumped",
            "step_ids": missing_prompts,
            "next_step": state.next_step,
        }

    # 2) 是否所有响应到齐
    missing_resp = [sid for sid in expected if read_response(pdir, sid) is None]
    if missing_resp:
        return {
            "status": "waiting",
            "step_ids": missing_resp,
            "next_step": state.next_step,
        }

    # 3) 全到齐 → apply、决定下一步
    apply_responses(state, expected, pdir)
    state.trace.append({"step": state.next_step, "applied": expected})
    # 每次推进后同步导出人类可读的 artifacts
    export_artifacts(state, pdir)
    new_next = decide_next(state)

    # 重试场景：清理被打回的层的旧文件
    if state.next_step.endswith("_audit") and not state._is_advance_audit():
        pass  # 不需要

    if _is_retry(state.next_step, new_next):
        reset_step_files(pdir, [new_next] + expected_prompts(new_next + "_audit"))

    state.next_step = new_next
    save_state(pdir, state)

    # 立即 dump 下一步的 prompt（如果不是终态）
    if new_next not in ("finalize", "DONE"):
        next_expected = expected_prompts(new_next)
        for sid in next_expected:
            if not (pdir / "queue" / f"{sid}.prompt.md").exists():
                text = build_prompt(state, sid)
                write_prompt(pdir, sid, text)
        return {"status": "advanced", "step_ids": next_expected, "next_step": new_next}

    if new_next == "finalize":
        # 直接进 finalize 处理
        return advance(state, pdir)

    return {"status": "advanced", "step_ids": [], "next_step": new_next}


def _is_retry(prev: str, new: str) -> bool:
    """新 next_step 是否是退回到上一层（重试）。"""
    if not prev.endswith("_audit"):
        return False
    body = prev[:-len("_audit")]
    return body == new


# 给 NovelState 加一个空方法占位（避免 AttributeError，后续 V2 可能有 advance 类型审稿）
def _is_advance_audit(self):  # pragma: no cover
    return False


NovelState._is_advance_audit = _is_advance_audit

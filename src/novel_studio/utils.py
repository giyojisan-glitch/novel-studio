"""工具函数：项目目录、state 持久化、slug。"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from slugify import slugify
from .state import NovelState


ROOT = Path(__file__).parent.parent.parent
PROJECTS_ROOT = ROOT / "projects"
OUTPUTS_ROOT = ROOT / "outputs"
INPUTS_ROOT = ROOT / "inputs"
STYLES_ROOT = ROOT / "styles"
ARTIFACTS_ROOT = ROOT / "artifacts"


def export_artifacts(state: NovelState, pdir: Path) -> Path:
    """把 state 各层以人类可读的 markdown 导出到 artifacts/{ts}/。

    每次 step 推进后调用一次，层层产出可见。
    """
    ARTIFACTS_ROOT.mkdir(exist_ok=True)
    adir = ARTIFACTS_ROOT / pdir.name
    adir.mkdir(exist_ok=True)

    (adir / "00_premise.md").write_text(
        f"# Premise（用户原始输入）\n\n"
        f"**类型**：{state.user_input.genre} · "
        f"**章节**：{state.user_input.chapter_count} · "
        f"**每章字数**：{state.user_input.target_words_per_chapter}\n\n"
        f"---\n\n{state.user_input.premise}\n",
        encoding="utf-8",
    )

    if state.l1:
        (adir / "01_L1_骨架.md").write_text(_render_l1(state), encoding="utf-8")
    if state.l2:
        (adir / "02_L2_章节梗概.md").write_text(_render_l2(state), encoding="utf-8")
    if state.l3:
        (adir / "03_L3_正文草稿.md").write_text(_render_l3(state), encoding="utf-8")
    if state.audit_history:
        (adir / "04_audit_历程.md").write_text(_render_audits(state), encoding="utf-8")
    return adir


def _render_l1(state: NovelState) -> str:
    l1 = state.l1
    L = [f"# L1 骨架\n"]
    L.append(f"**标题**：{l1.title}")
    L.append(f"**Logline**：{l1.logline}")
    L.append(f"**主题**：{l1.theme}\n")
    L.append("## 主角")
    L.append(f"- **姓名**：{l1.protagonist.name}")
    L.append("- **特质**：")
    for t in l1.protagonist.traits:
        L.append(f"  - {t}")
    L.append(f"- **want（外在）**：{l1.protagonist.want}")
    L.append(f"- **need（内在）**：{l1.protagonist.need}\n")
    if l1.antagonist:
        L.append("## 反派")
        L.append(f"- **姓名**：{l1.antagonist.name}")
        L.append("- **特质**：")
        for t in l1.antagonist.traits:
            L.append(f"  - {t}")
        L.append(f"- **want**：{l1.antagonist.want}")
        L.append(f"- **need**：{l1.antagonist.need}\n")
    L.append("## 三幕结构")
    L.append(f"**1. Setup**：{l1.three_act.setup}\n")
    L.append(f"**2. Confrontation**：{l1.three_act.confrontation}\n")
    L.append(f"**3. Resolution**：{l1.three_act.resolution}\n")
    L.append("## 世界规则")
    for i, r in enumerate(l1.world_rules, 1):
        L.append(f"{i}. {r}")
    L.append(f"\n---\n*重写次数：{l1.revision}*")
    return "\n".join(L)


def _render_l2(state: NovelState) -> str:
    total = state.user_input.chapter_count
    L = [f"# L2 章节梗概（{len(state.l2)}/{total} 完成）\n"]
    for c in sorted(state.l2, key=lambda x: x.index):
        L.append(f"## 第 {c.index} 章 · {c.title}\n")
        L.append(f"**视角**：{c.pov}\n")
        L.append(f"**承接上章**：{c.prev_connection}\n")
        L.append(f"**梗概**：\n\n> {c.summary}\n")
        L.append(f"**关键事件**：")
        for i, e in enumerate(c.key_events, 1):
            L.append(f"{i}. {e}")
        L.append(f"\n**章末钩子**：\n\n> {c.hook}\n")
        L.append(f"*重写次数：{c.revision}*\n")
        L.append("---\n")
    return "\n".join(L)


def _render_l3(state: NovelState) -> str:
    total = state.user_input.chapter_count
    L = [f"# L3 正文草稿（{len(state.l3)}/{total} 完成）\n"]
    for d in sorted(state.l3, key=lambda x: x.index):
        l2 = next((c for c in state.l2 if c.index == d.index), None)
        title = l2.title if l2 else f"第 {d.index} 章"
        L.append(f"## 第 {d.index} 章 · {title}")
        L.append(f"*{d.word_count} 字 · 重写 {d.revision} 次*\n")
        L.append(d.content)
        L.append("\n---\n")
    return "\n".join(L)


def _render_audits(state: NovelState) -> str:
    L = [f"# Audit 历程（共 {len(state.audit_history)} 次）\n"]
    for v in state.audit_history:
        target = f"（第 {v.target_index} 章）" if v.target_index else ""
        status = "✅ 通过" if v.passed else "❌ 打回重写"
        avg = sum(r.score for r in v.reports) / len(v.reports)
        L.append(f"## {v.layer}{target} — {status}")
        L.append(f"*平均分 {avg:.2f}*\n")
        for r in v.reports:
            mark = "✓" if r.passed else "✗"
            L.append(f"### [{r.head}] {mark} score={r.score:.2f}")
            if r.issues:
                L.append("**问题**：")
                for i in r.issues:
                    L.append(f"- {i}")
            if r.suggestions:
                L.append("**建议**：")
                for s in r.suggestions:
                    L.append(f"- {s}")
            L.append("")
        if v.retry_hint:
            L.append(f"**打回反馈**：\n> {v.retry_hint}\n")
        L.append("---\n")
    return "\n".join(L)


def resolve_input_file(arg: str) -> Path:
    """接受绝对路径 / 相对路径 / inputs/ 下的文件名，统一解析到真实路径。"""
    p = Path(arg)
    if p.is_absolute() and p.exists():
        return p
    if p.exists():
        return p.resolve()
    cand = INPUTS_ROOT / arg
    if cand.exists():
        return cand
    cand2 = INPUTS_ROOT / f"{arg}.md"
    if cand2.exists():
        return cand2
    raise FileNotFoundError(f"找不到输入文件：{arg}（也不在 {INPUTS_ROOT}/ 下）")


def make_project_dir(premise: str) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    # 只用纯时间戳；premise 存在项目内 premise.txt 里方便识别
    pdir = PROJECTS_ROOT / ts
    (pdir / "queue").mkdir(parents=True, exist_ok=True)
    (pdir / "responses").mkdir(parents=True, exist_ok=True)
    OUTPUTS_ROOT.mkdir(exist_ok=True)
    (pdir / "premise.txt").write_text(premise, encoding="utf-8")
    return pdir


def export_top(state: NovelState, pdir: Path, markdown: str) -> Path:
    """把成品 MD 复制到顶层 outputs/，按小说标题命名，方便查找。"""
    OUTPUTS_ROOT.mkdir(exist_ok=True)
    title = state.l1.title if state.l1 else "untitled"
    safe_title = slugify(title, lowercase=False, separator="_") or "untitled"
    ts = pdir.name.split("_")[0]
    target = OUTPUTS_ROOT / f"{safe_title}_{ts}.md"
    target.write_text(markdown, encoding="utf-8")
    return target


def save_state(pdir: Path, state: NovelState) -> None:
    (pdir / "state.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )


def load_state(pdir: Path) -> NovelState:
    return NovelState.model_validate_json((pdir / "state.json").read_text(encoding="utf-8"))


def write_prompt(pdir: Path, step_id: str, prompt: str) -> Path:
    p = pdir / "queue" / f"{step_id}.prompt.md"
    p.write_text(prompt, encoding="utf-8")
    return p


def read_response(pdir: Path, step_id: str) -> dict | None:
    """读 Claude 写入的响应。返回 None 表示还没响应。"""
    p = pdir / "responses" / f"{step_id}.response.json"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None
    text = _strip_code_fence(text)
    return json.loads(text)


def _strip_code_fence(text: str) -> str:
    """容错：如果 Claude 不小心用了 ```json 包裹，剥掉。"""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    return text


def queue_pending(pdir: Path) -> list[str]:
    """返回 queue 里有 prompt 但没对应 response 的 step_id 列表。"""
    pending = []
    for q in sorted((pdir / "queue").glob("*.prompt.md")):
        step_id = q.stem.replace(".prompt", "")
        if not (pdir / "responses" / f"{step_id}.response.json").exists():
            pending.append(step_id)
    return pending


def export_markdown(state: NovelState) -> str:
    """组装最终的 Markdown 输出。"""
    if not state.l1 or not state.l3:
        return ""
    lines = [f"# {state.l1.title}", "", f"> {state.l1.logline}", ""]
    lines.append(f"**主题**：{state.l1.theme}\n")
    lines.append("---\n")
    for draft in sorted(state.l3, key=lambda d: d.index):
        l2 = next((c for c in state.l2 if c.index == draft.index), None)
        title = l2.title if l2 else f"第 {draft.index} 章"
        lines.append(f"## 第 {draft.index} 章 · {title}\n")
        lines.append(draft.content)
        lines.append("")
    return "\n".join(lines)

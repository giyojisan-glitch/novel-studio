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
    if state.l3:
        (adir / "05_slop_report.md").write_text(_render_slop(state), encoding="utf-8")
    if state.world_bible:
        (adir / "06_world_bible.md").write_text(_render_world_bible(state), encoding="utf-8")
    if state.scene_lists:
        (adir / "07_scene_lists.md").write_text(_render_scene_lists(state), encoding="utf-8")
    if state.scene_cards:
        (adir / "08_scene_cards.md").write_text(_render_scene_cards(state), encoding="utf-8")
    # V5: premise 视觉锚点兑现追踪（独立 artifact 便于人眼审核）
    if state.world_bible and state.world_bible.visual_anchors:
        (adir / "09_visual_anchors.md").write_text(_render_visual_anchors(state), encoding="utf-8")
    # V6: 叙事承诺账本（setup_ch / payoff_ch / fulfilled）
    if state.world_bible and state.world_bible.plot_promises:
        (adir / "10_plot_promises.md").write_text(_render_plot_promises(state), encoding="utf-8")
    return adir


def _render_visual_anchors(state: NovelState) -> str:
    """V5 premise 视觉锚点兑现状态。"""
    wb = state.world_bible
    L = ["# Visual Anchors · Premise 忠实度追踪（V5）\n"]
    L.append("> L1 从 premise 抽取的必保视觉画面。每条必须在某章**具体写出来**才算 fulfilled。\n")
    L.append("---\n")
    fulfilled = set(wb.fulfilled_anchors)
    for anchor in wb.visual_anchors:
        status = "✅ 已兑现" if anchor in fulfilled else "⏳ 未兑现"
        L.append(f"## {status}  —  {anchor}")
        L.append("")
    # Unfulfilled 汇总
    unfulfilled = [a for a in wb.visual_anchors if a not in fulfilled]
    if unfulfilled:
        L.append("---\n")
        L.append(f"## ⚠️ 未兑现（{len(unfulfilled)}/{len(wb.visual_anchors)}）")
        L.append("若 final_audit 跑完后本清单非空，engine 会强制 bounce 回 L3 重写。")
    else:
        L.append("---\n")
        L.append("## ✅ 全部兑现")
    L.append("")
    return "\n".join(L)


def _render_plot_promises(state: NovelState) -> str:
    """V6 叙事承诺账本 · 每条 promise 的 setup_ch / payoff_ch / fulfilled 追踪。"""
    wb = state.world_bible
    L = ["# Plot Promises · Premise 叙事承诺追踪（V6）\n"]
    L.append("> L1 从 premise 抽取的情节机巧（非视觉）。每条必须在某章**具体引爆**才算 fulfilled。\n")
    L.append("> 与 09_visual_anchors 区别：")
    L.append("> - 09 兜「视觉画面」（如「泥塑裂纹」）")
    L.append("> - 10 兜「叙事承诺」（如「埋下跨越十年的死子」）\n")
    L.append("---\n")
    for p in wb.plot_promises:
        if p.fulfilled:
            head = f"## ✅ {p.id} · {p.content}"
            meta = f"**setup @ ch{p.setup_ch}** → **payoff @ ch{p.payoff_ch}**"
        elif p.setup_ch > 0:
            head = f"## ⏳ {p.id} · {p.content}"
            meta = f"**setup @ ch{p.setup_ch}**（已埋） → payoff @ ch{p.payoff_ch or '?'}（未引爆）"
        else:
            head = f"## ⏸ {p.id} · {p.content}"
            meta = "**未埋**（setup_ch=0）"
        L.append(head)
        L.append(f"- {meta}")
        L.append("")
    unfulfilled = [p for p in wb.plot_promises if not p.fulfilled]
    if unfulfilled:
        L.append("---\n")
        L.append(f"## ⚠️ 未兑现（{len(unfulfilled)}/{len(wb.plot_promises)}）")
        L.append("若 final_audit 跑完后本清单非空，engine 会强制 bounce 回 L3 重写。")
    else:
        L.append("---\n")
        L.append("## ✅ 全部兑现")
    L.append("")
    return "\n".join(L)


def _render_scene_lists(state: NovelState) -> str:
    """V4 L2.5 产出：每章的场景设计列表（不含 L3 实际 prose）。"""
    L = ["# Scene Lists（V4 L2.5 章节场景分解）\n"]
    for csl in sorted(state.scene_lists, key=lambda x: x.chapter_index):
        l2 = next((c for c in state.l2 if c.index == csl.chapter_index), None)
        title = l2.title if l2 else f"第 {csl.chapter_index} 章"
        L.append(f"## 第 {csl.chapter_index} 章 · {title}  （revision={csl.revision}）\n")
        for i, sc in enumerate(csl.scenes, 1):
            L.append(f"### 场景 {sc.index}（目标 {sc.approximate_words} 字）")
            L.append(f"- **目的**：{sc.purpose}")
            L.append(f"- **开场落点**：{sc.opening_beat}")
            L.append(f"- **结尾落点**：{sc.closing_beat}")
            if sc.dominant_motifs:
                L.append(f"- **核心意象**：{'、'.join(sc.dominant_motifs)}")
            if sc.pov:
                L.append(f"- **视角**：{sc.pov}")
            if sc.time_marker:
                L.append(f"- **⏳ time_marker**（V5）：`{sc.time_marker}`")
            if getattr(sc, "technical_setup", ""):
                L.append(f"- **🔧 technical_setup**（V6）：{sc.technical_setup}")
            if getattr(sc, "technical_payoff", ""):
                L.append(f"- **🔧 technical_payoff**（V6）：{sc.technical_payoff}")
            L.append("")
        if csl.transition_notes:
            L.append("### 转场注记")
            for note in csl.transition_notes:
                L.append(f"- {note}")
            L.append("")
        L.append("---\n")
    return "\n".join(L)


def _render_scene_cards(state: NovelState) -> str:
    """V4 SceneCard：设计 + L3 实际 prose 首尾 200 字，供对照验证。"""
    L = ["# Scene Cards（V4 设计 vs 实际对照）\n"]
    L.append("> 每张卡片左侧是 L2.5 设计的 opening/closing beat，右侧是 L3 实际写出来的首/尾 200 字。")
    L.append("> 用于人眼检查：LLM 有没有按设计走？有没有跨场景承接？\n")
    L.append("---\n")

    by_ch: dict[int, list] = {}
    for card in state.scene_cards:
        by_ch.setdefault(card.chapter_index, []).append(card)

    for ch in sorted(by_ch.keys()):
        cards = sorted(by_ch[ch], key=lambda c: c.scene_index)
        L.append(f"## 第 {ch} 章 · {len(cards)} 个场景\n")
        for card in cards:
            o = card.outline
            L.append(f"### 场景 {card.scene_index}")
            L.append(f"**设计目的**：{o.purpose}")
            L.append(f"**设计开场**：{o.opening_beat}  →  **实际开场**：「{card.actual_opening}」")
            L.append(f"**设计结尾**：{o.closing_beat}  →  **实际结尾**：「{card.actual_closing}」")
            L.append(f"**字数**：目标 {o.approximate_words} / 实际 {card.actual_word_count}")
            L.append("")
        L.append("---\n")
    return "\n".join(L)


def _render_world_bible(state: NovelState) -> str:
    """V3 WorldBible 导出为人类可读 markdown。"""
    b = state.world_bible
    if b is None:
        return "（WorldBible 未初始化）\n"
    L = ["# World Bible（V3 跨章真相账本）\n"]
    L.append(f"**最后更新到第 {b.last_updated_ch} 章**\n")
    L.append("---\n")

    L.append("## 角色\n")
    if not b.characters:
        L.append("（无）\n")
    for c in b.characters:
        L.append(f"### {c.name}")
        if c.traits:
            L.append(f"- 特质：{'、'.join(c.traits)}")
        if getattr(c, "faction", ""):
            L.append(f"- 🏴 阵营（V6）：{c.faction}")
        if c.arc_state:
            L.append(f"- 弧光阶段：{c.arc_state}")
        if c.last_appeared_in:
            L.append(f"- 最后出场：第 {c.last_appeared_in} 章")
        if c.voice_markers:
            L.append(f"- 说话方式：{'/'.join(c.voice_markers)}")
        if c.notable_events:
            L.append("- 已发生事件：")
            for e in c.notable_events:
                L.append(f"  - {e}")
        L.append("")

    L.append("## 硬设定（WorldFact）\n")
    if not b.facts:
        L.append("（无）\n")
    by_cat: dict[str, list] = {}
    for f in b.facts:
        by_cat.setdefault(f.category, []).append(f)
    cat_names = {"rule": "规则", "location": "场所", "item": "物件",
                 "relationship": "关系", "event": "事件"}
    for cat, items in by_cat.items():
        L.append(f"### {cat_names.get(cat, cat)}")
        for f in items:
            L.append(f"- {f.content}  *（第 {f.ch_introduced} 章引入）*")
        L.append("")

    L.append("## 伏笔账本\n")
    L.append("### 未兑现（active）")
    if b.active_foreshadow:
        for fs in b.active_foreshadow:
            L.append(f"- {fs}")
    else:
        L.append("（无）")
    L.append("\n### 已兑现（paid）")
    if b.paid_foreshadow:
        for fs in b.paid_foreshadow:
            L.append(f"- {fs}")
    else:
        L.append("（无）")
    L.append("")

    L.append("## 大事时间线\n")
    if b.timeline:
        for i, e in enumerate(b.timeline, 1):
            L.append(f"{i}. {e}")
    else:
        L.append("（无）")
    L.append("")

    # V5: 视觉锚点兑现追踪
    if b.visual_anchors:
        L.append("## V5 · Visual Anchors（premise 必保视觉）")
        fulfilled = set(b.fulfilled_anchors)
        for a in b.visual_anchors:
            marker = "✅" if a in fulfilled else "⏳"
            L.append(f"- {marker} {a}")
        L.append("")

    # V5: tracked_objects 当前状态
    if b.tracked_objects:
        L.append("## V5 · 被追踪物件（跨章状态机）")
        for obj in b.tracked_objects:
            L.append(f"### {obj.name}")
            L.append(f"- **当前状态**：{obj.current_state}")
            L.append(f"- **最后变更**：第 {obj.last_changed_ch} 章")
            if obj.state_history:
                L.append("- **历史**：")
                for h in obj.state_history:
                    L.append(f"  - {h}")
            L.append("")

    # V5: time_markers_used（全书时间进度条）
    if b.time_markers_used:
        L.append("## V5 · 全书 Time Markers（按章按场景顺序）")
        L.append(" → ".join(f"「{m}」" for m in b.time_markers_used))
        L.append("")

    # V6: plot_promises 账本
    if b.plot_promises:
        L.append("## V6 · Plot Promises（叙事承诺账本）")
        for p in b.plot_promises:
            if p.fulfilled:
                marker = f"✅ setup@ch{p.setup_ch} → payoff@ch{p.payoff_ch}"
            elif p.setup_ch > 0:
                marker = f"⏳ setup@ch{p.setup_ch}（已埋，未引爆）"
            else:
                marker = "⏸ 未埋"
            L.append(f"- **{p.id}** · {p.content}  [{marker}]")
        L.append("")

    return "\n".join(L)


def _render_slop(state: NovelState) -> str:
    """逐章跑 slop 扫描并合并成一份报告。feed-forward 模式：只报告不阻塞。"""
    from .slop_check import scan

    L = ["# Slop Report（机械检测 · 不调 LLM）\n"]
    L.append("> 灵感来自 autonovel/ANTI-SLOP.md，完全中文化。")
    L.append("> 0-2 干净，2-4 轻度 AI 味，4-6.5 中度，6.5+ 重度。\n")
    L.append("## 分章节扫描\n")

    all_scores = []
    for d in sorted(state.l3, key=lambda x: x.index):
        l2 = next((c for c in state.l2 if c.index == d.index), None)
        title = l2.title if l2 else f"第 {d.index} 章"
        report = scan(d.content)
        all_scores.append(report.score)
        verdict = (
            "✓ 干净" if report.score < 2.0
            else "⚠ 轻度" if report.score < 4.0
            else "⚠ 中度" if report.score < 6.5
            else "✗ 重度"
        )
        L.append(f"### 第 {d.index} 章 · {title}  —  **{report.score:.2f} / 10  {verdict}**")
        L.append(f"*{report.stats['chinese_chars']} 字 · {report.stats['paragraphs']} 段 · "
                 f"{report.stats['sentences']} 句*\n")
        if not report.hits:
            L.append("（无命中）\n")
        else:
            top = report.hits[:8]
            L.append("Top 命中：")
            for h in top:
                L.append(f"- **[{h.category}]** {h.detail}" +
                         (f" ×{h.count}" if h.count > 1 else "") +
                         f"  *(+{h.points:.1f})*")
            if len(report.hits) > 8:
                L.append(f"- *（还有 {len(report.hits) - 8} 条）*")
            L.append("")
        L.append("---\n")

    if all_scores:
        avg = sum(all_scores) / len(all_scores)
        L.insert(3, f"**全书平均 Slop Score: {avg:.2f} / 10.0**\n")
    return "\n".join(L)


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
    """读 Claude 写入的响应。返回 None 表示还没响应。

    JSON 解析失败时：raise JSONDecodeError 并在 msg 里带上**具体文件路径 + 行列号**，
    方便定位（benchmark 跑多篇时某一篇 response 有非法引号导致解析失败，
    默认的 `Expecting ',' delimiter: line 1 col 241` 不指哪篇会很难查）。
    """
    p = pdir / "responses" / f"{step_id}.response.json"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 带上文件路径 + 行列号重抛，方便 debug
        raise json.JSONDecodeError(
            f"{e.msg} (file: {p}, line {e.lineno} col {e.colno})",
            e.doc,
            e.pos,
        ) from e


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

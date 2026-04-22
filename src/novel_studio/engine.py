"""Step engine：多层状态机推进的核心逻辑。

step 推进规则：
1. load state，根据 state.next_step 算出 _expected_prompts(next_step)
2. 若任一 prompt 还没 dump → dump 之，return waiting
3. 若有响应缺失 → return waiting
4. 全部响应到齐 → apply 到 state、聚合 audit、推进 next_step、立刻 dump 下一组

V2 Pipeline (user_input.pipeline_version == "v2"):
  L1 → L1_audit → L2_1..N → L2_N_audit
       → L3_1..N → L3_N_audit
       → final_audit
         ├─ usable=True  → L4_adversarial_1 → L4_scrubber_1 → ... → L4_scrubber_N → finalize
         └─ usable=False → bounce to suspect_layer（重跑那一层）
  finalize 用 L4 内容（而不是 L3）组装 MD

V3 Pipeline (pipeline_version == "v3"，长篇专用):
  L1 → L1_audit → bible_init (synthetic)
       → 对每章 i in 1..N 交替进行：
           L2_i → L2_i_audit → L3_i → L3_i_audit → bible_update_i
       → final_audit → L4_adversarial → L4_scrubber → finalize
  特点：
    - interleaved L2/L3（每章出梗概立即写正文，L2_{i+1} 能看到 L3_i 实际文本）
    - bible_update_i 增量维护 WorldBible（角色状态 / 硬设定 / 伏笔流转）
    - L2/L3 prompt 注入 bible context → 跨章一致性

V1 Pipeline（默认，向后兼容）：
  L1 → L1_audit → L2_* → L3_* → finalize（L4 透传）
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
    AdversarialCut,
    FinalVerdict,
    BibleUpdate,
    ChapterSceneList,
    L3SceneDraft,
    SceneCard,
    TrackedObject,
)
from . import prompts as P
from .audit import aggregate, should_force_pass, MAX_REVISION
from .llm import BaseProvider, HumanQueueProvider
from .utils import (
    write_prompt,
    read_response,
    save_state,
    export_markdown,
    export_top,
    export_artifacts,
)
from .slop_check import scan as slop_scan


# Default provider used by advance() when caller doesn't inject one.
# Kept as HumanQueue for backward compat.
DEFAULT_PROVIDER: BaseProvider = HumanQueueProvider()


# ====== expected prompts mapping ======


def expected_prompts(next_step: str) -> list[str]:
    """next_step → 该步需要哪些 prompt 文件。audit 步有 2 或 3 个 head 并行；final_audit / L4_* / bible_update_* / L25_{i} / L3_{i}_{s} 是单个 prompt。"""
    if next_step in ("finalize", "DONE", "bible_init"):
        return []                                    # bible_init 为纯合成（无 LLM）
    if next_step == "final_audit":
        return ["final_audit"]
    if next_step.startswith(("L4_adversarial_", "L4_scrubber_", "bible_update_")):
        return [next_step]
    # V4: L3_{i}_chapter_audit → 3 头（logic + pace + continuity）
    if next_step.endswith("_chapter_audit"):
        base = next_step[:-len("_chapter_audit")]
        return [
            f"{base}_chapter_audit_logic",
            f"{base}_chapter_audit_pace",
            f"{base}_chapter_audit_continuity",
        ]
    if next_step.endswith("_audit"):
        base = next_step[:-len("_audit")]
        return [f"{base}_audit_logic", f"{base}_audit_pace"]
    return [next_step]


def build_prompt(state: NovelState, step_id: str) -> str:
    """根据 step_id 构造对应的 prompt 文本。"""
    if step_id == "L1":
        return P.l1_prompt(state)
    parts = step_id.split("_")
    # V4: L25_{i}（章节场景列表）— 要比 L2_ 早判断，避免「L25 也以 L2 开头」
    if step_id.startswith("L25_") and not _is_audit_step(step_id):
        if len(parts) == 2 and parts[1].isdigit():
            return P.l25_prompt(state, int(parts[1]))
    if step_id.startswith("L2_") and not _is_audit_step(step_id):
        if len(parts) == 2 and parts[1].isdigit():
            return P.l2_prompt(state, int(parts[1]))
    # V4: L3_{i}_{s}（单场景正文）— 3 部分全是数字
    if (step_id.startswith("L3_")
        and not _is_audit_step(step_id)
        and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit()):
        return P.l3_scene_prompt(state, int(parts[1]), int(parts[2]))
    # V1/V2/V3: L3_{i}（章节级正文）— 严格 2 部分
    if step_id.startswith("L3_") and not _is_audit_step(step_id):
        if len(parts) == 2 and parts[1].isdigit():
            return P.l3_prompt(state, int(parts[1]))
    if _is_audit_step(step_id):
        head = _audit_head_of(step_id)
        layer, idx = _parse_audit_target(step_id)
        # V4 continuity 头有专门 prompt
        if head == "continuity":
            return P.continuity_audit_prompt(state, idx)
        return P.audit_prompt(state, layer, idx, head)
    if step_id == "final_audit":
        full_md = export_markdown(state)
        slop_avg = _compute_slop_avg(state)
        return P.final_audit_prompt(state, full_md, slop_avg)
    if step_id.startswith("L4_adversarial_"):
        idx = int(step_id.split("_")[2])
        l3 = next(d for d in state.l3 if d.index == idx)
        # 砍 15%，最少 80 字
        cut_target = max(80, int(l3.word_count * 0.15))
        return P.adversarial_edit_prompt(state, idx, cut_target)
    if step_id.startswith("L4_scrubber_"):
        idx = int(step_id.split("_")[2])
        return P.scrubber_prompt(state, idx)
    if step_id.startswith("bible_update_"):
        idx = int(step_id.split("_")[2])
        return P.bible_update_prompt(state, idx)
    raise ValueError(f"unknown step_id: {step_id}")


def _is_audit_step(step_id: str) -> bool:
    return step_id.endswith(("_audit_logic", "_audit_pace", "_audit_continuity"))


def _audit_head_of(step_id: str) -> str:
    if step_id.endswith("_audit_logic"):
        return "logic"
    if step_id.endswith("_audit_pace"):
        return "pace"
    if step_id.endswith("_audit_continuity"):
        return "continuity"
    raise ValueError(f"not an audit step: {step_id}")


def _compute_slop_avg(state: NovelState) -> float:
    """跑 slop 扫描对所有 L3 章节，返回平均分。供 final_audit 参考。"""
    if not state.l3:
        return 0.0
    scores = [slop_scan(d.content).score for d in state.l3]
    return sum(scores) / len(scores)


# ====== 应用响应 ======


def apply_responses(
    state: NovelState,
    step_ids: list[str],
    pdir: Path,
    provider: BaseProvider | None = None,
) -> None:
    """把响应 JSON 解析并写入 state 对应字段。"""
    provider = provider or DEFAULT_PROVIDER

    def _fetch(sid: str):
        return provider.query(sid, pdir).data

    if len(step_ids) == 1:
        sid = step_ids[0]
        data = _fetch(sid)
        parts = sid.split("_")
        if sid == "L1":
            state.l1 = L1Skeleton(**_coerce_dict(data, expected_index=None))
        elif sid.startswith("L25_") and len(parts) == 2 and parts[1].isdigit():
            # V4: L25_{i} → ChapterSceneList
            idx = int(parts[1])
            csl = ChapterSceneList(**_coerce_dict(data, expected_index=None))
            csl.chapter_index = idx
            _upsert_scene_list(state, csl)
            # 同时播种 SceneCards（设计部分，actual_* 留给 L3 写完后填）
            for outline in csl.scenes:
                _upsert_scene_card(state, SceneCard(
                    chapter_index=idx, scene_index=outline.index, outline=outline,
                ))
            # V5: 把本章 time_markers append 到 bible.time_markers_used（按场景顺序）
            if state.world_bible is not None:
                new_markers = [s.time_marker for s in csl.scenes if s.time_marker]
                if new_markers:
                    state.world_bible.time_markers_used = (
                        list(state.world_bible.time_markers_used) + new_markers
                    )
        elif sid.startswith("L2_") and len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1])
            outline = L2ChapterOutline(**_coerce_dict(data, expected_index=idx))
            _upsert_l2(state, outline)
        elif (sid.startswith("L3_") and len(parts) == 3
              and parts[1].isdigit() and parts[2].isdigit()):
            # V4: L3_{i}_{s} → L3SceneDraft + update SceneCard.actual_*
            i, s = int(parts[1]), int(parts[2])
            sd_data = _coerce_dict(data, expected_index=None)
            sd = L3SceneDraft(**sd_data)
            sd.chapter_index = i
            sd.scene_index = s
            _upsert_l3_scene(state, sd)
            _fill_scene_card_actuals(state, i, s, sd.content)
            # 每次场景写完就同步到 state.l3（最新的拼接），章节级 audit / final / L4 沿用
            _sync_l3_from_scenes(state, i)
        elif sid.startswith("L3_") and len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1])
            draft = L3ChapterDraft(**_coerce_dict(data, expected_index=idx))
            _upsert_l3(state, draft)
        elif sid == "final_audit":
            state.final_verdict = FinalVerdict(**_coerce_dict(data, expected_index=None))
            # V5: unfulfilled_anchors 非空 → 强制 usable=False 和 suspect=L3
            # （LLM 可能错误标 usable=True 却漏掉 visual_anchor 兑现检查）
            if state.final_verdict.unfulfilled_anchors:
                state.final_verdict.usable = False
                if state.final_verdict.suspect_layer == "none":
                    state.final_verdict.suspect_layer = "L3"
                # 把未兑现锚点明确写进 retry_hint
                unfulfilled_str = "；".join(state.final_verdict.unfulfilled_anchors)
                addendum = f"[V5 视觉锚点未兑现] {unfulfilled_str}"
                if addendum not in state.final_verdict.retry_hint:
                    state.final_verdict.retry_hint = (
                        state.final_verdict.retry_hint + "\n" + addendum
                        if state.final_verdict.retry_hint else addendum
                    )
        elif sid.startswith("L4_adversarial_"):
            idx = int(sid.split("_")[2])
            # L4_adversarial 本身就预期 list（AdversarialCut 数组）
            if isinstance(data, dict) and "cuts" in data:
                data = data["cuts"]
            cuts = [AdversarialCut(**c) for c in (data if isinstance(data, list) else [])]
            _upsert_l4_cuts(state, idx, cuts)
        elif sid.startswith("L4_scrubber_"):
            idx = int(sid.split("_")[2])
            polished = L4PolishedChapter(**_coerce_dict(data, expected_index=idx))
            polished.index = idx
            _upsert_l4_polished(state, polished)
        elif sid.startswith("bible_update_"):
            idx = int(sid.split("_")[2])
            # BibleUpdate 字段是 chapter_index（不是 index），用 None 跳过索引校验
            update = BibleUpdate(**_coerce_dict(data, expected_index=None))
            update.chapter_index = idx                          # 强制对齐，防 LLM 写错
            _apply_bible_update_to_state(state, update)
    else:
        # audit 一组：2 头 → 聚合
        reports = [AuditReport(**_fetch(sid)) for sid in step_ids]
        layer, idx = _parse_audit_target(step_ids[0])
        verdict = aggregate(layer, idx, reports)
        state.audit_history.append(verdict)


def _looks_like_schema_envelope(data: dict) -> bool:
    """识别"LLM 把自己的 JSON Schema 当成 response shape 返回"这个典型 bug。

    症状（真实 Doubao final_audit 观察到）：
        {
          "type": "object",
          "properties": {
            "usable": {"type": "boolean", "value": false},
            "overall_score": {"type": "number", "value": 0.3},
            ...
          },
          "required": ["usable", "overall_score"]
        }
    正确的 shape 应该是 `{"usable": false, "overall_score": 0.3, ...}`。
    """
    return (
        isinstance(data, dict)
        and data.get("type") == "object"
        and isinstance(data.get("properties"), dict)
        and all(
            isinstance(v, dict) and ("value" in v or "default" in v)
            for v in data["properties"].values()
        )
    )


def _unwrap_schema_envelope(data: dict) -> dict:
    """把 {type/properties/required} 壳展平成 {field: value}。"""
    out: dict = {}
    for k, meta in data["properties"].items():
        if not isinstance(meta, dict):
            continue
        if "value" in meta:
            out[k] = meta["value"]
        elif "default" in meta:
            out[k] = meta["default"]
    return out


def _coerce_dict(data, expected_index: int | None) -> dict:
    """防御性容错：LLM 有时把单章 prompt 理解成"给我所有章节的列表"，返回 list 而不是 dict。
    有时（Doubao 特别明显）把 JSON Schema 壳当响应返回，真数据藏在 properties.*.value。

    策略：
    - JSON Schema 壳 → 剥壳
    - data 已是 dict：直接返回
    - data 是 list：找 index 匹配的那项；没有就取第一个 dict
    - 其他：raise
    """
    if isinstance(data, dict) and _looks_like_schema_envelope(data):
        return _unwrap_schema_envelope(data)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        if not data:
            raise ValueError("LLM 返回空 list")
        if expected_index is not None:
            for item in data:
                if isinstance(item, dict) and item.get("index") == expected_index:
                    return item
        for item in data:
            if isinstance(item, dict):
                return item
        raise ValueError(f"list 里找不到 dict 项: {data}")
    raise ValueError(f"无法处理的响应类型 {type(data).__name__}: {data}")


def _upsert_l2(state: NovelState, outline: L2ChapterOutline) -> None:
    state.l2 = [c for c in state.l2 if c.index != outline.index] + [outline]
    state.l2.sort(key=lambda c: c.index)


def _upsert_l3(state: NovelState, draft: L3ChapterDraft) -> None:
    state.l3 = [d for d in state.l3 if d.index != draft.index] + [draft]
    state.l3.sort(key=lambda d: d.index)


def _upsert_l4_cuts(state: NovelState, idx: int, cuts: list[AdversarialCut]) -> None:
    """把 adversarial 切割结果附到对应的 L4 条目上。如果 L4 还没有该 index，新建。"""
    existing = next((p for p in state.l4 if p.index == idx), None)
    if existing:
        existing.adversarial_cuts = cuts
    else:
        state.l4.append(L4PolishedChapter(index=idx, adversarial_cuts=cuts))
    state.l4.sort(key=lambda p: p.index)


def _upsert_l4_polished(state: NovelState, polished: L4PolishedChapter) -> None:
    """Scrubber 输出：替换或新建 L4 条目，保留 adversarial_cuts。"""
    existing = next((p for p in state.l4 if p.index == polished.index), None)
    if existing:
        # scrubber 输出的 adversarial_cuts 应该抄自输入；如果没抄我们保留原来的
        polished.adversarial_cuts = polished.adversarial_cuts or existing.adversarial_cuts
    state.l4 = [p for p in state.l4 if p.index != polished.index] + [polished]
    state.l4.sort(key=lambda p: p.index)


def _upsert_scene_list(state: NovelState, csl: ChapterSceneList) -> None:
    state.scene_lists = [x for x in state.scene_lists if x.chapter_index != csl.chapter_index] + [csl]
    state.scene_lists.sort(key=lambda x: x.chapter_index)


def _upsert_scene_card(state: NovelState, card: SceneCard) -> None:
    state.scene_cards = [
        c for c in state.scene_cards
        if not (c.chapter_index == card.chapter_index and c.scene_index == card.scene_index)
    ] + [card]
    state.scene_cards.sort(key=lambda c: (c.chapter_index, c.scene_index))


def _upsert_l3_scene(state: NovelState, sd: L3SceneDraft) -> None:
    state.l3_scenes = [
        s for s in state.l3_scenes
        if not (s.chapter_index == sd.chapter_index and s.scene_index == sd.scene_index)
    ] + [sd]
    state.l3_scenes.sort(key=lambda s: (s.chapter_index, s.scene_index))


def _fill_scene_card_actuals(state: NovelState, chapter_idx: int, scene_idx: int, content: str) -> None:
    """L3 写完某场景后，把其首/尾 200 字填到对应 SceneCard。"""
    for card in state.scene_cards:
        if card.chapter_index == chapter_idx and card.scene_index == scene_idx:
            card.actual_opening = content[:200]
            card.actual_closing = content[-200:]
            card.actual_word_count = len(content)
            return


def _sync_l3_from_scenes(state: NovelState, chapter_idx: int) -> None:
    """V4: 把同章节所有 L3SceneDraft 拼接成 L3ChapterDraft，upsert 到 state.l3。

    这样 chapter_audit / final_audit / L4 仍然按章节粒度工作，无需区分 v3/v4。
    """
    scenes = sorted(
        [s for s in state.l3_scenes if s.chapter_index == chapter_idx],
        key=lambda s: s.scene_index,
    )
    if not scenes:
        return
    content = "\n\n".join(s.content for s in scenes)
    word_count = sum(s.word_count for s in scenes)
    max_rev = max(s.revision for s in scenes)
    draft = L3ChapterDraft(
        index=chapter_idx, content=content, word_count=word_count, revision=max_rev,
    )
    _upsert_l3(state, draft)


def _apply_bible_update_to_state(state: NovelState, update: BibleUpdate) -> None:
    """合并 BibleUpdate 到 state.world_bible。v3 专用。"""
    from .state import WorldBible
    current = state.world_bible or WorldBible()
    state.world_bible = P.apply_bible_update(current, update)
    state.current_bible_update_idx = max(state.current_bible_update_idx, update.chapter_index)


def _parse_audit_target(audit_step_id: str) -> tuple[str, int | None]:
    """解析 audit step_id 到 (layer, target_index)。

    支持形式：
      L1_audit_{head}                    → ("L1", None)
      L2_{i}_audit_{head}                → ("L2", i)
      L25_{i}_audit_{head}               → ("L25", i)
      L3_{i}_audit_{head}                → ("L3", i)        [V1/V2/V3 章节级]
      L3_{i}_chapter_audit_{head}        → ("L3", i)        [V4 章节级，三头包含 continuity]
    """
    body = audit_step_id.rsplit("_audit_", 1)[0]
    if body == "L1":
        return "L1", None
    # V4: 剥掉 "_chapter" 后缀（如果是章节级审）
    if body.endswith("_chapter"):
        body = body[:-len("_chapter")]
    layer, idx_s = body.split("_", 1)
    return layer, int(idx_s)


# ====== 决定下一步 ======


def _scenes_in_chapter(state: NovelState, chapter_idx: int) -> int:
    """V4：某章 L2.5 规划了多少个场景。"""
    csl = next((x for x in state.scene_lists if x.chapter_index == chapter_idx), None)
    return len(csl.scenes) if csl else 0


def decide_next(state: NovelState) -> str:
    """根据 state 当前状况返回下一个 step。"""
    cur = state.next_step
    total = state.user_input.chapter_count
    pv = state.user_input.pipeline_version
    v2 = pv == "v2"
    v3 = pv == "v3"
    v4 = pv in ("v4", "v5")              # V5 沿用 V4 路由（+ V5 新增 prompt 注入而已）
    use_final_audit = v2 or v3 or v4      # v4/v5 沿用 v2 的成品审 + L4 润色
    use_bible = v3 or v4                   # v3/v4/v5 共用世界观 bible

    # ---- 合成步完成 ----
    if cur == "bible_init":
        return "L2_1"

    # bible_update_{i} 完成 → 交替：下一章 L2_{i+1}，或最后一章后的 final_audit
    if cur.startswith("bible_update_"):
        idx = int(cur.split("_")[2])
        if idx < total:
            return f"L2_{idx + 1}"
        return "final_audit" if use_final_audit else "finalize"

    # ---- V2/V3/V4 共用：final_audit / L4 分支 ----
    if cur == "final_audit":
        fv = state.final_verdict
        if fv and fv.usable:
            return "L4_adversarial_1"
        return _bounce_back(state, fv)

    if cur.startswith("L4_adversarial_"):
        idx = int(cur.split("_")[2])
        return f"L4_scrubber_{idx}"
    if cur.startswith("L4_scrubber_"):
        idx = int(cur.split("_")[2])
        if idx < total:
            return f"L4_adversarial_{idx + 1}"
        return "finalize"

    parts = cur.split("_")

    # ---- V4: L3_{i}_{s} 单场景完成 → 下一场景 / 章节级 audit ----
    if (cur.startswith("L3_")
        and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit()):
        i, s = int(parts[1]), int(parts[2])
        m = _scenes_in_chapter(state, i)
        if s < m:
            return f"L3_{i}_{s + 1}"
        return f"L3_{i}_chapter_audit"

    # ---- V4: L25_{i} / L25_{i}_audit ----
    if cur.startswith("L25_") and len(parts) == 2 and parts[1].isdigit():
        # L25_i 刚产出 → 进 audit
        return f"{cur}_audit"

    # ---- 主 prompt 完成 → 进 audit（L1/L2/L3 章节级） ----
    if cur == "L1":
        return "L1_audit"
    if cur.startswith("L2_") and len(parts) == 2 and parts[1].isdigit():
        return cur + "_audit"
    if cur.startswith("L3_") and len(parts) == 2 and parts[1].isdigit():
        return cur + "_audit"

    # ---- audit 完成 → 看 verdict ----
    # V4 章节级 audit：形式 L3_{i}_chapter_audit
    if cur.endswith("_chapter_audit"):
        body = cur[:-len("_chapter_audit")]  # "L3_{i}"
        verdict = state.audit_history[-1]
        layer, idx_s = body.split("_", 1)
        idx = int(idx_s)
        # 用本章的 last scene revision 作为重写计数
        scenes = [s for s in state.l3_scenes if s.chapter_index == idx]
        current_rev = max((s.revision for s in scenes), default=0)
        if verdict.passed or should_force_pass(current_rev):
            # V4 沿用 v3 bible_update
            return f"bible_update_{idx}" if use_bible else (
                "final_audit" if (idx == total and use_final_audit)
                else f"L2_{idx + 1}" if idx < total else "finalize"
            )
        # 重写整章所有场景：revision++，回到场景 1
        for s in scenes:
            s.revision += 1
        return f"L3_{idx}_1"

    if cur.endswith("_audit"):
        body = cur[:-len("_audit")]
        verdict = state.audit_history[-1]

        if body == "L1":
            current_rev = state.l1.revision
            if verdict.passed or should_force_pass(current_rev):
                # v3/v4：先合成 bible_init，再进 L2_1
                return "bible_init" if use_bible else "L2_1"
            state.l1.revision += 1
            return "L1"

        layer, idx_s = body.split("_", 1)
        idx = int(idx_s)
        if layer == "L25":
            cur_csl = next(x for x in state.scene_lists if x.chapter_index == idx)
            current_rev = cur_csl.revision
            if verdict.passed or should_force_pass(current_rev):
                return f"L3_{idx}_1"           # 开始写本章第 1 场景
            cur_csl.revision += 1
            return f"L25_{idx}"
        if layer == "L2":
            cur_outline = next(c for c in state.l2 if c.index == idx)
            current_rev = cur_outline.revision
            if verdict.passed or should_force_pass(current_rev):
                # v4: L2_i 过了 → L25_i（场景分解）
                if v4:
                    return f"L25_{idx}"
                # v3: interleaved —— L2_i 过了立即写 L3_i
                if v3:
                    return f"L3_{idx}"
                # v1/v2: 全部 L2 先出，再进 L3
                if idx < total:
                    return f"L2_{idx + 1}"
                return "L3_1"
            cur_outline.revision += 1
            return f"L2_{idx}"
        if layer == "L3":
            cur_draft = next(d for d in state.l3 if d.index == idx)
            current_rev = cur_draft.revision
            if verdict.passed or should_force_pass(current_rev):
                # v3: 每章写完跑 bible_update 再决定下一步
                if v3:
                    return f"bible_update_{idx}"
                if idx < total:
                    return f"L3_{idx + 1}"
                # 最后一章 L3 过了：v1 直接 finalize，v2 进 final_audit
                return "final_audit" if v2 else "finalize"
            cur_draft.revision += 1
            return f"L3_{idx}"

    if cur == "finalize":
        return "DONE"

    raise ValueError(f"unknown next_step: {cur}")


MAX_FINAL_BOUNCES = 2  # 超过就强制放行，避免 LLM 重复给相同 retry_hint 的死循环


def _bounce_back(state: NovelState, fv: FinalVerdict | None) -> str:
    """Final audit usable=False 时，退回到 suspect_layer。

    规则：
    - suspect=premise：无法自动修（用户要改输入）。设为 DONE（强制放行）并 trace 标记
    - suspect=L1：清空 L2/L3，重跑 L1
    - suspect=L2：清空 L3，从 L2_1 重跑
    - suspect=L3：从 L3_1 重跑（L2 保留）
    - suspect=L4 或 none：留在 final_audit，下一轮 final_audit
    - **bounce 计数器**：每次进这里 +1，>= MAX_FINAL_BOUNCES 强制放行（避免 LLM 每次都
      给同样的 retry_hint 导致无限循环）
    """
    if fv is None:
        return "DONE"
    state.final_bounce_count += 1
    pv = state.user_input.pipeline_version
    has_l4 = pv in ("v2", "v3", "v4", "v5")
    if state.final_bounce_count > MAX_FINAL_BOUNCES:
        state.trace.append({
            "bounce": "force_pass_max_reached",
            "count": state.final_bounce_count,
            "hint": fv.retry_hint,
        })
        return "L4_adversarial_1" if has_l4 else "finalize"
    v3 = pv == "v3"
    v4 = pv in ("v4", "v5")            # V5 沿用 V4 的 bounce 清理逻辑
    use_bible = v3 or v4
    s = fv.suspect_layer
    if s == "premise":
        state.trace.append({"bounce": "premise_unfixable", "hint": fv.retry_hint})
        return "DONE"
    if s == "L1":
        state.trace.append({"bounce": "to_L1", "hint": fv.retry_hint})
        state.l2.clear()
        state.l3.clear()
        state.l4.clear()
        if v4:
            state.scene_lists.clear()
            state.l3_scenes.clear()
            state.scene_cards.clear()
        if use_bible:
            # V3/V4: L1 要重写，bible 要从头重建（下一轮 bible_init 会做）
            state.world_bible = None
            state.current_bible_update_idx = 0
        if state.l1:
            state.l1.revision += 1
        return "L1"
    if s == "L2":
        state.trace.append({"bounce": "to_L2_1", "hint": fv.retry_hint})
        state.l3.clear()
        state.l4.clear()
        if v4:
            state.scene_lists.clear()
            state.l3_scenes.clear()
            state.scene_cards.clear()
        if use_bible and state.l1:
            # V3/V4: L2 重写 → bible 重置到 post-L1 状态（抛掉后续 drifted facts）
            state.world_bible = P.build_initial_bible(state.l1)
            state.current_bible_update_idx = 0
        for c in state.l2:
            c.revision += 1
        return "L2_1"
    if s == "L3":
        state.trace.append({"bounce": "to_L3_1", "hint": fv.retry_hint})
        state.l4.clear()
        if v4:
            # V4: L3 场景全部清掉（scene_lists 保留，因为 L2.5 没被 suspect）
            for sd in state.l3_scenes:
                sd.revision += 1
            state.l3_scenes.clear()
            # scene_cards actual_* 重置（保留 outline 部分）
            for card in state.scene_cards:
                card.actual_opening = ""
                card.actual_closing = ""
                card.actual_word_count = 0
        if use_bible and state.l1:
            # V3/V4: L3 重写 → bible 重置（否则旧版 drifted facts 会污染 retry 的 L3 prompt）
            state.world_bible = P.build_initial_bible(state.l1)
            state.current_bible_update_idx = 0
        for d in state.l3:
            d.revision += 1
        if v4:
            return "L3_1_1"          # V4: 从第 1 章第 1 场景重写
        return "L3_1"
    # L4 / none：没法有意义地退（L4 还没跑），直接放行
    state.trace.append({"bounce": "force_pass", "hint": fv.retry_hint})
    return "L4_adversarial_1" if has_l4 else "finalize"


# ====== 重试时清理对应 queue/response ======


def reset_step_files(pdir: Path, step_ids: list[str]) -> None:
    """删除给定 step 的 prompt 和 response 文件，让其重新 dump/响应。"""
    for sid in step_ids:
        for sub, suffix in (("queue", ".prompt.md"), ("responses", ".response.json")):
            p = pdir / sub / f"{sid}{suffix}"
            if p.exists():
                p.unlink()


# ====== 主 advance ======


def advance(
    state: NovelState,
    pdir: Path,
    provider: BaseProvider | None = None,
) -> dict:
    """单步推进。返回状态字典：{status, step_ids, next_step, ...}

    provider: LLM provider 抽象。默认 HumanQueueProvider（文件驱动，需要人响应）。
              传入 StubProvider 可跑 smoke test；传入 AnthropicProvider 全自动。
    """
    provider = provider or DEFAULT_PROVIDER
    # 同步 creativity 档位到 provider（strict/balanced/creative → 温度 / prompt 头）
    provider.creativity = state.user_input.creativity

    if state.next_step == "finalize":
        return _do_finalize(state, pdir)

    if state.next_step == "DONE":
        return {"status": "completed", "output": "（已完成）"}

    if state.next_step == "bible_init":
        return _do_bible_init(state, pdir, provider)

    expected = expected_prompts(state.next_step)

    # 1) 发起还没 request 过的 step（对 Anthropic 会真调 API）
    dispatched = []
    for sid in expected:
        result = provider.query(sid, pdir)
        if result.ready:
            continue
        if provider.has_pending_request(sid, pdir):
            continue  # 已发请求，等响应（人在环路场景）
        # 没发：dispatch（同步 provider 会原地完成）
        text = build_prompt(state, sid)
        provider.request(sid, text, pdir)
        dispatched.append(sid)

    if dispatched:
        save_state(pdir, state)
        # 对 Human provider：dispatched 表示 "prompt 已 dump，等人响应"
        # 对 Anthropic/Stub：已经拿到响应了（同步），继续往下走到 step 2
        # 所以 re-query 看看
        still_pending = [
            sid for sid in expected
            if not provider.query(sid, pdir).ready
        ]
        if still_pending:
            return {
                "status": "dumped",
                "step_ids": still_pending,
                "next_step": state.next_step,
            }

    # 2) 是否所有响应到齐
    results = {sid: provider.query(sid, pdir) for sid in expected}
    missing_resp = [sid for sid, r in results.items() if not r.ready]
    if missing_resp:
        return {
            "status": "waiting",
            "step_ids": missing_resp,
            "next_step": state.next_step,
        }

    # 3) 全到齐 → apply、决定下一步
    apply_responses(state, expected, pdir, provider=provider)
    state.trace.append({"step": state.next_step, "applied": expected})
    export_artifacts(state, pdir)
    new_next = decide_next(state)

    # 重试：清理要重跑的 step 的旧文件
    if _is_retry(state.next_step, new_next):
        _cleanup_retry_files(pdir, state, new_next)

    state.next_step = new_next
    save_state(pdir, state)

    # 立即 dispatch 下一步（如果不是终态）
    if new_next not in ("finalize", "DONE"):
        next_expected = expected_prompts(new_next)
        for sid in next_expected:
            result = provider.query(sid, pdir)
            if result.ready:
                continue
            if provider.has_pending_request(sid, pdir):
                continue
            text = build_prompt(state, sid)
            provider.request(sid, text, pdir)
        # 同步 provider 会直接准备好 → 下次 advance 会处理；异步则返回 "advanced"
        return {"status": "advanced", "step_ids": next_expected, "next_step": new_next}

    if new_next in ("finalize", "bible_init"):
        # 合成步无需 LLM，立即推进
        return advance(state, pdir, provider=provider)

    return {"status": "advanced", "step_ids": [], "next_step": new_next}


def _request_already_made(provider: BaseProvider, step_id: str, pdir: Path) -> bool:
    """判定是否已经给这个 step 发过请求（避免重复 dispatch）。

    HumanQueue/Anthropic：看 queue/{sid}.prompt.md 是否存在
    Stub：看内部 cache（但 Stub 的 query() 在 request 后就 ready=True，不会走到这里）
    """
    return (pdir / "queue" / f"{step_id}.prompt.md").exists()


def _do_bible_init(state: NovelState, pdir: Path, provider: BaseProvider) -> dict:
    """V3 合成步：从 L1 构造初始 WorldBible。无 LLM 调用，立即推进到 L2_1。"""
    if state.l1 is None:
        raise RuntimeError("bible_init requires L1 to exist")
    state.world_bible = P.build_initial_bible(state.l1)
    state.trace.append({"step": "bible_init", "applied": "synthetic"})
    export_artifacts(state, pdir)
    state.next_step = decide_next(state)                        # → "L2_1"
    save_state(pdir, state)
    return advance(state, pdir, provider=provider)              # 立即 dispatch 下一步


def _do_finalize(state: NovelState, pdir: Path) -> dict:
    """finalize 步：不需 LLM，直接生成 MD。v2/v3/v4 用 L4 内容，v1 用 L3。"""
    has_l4_pipeline = state.user_input.pipeline_version in ("v2", "v3", "v4", "v5")
    # v2/v3/v4: L4 已经有实际 polished content；v1: 透传 L3
    if not has_l4_pipeline:
        state.l4 = [L4PolishedChapter(index=d.index, content=d.content) for d in state.l3]
    state.final_markdown = export_markdown(state)
    local_md = pdir / "novel.md"
    local_md.write_text(state.final_markdown, encoding="utf-8")
    (pdir / "trace.json").write_text(
        json.dumps(state.trace, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    top_md = export_top(state, pdir, state.final_markdown)
    state.next_step = "DONE"
    state.completed = True
    save_state(pdir, state)
    return {"status": "completed", "output": str(top_md), "project": str(local_md)}


def _is_retry(prev: str, new: str) -> bool:
    """新 next_step 是否是退回到上一层（重试）。"""
    if prev.endswith("_audit"):
        body = prev[:-len("_audit")]
        # V4 章节级审：L3_{i}_chapter_audit → 重跑 L3_{i}_1
        if body.endswith("_chapter"):
            layer_idx = body[:-len("_chapter")]
            return new == f"{layer_idx}_1"
        return body == new
    # final_audit 的 bounce-back 也是重试（含 V4 的 L3_1_1）
    if prev == "final_audit" and new in ("L1", "L2_1", "L3_1", "L3_1_1"):
        return True
    return False


def _cleanup_retry_files(pdir: Path, state: NovelState, new_next: str) -> None:
    """根据 bounce 目标清理对应的 queue/response 文件。"""
    total = state.user_input.chapter_count
    targets: list[str] = []

    def _l3_chapter_suffixes(ch: int) -> list[str]:
        # V1/V2/V3 章节级 + V4 章节级 audit + V4 所有场景
        out = [
            f"L3_{ch}", f"L3_{ch}_audit_logic", f"L3_{ch}_audit_pace",
            f"L3_{ch}_chapter_audit_logic",
            f"L3_{ch}_chapter_audit_pace",
            f"L3_{ch}_chapter_audit_continuity",
        ]
        # V4 场景：最多 8 个（schema 上限），清多了无害（文件不存在 unlink skip）
        for s in range(1, 9):
            out.append(f"L3_{ch}_{s}")
        return out

    def _l25_suffixes(ch: int) -> list[str]:
        return [f"L25_{ch}", f"L25_{ch}_audit_logic", f"L25_{ch}_audit_pace"]

    if new_next == "L1":
        targets = ["L1", "L1_audit_logic", "L1_audit_pace"]
        for i in range(1, total + 1):
            targets += [f"L2_{i}", f"L2_{i}_audit_logic", f"L2_{i}_audit_pace"]
            targets += _l25_suffixes(i)
            targets += _l3_chapter_suffixes(i)
    elif new_next == "L2_1":
        for i in range(1, total + 1):
            targets += [f"L2_{i}", f"L2_{i}_audit_logic", f"L2_{i}_audit_pace"]
            targets += _l25_suffixes(i)
            targets += _l3_chapter_suffixes(i)
    elif new_next == "L3_1" or new_next == "L3_1_1":
        for i in range(1, total + 1):
            targets += _l3_chapter_suffixes(i)
    elif new_next in (f"L2_{i}" for i in range(1, total + 1)) or new_next in (f"L3_{i}" for i in range(1, total + 1)):
        targets = [new_next, f"{new_next}_audit_logic", f"{new_next}_audit_pace"]
    elif new_next.startswith("L3_") and new_next.count("_") == 2:
        # V4: L3_{i}_1（章节级重写）
        parts = new_next.split("_")
        if len(parts) == 3 and parts[1].isdigit() and parts[2] == "1":
            ch = int(parts[1])
            targets = _l3_chapter_suffixes(ch)
    elif new_next.startswith("L25_"):
        parts = new_next.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            ch = int(parts[1])
            targets = _l25_suffixes(ch) + _l3_chapter_suffixes(ch)
    if targets:
        reset_step_files(pdir, targets)

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
    """next_step → 该步需要哪些 prompt 文件。audit 步有 2 个 head 并行；final_audit / L4_* / bible_update_* 是单个 prompt。"""
    if next_step in ("finalize", "DONE", "bible_init"):
        return []                                    # bible_init 为纯合成（无 LLM）
    if next_step == "final_audit":
        return ["final_audit"]
    if next_step.startswith(("L4_adversarial_", "L4_scrubber_", "bible_update_")):
        return [next_step]
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
        if sid == "L1":
            state.l1 = L1Skeleton(**_coerce_dict(data, expected_index=None))
        elif sid.startswith("L2_"):
            idx = int(sid.split("_")[1])
            outline = L2ChapterOutline(**_coerce_dict(data, expected_index=idx))
            _upsert_l2(state, outline)
        elif sid.startswith("L3_"):
            idx = int(sid.split("_")[1])
            draft = L3ChapterDraft(**_coerce_dict(data, expected_index=idx))
            _upsert_l3(state, draft)
        elif sid == "final_audit":
            state.final_verdict = FinalVerdict(**_coerce_dict(data, expected_index=None))
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


def _apply_bible_update_to_state(state: NovelState, update: BibleUpdate) -> None:
    """合并 BibleUpdate 到 state.world_bible。v3 专用。"""
    from .state import WorldBible
    current = state.world_bible or WorldBible()
    state.world_bible = P.apply_bible_update(current, update)
    state.current_bible_update_idx = max(state.current_bible_update_idx, update.chapter_index)


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
    v2 = state.user_input.pipeline_version == "v2"
    v3 = state.user_input.pipeline_version == "v3"
    use_final_audit = v2 or v3  # v3 沿用 v2 的成品审 + L4 润色

    # bible_init 合成步完成 → 进 L2_1（不经 LLM）
    if cur == "bible_init":
        return "L2_1"

    # bible_update_{i} 完成 → 交替：下一章 L2_{i+1}，或最后一章后的 final_audit
    if cur.startswith("bible_update_"):
        idx = int(cur.split("_")[2])
        if idx < total:
            return f"L2_{idx + 1}"
        return "final_audit" if use_final_audit else "finalize"

    # V2/V3 共用：final_audit / L4 分支优先判断（避免被下面 `_audit` 后缀误捕）
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

    # 主 prompt 完成 → 进 audit（L1/L2/L3 共有行为）
    if cur == "L1":
        return "L1_audit"
    if cur.startswith("L2_") and not cur.endswith("_audit"):
        return cur + "_audit"
    if cur.startswith("L3_") and not cur.endswith("_audit"):
        return cur + "_audit"

    # audit 完成 → 看 verdict（L1/L2/L3）
    if cur.endswith("_audit"):
        body = cur[:-len("_audit")]
        verdict = state.audit_history[-1]

        if body == "L1":
            current_rev = state.l1.revision
            if verdict.passed or should_force_pass(current_rev):
                # v3：先合成 bible_init，再进 L2_1
                return "bible_init" if v3 else "L2_1"
            state.l1.revision += 1
            return "L1"

        layer, idx_s = body.split("_", 1)
        idx = int(idx_s)
        if layer == "L2":
            cur_outline = next(c for c in state.l2 if c.index == idx)
            current_rev = cur_outline.revision
            if verdict.passed or should_force_pass(current_rev):
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
    v2_or_v3 = state.user_input.pipeline_version in ("v2", "v3")
    if state.final_bounce_count > MAX_FINAL_BOUNCES:
        state.trace.append({
            "bounce": "force_pass_max_reached",
            "count": state.final_bounce_count,
            "hint": fv.retry_hint,
        })
        return "L4_adversarial_1" if v2_or_v3 else "finalize"
    v3 = state.user_input.pipeline_version == "v3"
    s = fv.suspect_layer
    if s == "premise":
        state.trace.append({"bounce": "premise_unfixable", "hint": fv.retry_hint})
        return "DONE"
    if s == "L1":
        state.trace.append({"bounce": "to_L1", "hint": fv.retry_hint})
        state.l2.clear()
        state.l3.clear()
        state.l4.clear()
        if v3:
            # V3: L1 要重写，bible 要从头重建（下一轮 bible_init 会做）
            state.world_bible = None
            state.current_bible_update_idx = 0
        if state.l1:
            state.l1.revision += 1
        return "L1"
    if s == "L2":
        state.trace.append({"bounce": "to_L2_1", "hint": fv.retry_hint})
        state.l3.clear()
        state.l4.clear()
        if v3 and state.l1:
            # V3: L2 重写 → bible 重置到 post-L1 状态（抛掉后续 drifted facts）
            state.world_bible = P.build_initial_bible(state.l1)
            state.current_bible_update_idx = 0
        for c in state.l2:
            c.revision += 1
        return "L2_1"
    if s == "L3":
        state.trace.append({"bounce": "to_L3_1", "hint": fv.retry_hint})
        state.l4.clear()
        if v3 and state.l1:
            # V3: L3 重写 → bible 重置（否则旧版 drifted facts 会污染 retry 的 L3 prompt）
            state.world_bible = P.build_initial_bible(state.l1)
            state.current_bible_update_idx = 0
        for d in state.l3:
            d.revision += 1
        return "L3_1"
    # L4 / none：没法有意义地退（L4 还没跑），直接放行
    state.trace.append({"bounce": "force_pass", "hint": fv.retry_hint})
    return "L4_adversarial_1" if state.user_input.pipeline_version in ("v2", "v3") else "finalize"


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
    """finalize 步：不需 LLM，直接生成 MD。v2/v3 用 L4 内容，v1 用 L3。"""
    has_l4_pipeline = state.user_input.pipeline_version in ("v2", "v3")
    # v2/v3: L4 已经有实际 polished content；v1: 透传 L3
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
        return body == new
    # final_audit 的 bounce-back 也是重试
    if prev == "final_audit" and new in ("L1", "L2_1", "L3_1"):
        return True
    return False


def _cleanup_retry_files(pdir: Path, state: NovelState, new_next: str) -> None:
    """根据 bounce 目标清理对应的 queue/response 文件。"""
    total = state.user_input.chapter_count
    targets: list[str] = []
    if new_next == "L1":
        targets = ["L1", "L1_audit_logic", "L1_audit_pace"]
        # 如果从 L1 重跑，所有 L2/L3 的队列也该清（state 已 clear 但文件可能遗留）
        for i in range(1, total + 1):
            targets += [f"L2_{i}", f"L2_{i}_audit_logic", f"L2_{i}_audit_pace"]
            targets += [f"L3_{i}", f"L3_{i}_audit_logic", f"L3_{i}_audit_pace"]
    elif new_next == "L2_1":
        for i in range(1, total + 1):
            targets += [f"L2_{i}", f"L2_{i}_audit_logic", f"L2_{i}_audit_pace"]
            targets += [f"L3_{i}", f"L3_{i}_audit_logic", f"L3_{i}_audit_pace"]
    elif new_next == "L3_1":
        for i in range(1, total + 1):
            targets += [f"L3_{i}", f"L3_{i}_audit_logic", f"L3_{i}_audit_pace"]
    elif new_next in (f"L2_{i}" for i in range(1, total + 1)) or new_next in (f"L3_{i}" for i in range(1, total + 1)):
        # 单章重写：只清自身 + 自身 audit
        targets = [new_next, f"{new_next}_audit_logic", f"{new_next}_audit_pace"]
    if targets:
        reset_step_files(pdir, targets)

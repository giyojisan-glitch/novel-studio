"""Engine state-machine tests — routing between v1/v2 pipelines, bounce-back logic."""
from __future__ import annotations

from pathlib import Path

import pytest

from novel_studio.engine import (
    advance, expected_prompts, decide_next, _bounce_back,
    _coerce_dict, _looks_like_schema_envelope, _unwrap_schema_envelope,
    MAX_FINAL_BOUNCES,
)
from novel_studio.llm import StubProvider
from novel_studio.state import (
    NovelState, UserInput, L1Skeleton, CharacterCard, ThreeAct,
    L2ChapterOutline, L3ChapterDraft, AuditReport, AuditVerdict,
    FinalVerdict, WorldBible, WorldFact, BibleUpdate,
)
from novel_studio.utils import save_state
from novel_studio import prompts as P


def _make_state(pipeline: str = "v1", chapters: int = 3) -> NovelState:
    return NovelState(
        user_input=UserInput(
            premise="test premise long enough to not trigger length check",
            chapter_count=chapters,
            pipeline_version=pipeline,
        ),
        l1=L1Skeleton(
            title="T", logline="l", theme="th",
            protagonist=CharacterCard(name="p", traits=["a"], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=["r1", "r2", "r3"],
        ),
    )


def _pass_verdict(layer, idx=None):
    return AuditVerdict(
        layer=layer, target_index=idx,
        reports=[AuditReport(head="logic", passed=True, score=0.85)],
        passed=True, retry_hint="",
    )


def _fail_verdict(layer, idx=None):
    return AuditVerdict(
        layer=layer, target_index=idx,
        reports=[AuditReport(head="logic", passed=False, score=0.4, issues=["bad"])],
        passed=False, retry_hint="[logic] bad",
    )


# ---------- expected_prompts routing ----------


class TestExpectedPrompts:
    def test_finalize_empty(self):
        assert expected_prompts("finalize") == []
        assert expected_prompts("DONE") == []

    def test_l1_single(self):
        assert expected_prompts("L1") == ["L1"]

    def test_l2_single(self):
        assert expected_prompts("L2_1") == ["L2_1"]

    def test_audit_two_heads(self):
        assert expected_prompts("L1_audit") == ["L1_audit_logic", "L1_audit_pace"]
        assert expected_prompts("L3_2_audit") == ["L3_2_audit_logic", "L3_2_audit_pace"]

    def test_final_audit_single(self):
        assert expected_prompts("final_audit") == ["final_audit"]

    def test_l4_steps_single(self):
        assert expected_prompts("L4_adversarial_1") == ["L4_adversarial_1"]
        assert expected_prompts("L4_scrubber_3") == ["L4_scrubber_3"]


# ---------- decide_next — v1 pipeline ----------


class TestDecideNextV1:
    def test_l1_then_audit(self):
        s = _make_state("v1")
        s.next_step = "L1"
        assert decide_next(s) == "L1_audit"

    def test_l1_audit_pass_goes_to_l2_1(self):
        s = _make_state("v1")
        s.next_step = "L1_audit"
        s.audit_history.append(_pass_verdict("L1"))
        assert decide_next(s) == "L2_1"

    def test_l1_audit_fail_retries_l1(self):
        s = _make_state("v1")
        s.next_step = "L1_audit"
        s.audit_history.append(_fail_verdict("L1"))
        assert decide_next(s) == "L1"
        assert s.l1.revision == 1

    def test_last_l3_audit_v1_goes_to_finalize(self):
        s = _make_state("v1", chapters=2)
        s.l2 = [
            L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                             pov="p", key_events=["e"], prev_connection="pc"),
            L2ChapterOutline(index=2, title="c2", summary="s", hook="h",
                             pov="p", key_events=["e"], prev_connection="pc"),
        ]
        s.l3 = [
            L3ChapterDraft(index=1, content="a", word_count=1),
            L3ChapterDraft(index=2, content="b", word_count=1),
        ]
        s.next_step = "L3_2_audit"
        s.audit_history.append(_pass_verdict("L3", 2))
        assert decide_next(s) == "finalize"


# ---------- decide_next — v2 pipeline ----------


class TestDecideNextV2:
    def test_last_l3_audit_v2_goes_to_final_audit(self):
        s = _make_state("v2", chapters=2)
        s.l2 = [
            L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                             pov="p", key_events=["e"], prev_connection="pc"),
            L2ChapterOutline(index=2, title="c2", summary="s", hook="h",
                             pov="p", key_events=["e"], prev_connection="pc"),
        ]
        s.l3 = [
            L3ChapterDraft(index=1, content="a", word_count=1),
            L3ChapterDraft(index=2, content="b", word_count=1),
        ]
        s.next_step = "L3_2_audit"
        s.audit_history.append(_pass_verdict("L3", 2))
        assert decide_next(s) == "final_audit"

    def test_final_audit_usable_goes_to_l4_adversarial_1(self):
        s = _make_state("v2")
        s.next_step = "final_audit"
        s.final_verdict = FinalVerdict(usable=True, overall_score=0.85)
        assert decide_next(s) == "L4_adversarial_1"

    def test_l4_adversarial_to_l4_scrubber(self):
        s = _make_state("v2")
        s.next_step = "L4_adversarial_2"
        assert decide_next(s) == "L4_scrubber_2"

    def test_l4_scrubber_intermediate_goes_to_next_adversarial(self):
        s = _make_state("v2", chapters=3)
        s.next_step = "L4_scrubber_2"
        assert decide_next(s) == "L4_adversarial_3"

    def test_l4_scrubber_last_goes_to_finalize(self):
        s = _make_state("v2", chapters=3)
        s.next_step = "L4_scrubber_3"
        assert decide_next(s) == "finalize"


# ---------- Bounce-back ----------


class TestBounceBack:
    def test_premise_bounce_goes_to_done(self):
        s = _make_state("v2")
        fv = FinalVerdict(usable=False, overall_score=0.3,
                          symptoms=["premise self-contradicts"],
                          suspect_layer="premise", retry_hint="ask user")
        assert _bounce_back(s, fv) == "DONE"

    def test_l1_bounce_clears_l2_l3_l4_and_retries_l1(self):
        s = _make_state("v2")
        s.l2 = [L2ChapterOutline(index=1, title="c", summary="s", hook="h",
                                  pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="a", word_count=1)]
        fv = FinalVerdict(usable=False, overall_score=0.3,
                          symptoms=["world rule violation"],
                          suspect_layer="L1", retry_hint="fix rule X")
        assert _bounce_back(s, fv) == "L1"
        assert s.l2 == []
        assert s.l3 == []
        assert s.l1.revision == 1

    def test_l2_bounce_clears_l3_l4_retries_l2_1(self):
        s = _make_state("v2")
        s.l2 = [L2ChapterOutline(index=1, title="c", summary="s", hook="h",
                                  pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="a", word_count=1)]
        fv = FinalVerdict(usable=False, overall_score=0.4,
                          symptoms=["chapter 2 timeline break"],
                          suspect_layer="L2", retry_hint="fix ch2 date")
        assert _bounce_back(s, fv) == "L2_1"
        assert s.l3 == []
        assert s.l2[0].revision == 1

    def test_l3_bounce_preserves_l2_retries_l3_1(self):
        s = _make_state("v2")
        s.l2 = [L2ChapterOutline(index=1, title="c", summary="s", hook="h",
                                  pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="a", word_count=1)]
        fv = FinalVerdict(usable=False, overall_score=0.5,
                          symptoms=["ch2 style off"],
                          suspect_layer="L3", retry_hint="fix ch2 voice")
        assert _bounce_back(s, fv) == "L3_1"
        assert len(s.l2) == 1  # L2 unchanged
        assert s.l3[0].revision == 1


# ---------- End-to-end smoke test with StubProvider ----------


def _setup_project(tmp_path: Path, pipeline: str = "v1", chapters: int = 2) -> tuple[NovelState, Path]:
    """构造一个最小项目目录 + 空 state。"""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    (pdir / "queue").mkdir()
    (pdir / "responses").mkdir()
    state = NovelState(
        user_input=UserInput(
            premise="end-to-end smoke test premise, long enough to not trigger length check",
            genre="科幻", chapter_count=chapters, target_words_per_chapter=500,
            pipeline_version=pipeline,
        )
    )
    save_state(pdir, state)
    return state, pdir


class TestV1PipelineSmoke:
    """用 StubProvider 跑通 V1 pipeline 从 init 到 DONE。"""

    def test_v1_full_run_completes(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v1", chapters=2)
        provider = StubProvider()
        # 循环 advance 直到 DONE 或卡死（最多 50 步防死循环）
        for _ in range(50):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed is True
        assert state.next_step == "DONE"
        # V1 pipeline: L1 + L2 × 2 + L3 × 2 + L4 透传
        assert state.l1 is not None
        assert len(state.l2) == 2
        assert len(state.l3) == 2
        assert len(state.l4) == 2  # L4 透传也会填
        # 最终输出文件应该生成
        assert (pdir / "novel.md").exists()
        assert len(state.final_markdown) > 0


class TestBugASchemaEnvelope:
    """Bug A 修复：Doubao 偶尔把自己的 JSON Schema 当响应返回，真数据藏在 properties.*.value。
    _coerce_dict 现在会识别这种壳并剥壳。"""

    def test_schema_envelope_detected(self):
        envelope = {
            "type": "object",
            "properties": {
                "usable": {"type": "boolean", "value": False},
                "overall_score": {"type": "number", "value": 0.3},
            },
            "required": ["usable"],
        }
        assert _looks_like_schema_envelope(envelope) is True

    def test_flat_dict_not_envelope(self):
        flat = {"usable": True, "overall_score": 0.85}
        assert _looks_like_schema_envelope(flat) is False

    def test_unwrap_extracts_values(self):
        envelope = {
            "type": "object",
            "properties": {
                "usable": {"type": "boolean", "value": False},
                "symptoms": {"type": "array", "value": ["bad1", "bad2"]},
                "suspect_layer": {"type": "string", "default": "none", "value": "L3"},
            },
            "required": ["usable"],
        }
        out = _unwrap_schema_envelope(envelope)
        assert out == {"usable": False, "symptoms": ["bad1", "bad2"], "suspect_layer": "L3"}

    def test_coerce_dict_unwraps_envelope(self):
        envelope = {
            "type": "object",
            "properties": {
                "usable": {"value": True},
                "overall_score": {"value": 0.8},
            },
        }
        out = _coerce_dict(envelope, expected_index=None)
        assert out == {"usable": True, "overall_score": 0.8}

    def test_coerce_dict_flat_passthrough(self):
        flat = {"usable": True, "overall_score": 0.8}
        assert _coerce_dict(flat, expected_index=None) == flat


class TestBugBFinalAuditBounceCap:
    """Bug B 修复：final_audit 反复 bounce-back 会死循环（LLM 给相同 retry_hint）。
    超过 MAX_FINAL_BOUNCES 强制放行。"""

    def test_bounce_count_increments(self):
        s = _make_state("v2")
        assert s.final_bounce_count == 0
        fv = FinalVerdict(usable=False, overall_score=0.3, suspect_layer="L3",
                          retry_hint="fix X")
        _bounce_back(s, fv)
        assert s.final_bounce_count == 1

    def test_force_pass_after_max(self):
        s = _make_state("v2")
        s.l2 = [L2ChapterOutline(index=1, title="c", summary="s", hook="h",
                                 pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="a", word_count=1)]
        fv = FinalVerdict(usable=False, overall_score=0.3, suspect_layer="L3",
                          retry_hint="fix X")
        # 连续 bounce 到超限
        for _ in range(MAX_FINAL_BOUNCES):
            target = _bounce_back(s, fv)
            assert target == "L3_1", f"未超限应该正常 bounce，got {target}"
        # 下一次必须强制放行
        target = _bounce_back(s, fv)
        assert target == "L4_adversarial_1"
        # trace 里留痕
        assert any(t.get("bounce") == "force_pass_max_reached" for t in s.trace)


class TestBugCBibleDedup:
    """Bug C 修复：apply_bible_update 会对 facts/timeline 去重，避免 bounce 后重复累积。"""

    def test_facts_deduped_by_category_and_content(self):
        bible = WorldBible(facts=[
            WorldFact(category="rule", content="重复规则", ch_introduced=1),
        ])
        update = BibleUpdate(
            chapter_index=2,
            new_facts=[
                WorldFact(category="rule", content="重复规则", ch_introduced=2),      # 重复
                WorldFact(category="rule", content="新规则", ch_introduced=2),         # 新
                WorldFact(category="item", content="重复规则", ch_introduced=2),       # 同 content 不同 category → 保留
            ],
        )
        out = P.apply_bible_update(bible, update)
        assert len(out.facts) == 3  # 1 老 + 2 新（category=rule 的"重复规则"被去重）
        rule_contents = [f.content for f in out.facts if f.category == "rule"]
        assert rule_contents.count("重复规则") == 1

    def test_timeline_deduped(self):
        bible = WorldBible(timeline=["事件A"])
        update = BibleUpdate(
            chapter_index=2,
            timeline_additions=["事件A", "事件A", "事件B"],  # 3 条里 1 个全新
        )
        out = P.apply_bible_update(bible, update)
        assert out.timeline == ["事件A", "事件B"]


class TestBugEFinalAuditHintInPrompts:
    """Bug E 修复：final_audit bounce-back 时，L1/L2/L3 prompt 能看到 final_verdict.retry_hint。"""

    def test_l3_prompt_includes_final_hint_on_bounce(self):
        s = _make_state("v3", chapters=3)
        s.l2 = [L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                 pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="x", word_count=1)]
        s.final_verdict = FinalVerdict(
            usable=False, overall_score=0.3, suspect_layer="L3",
            retry_hint="恢复亡父七天的核心设定；删除新增人物",
            symptoms=["时间线矛盾", "新增角色"],
        )
        s.final_bounce_count = 1
        prompt = P.l3_prompt(s, chapter_idx=1)
        assert "成品审反馈" in prompt
        assert "恢复亡父七天" in prompt
        assert "时间线矛盾" in prompt

    def test_l2_prompt_includes_final_hint_on_bounce(self):
        s = _make_state("v3", chapters=3)
        s.final_verdict = FinalVerdict(
            usable=False, overall_score=0.4, suspect_layer="L2",
            retry_hint="梗概缺少主线推进",
        )
        s.final_bounce_count = 1
        prompt = P.l2_prompt(s, chapter_idx=1)
        assert "成品审反馈" in prompt
        assert "梗概缺少主线推进" in prompt

    def test_l3_prompt_omits_final_hint_when_usable(self):
        s = _make_state("v3", chapters=3)
        s.l2 = [L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                 pov="p", key_events=["e"], prev_connection="pc")]
        s.l3 = [L3ChapterDraft(index=1, content="x", word_count=1)]
        s.final_verdict = FinalVerdict(usable=True, overall_score=0.85)
        prompt = P.l3_prompt(s, chapter_idx=1)
        assert "成品审反馈" not in prompt

    def test_v3_bounce_l3_resets_bible(self):
        """V3: L3 suspect → bounce 清 bible 回到 post-L1 状态（抛掉 drifted facts）。"""
        s = _make_state("v3", chapters=3)
        # 模拟已经被 drift 污染的 bible
        s.world_bible = WorldBible(facts=[
            WorldFact(category="rule", content="drifted fact", ch_introduced=5),
        ])
        s.current_bible_update_idx = 5
        fv = FinalVerdict(usable=False, overall_score=0.3, suspect_layer="L3",
                          retry_hint="x")
        _bounce_back(s, fv)
        # bible 应被重置到 post-L1 状态（只含 L1 的 world_rules）
        assert s.world_bible is not None
        drifted = [f for f in s.world_bible.facts if f.content == "drifted fact"]
        assert drifted == []
        assert s.current_bible_update_idx == 0


class TestDecideNextV3:
    """V3 interleaved flow: L1 → L1_audit → bible_init → L2_1 → L2_1_audit → L3_1 → L3_1_audit → bible_update_1 → L2_2 → ..."""

    def test_l1_audit_pass_v3_goes_to_bible_init(self):
        s = _make_state("v3")
        s.next_step = "L1_audit"
        s.audit_history.append(_pass_verdict("L1"))
        assert decide_next(s) == "bible_init"

    def test_bible_init_goes_to_l2_1(self):
        s = _make_state("v3")
        s.next_step = "bible_init"
        assert decide_next(s) == "L2_1"

    def test_l2_audit_pass_v3_interleaves_to_l3_same_idx(self):
        s = _make_state("v3", chapters=3)
        s.l2 = [L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                 pov="p", key_events=["e"], prev_connection="pc")]
        s.next_step = "L2_1_audit"
        s.audit_history.append(_pass_verdict("L2", 1))
        assert decide_next(s) == "L3_1"  # 不是 L2_2

    def test_l3_audit_pass_v3_goes_to_bible_update(self):
        s = _make_state("v3", chapters=3)
        s.l3 = [L3ChapterDraft(index=1, content="x", word_count=1)]
        s.next_step = "L3_1_audit"
        s.audit_history.append(_pass_verdict("L3", 1))
        assert decide_next(s) == "bible_update_1"

    def test_bible_update_mid_chapter_goes_to_next_l2(self):
        s = _make_state("v3", chapters=3)
        s.next_step = "bible_update_2"
        assert decide_next(s) == "L2_3"

    def test_bible_update_last_chapter_goes_to_final_audit(self):
        s = _make_state("v3", chapters=3)
        s.next_step = "bible_update_3"
        assert decide_next(s) == "final_audit"


class TestV3PipelineSmoke:
    """用 StubProvider 跑通 V3 pipeline（含 bible_init + 交替 L2/L3 + bible_update + final_audit + L4）。"""

    def test_v3_full_run_completes(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v3", chapters=3)
        provider = StubProvider()
        for _ in range(200):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed is True
        # V3: bible 必须被初始化 + 每章被更新
        assert state.world_bible is not None
        assert state.world_bible.last_updated_ch == 3
        # 注意：stub 每章 bible_update 返回相同 timeline 字符串，
        # apply_bible_update 有 dedup → 只保留 1 条。真 LLM 每章会产出不同内容。
        assert len(state.world_bible.timeline) >= 1
        # trace 顺序验证：L3_1 必须出现在 L2_2 之前（interleaved）
        steps_ran = [t["step"] for t in state.trace if "step" in t]
        assert "bible_init" in steps_ran
        assert "bible_update_1" in steps_ran and "bible_update_3" in steps_ran
        l3_1_at = steps_ran.index("L3_1")
        l2_2_at = steps_ran.index("L2_2")
        assert l3_1_at < l2_2_at, f"interleaved expected: L3_1={l3_1_at} L2_2={l2_2_at}"
        # V3 沿用 final_audit + L4 管道
        assert state.final_verdict is not None
        assert len(state.l4) == 3


class TestV2PipelineSmoke:
    """用 StubProvider 跑通 V2 pipeline（含 final_audit + L4 adversarial + L4 scrubber）。"""

    def test_v2_full_run_completes(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v2", chapters=2)
        provider = StubProvider()
        for _ in range(80):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed is True
        # V2: 必须有 final_verdict
        assert state.final_verdict is not None
        assert state.final_verdict.usable is True  # Stub 默认 usable=True
        # L4 必须有真实产出（不是透传）
        assert len(state.l4) == 2
        for p in state.l4:
            # Stub scrubber 给了 content + polish_notes
            assert p.content, f"章节 {p.index} L4 content 空"
            assert len(p.polish_notes) > 0, f"章节 {p.index} 没有 polish_notes"
        # trace 里应该有 final_audit 步骤
        steps_ran = [t["step"] for t in state.trace if "step" in t]
        assert "final_audit" in steps_ran
        assert any(s.startswith("L4_adversarial_") for s in steps_ran)
        assert any(s.startswith("L4_scrubber_") for s in steps_ran)

    # Note: bounce-back behavior is covered thoroughly by TestBounceBack unit tests.
    # End-to-end bounce-and-recover requires per-call Stub response scripting which
    # isn't worth the test complexity; smoke test above + unit tests above are enough.

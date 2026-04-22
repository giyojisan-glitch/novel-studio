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
    SceneOutline, ChapterSceneList, L3SceneDraft, SceneCard,
    TrackedObject, CharacterState,
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
        # Shape A：data 塞在 value 里
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

    # ---------- Bug A 扩展：Shape B（V4 L25 观察到的纯 schema 壳） ----------

    def test_shape_b_defs_envelope_detected(self):
        """Shape B：doubao 返回纯 schema（有 $defs/properties/required，无 value）。"""
        shape_b = {
            "$defs": {"SceneOutline": {"type": "object"}},
            "properties": {
                "chapter_index": {"type": "integer"},
                "scenes": {"type": "array", "items": {"$ref": "#/$defs/SceneOutline"}},
            },
            "required": ["chapter_index", "scenes"],
        }
        assert _looks_like_schema_envelope(shape_b) is True

    def test_shape_b_required_only_detected(self):
        """有 required + schema-like properties，无 $defs / type=object，仍识别为 Shape B。"""
        shape_b = {
            "properties": {"usable": {"type": "boolean"}, "score": {"type": "number"}},
            "required": ["usable"],
        }
        assert _looks_like_schema_envelope(shape_b) is True

    def test_shape_b_unwrap_raises_with_actionable_hint(self):
        """Shape B 无数据 → _unwrap_schema_envelope 应 raise 且消息含"删除 response"提示。"""
        shape_b = {
            "$defs": {},
            "properties": {"chapter_index": {"type": "integer"}, "scenes": {"type": "array"}},
            "required": ["chapter_index"],
        }
        with pytest.raises(ValueError) as excinfo:
            _unwrap_schema_envelope(shape_b)
        assert "Schema 壳" in str(excinfo.value) or "schema" in str(excinfo.value).lower()
        assert "删除" in str(excinfo.value) or "删" in str(excinfo.value)

    def test_coerce_dict_raises_on_shape_b(self):
        """_coerce_dict 对 Shape B 也应 raise（通过 _unwrap_schema_envelope）。"""
        shape_b = {
            "properties": {"chapter_index": {"type": "integer"}},
            "required": ["chapter_index"],
        }
        with pytest.raises(ValueError):
            _coerce_dict(shape_b, expected_index=None)

    # ---------- Bug A 再扩展：Shape C（V5 bible_update 观察到的混合数据壳） ----------

    def test_shape_c_real_data_in_properties_detected(self):
        """Shape C：顶层 schema 壳（$defs / required），但 properties 值已经是真实数据
        （scalar / list / 带数据 key 的 dict），不是 schema 定义。"""
        shape_c = {
            "$defs": {"CharacterState": {"type": "object"}},
            "properties": {
                "chapter_index": 2,
                "new_characters": [{"name": "沈清", "traits": ["棋艺高超"]}],
                "timeline_additions": ["沈清赢下第二轮"],
            },
            "required": ["chapter_index"],
            "title": "BibleUpdate",
            "type": "object",
        }
        assert _looks_like_schema_envelope(shape_c) is True

    def test_shape_c_unwrap_returns_properties_as_data(self):
        """Shape C 剥壳后应直接返回 properties 作为数据 dict（不走 Shape A 的 value 提取）。"""
        shape_c = {
            "$defs": {},
            "properties": {
                "chapter_index": 2,
                "new_characters": [{"name": "沈清", "traits": ["棋艺高超"]}],
            },
            "required": ["chapter_index"],
        }
        out = _unwrap_schema_envelope(shape_c)
        assert out == {
            "chapter_index": 2,
            "new_characters": [{"name": "沈清", "traits": ["棋艺高超"]}],
        }

    def test_shape_c_coerce_dict_unwraps_real_data(self):
        """_coerce_dict 对 Shape C 正确剥壳并返回真实数据。"""
        shape_c = {
            "$defs": {},
            "properties": {
                "chapter_index": 3,
                "new_facts": [
                    {"category": "item", "content": "染血裂纹棋盘", "ch_introduced": 3}
                ],
            },
            "required": ["chapter_index"],
            "type": "object",
        }
        out = _coerce_dict(shape_c, expected_index=None)
        assert out["chapter_index"] == 3
        assert out["new_facts"][0]["content"] == "染血裂纹棋盘"


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


class TestExpectedPromptsV4:
    """V4 新 step 形式的 expected_prompts 路由。"""

    def test_l25_single(self):
        assert expected_prompts("L25_1") == ["L25_1"]

    def test_l25_audit_two_heads(self):
        assert expected_prompts("L25_2_audit") == ["L25_2_audit_logic", "L25_2_audit_pace"]

    def test_l3_scene_single(self):
        assert expected_prompts("L3_1_2") == ["L3_1_2"]

    def test_l3_chapter_audit_three_heads(self):
        """V4: 章节级审 = logic + pace + continuity。"""
        expected = expected_prompts("L3_2_chapter_audit")
        assert expected == [
            "L3_2_chapter_audit_logic",
            "L3_2_chapter_audit_pace",
            "L3_2_chapter_audit_continuity",
        ]


class TestDecideNextV4:
    """V4 interleaved + 场景分解路由：
    L1 → L1_audit → bible_init → L2_1 → L2_1_audit → L25_1 → L25_1_audit
        → L3_1_1 → L3_1_2 → ... → L3_1_chapter_audit → bible_update_1 → L2_2 → ...
    """

    def _v4_state(self, chapters=3):
        s = _make_state("v4", chapters=chapters)
        return s

    def _add_scene_list(self, s, ch, n_scenes):
        scenes = [SceneOutline(index=i, purpose="p", opening_beat="o",
                                closing_beat="c", approximate_words=300)
                  for i in range(1, n_scenes + 1)]
        s.scene_lists.append(ChapterSceneList(chapter_index=ch, scenes=scenes))

    def test_l1_audit_pass_goes_to_bible_init(self):
        s = self._v4_state()
        s.next_step = "L1_audit"
        s.audit_history.append(_pass_verdict("L1"))
        assert decide_next(s) == "bible_init"

    def test_bible_init_goes_to_l2_1(self):
        s = self._v4_state()
        s.next_step = "bible_init"
        assert decide_next(s) == "L2_1"

    def test_l2_audit_pass_v4_goes_to_l25(self):
        """v4: L2_i 过 → L25_i（而不是 L3_i 像 v3）。"""
        s = self._v4_state()
        s.l2 = [L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                 pov="p", key_events=["e"], prev_connection="pc")]
        s.next_step = "L2_1_audit"
        s.audit_history.append(_pass_verdict("L2", 1))
        assert decide_next(s) == "L25_1"

    def test_l25_goes_to_l25_audit(self):
        s = self._v4_state()
        s.next_step = "L25_1"
        assert decide_next(s) == "L25_1_audit"

    def test_l25_audit_pass_goes_to_first_scene(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 4)
        s.next_step = "L25_1_audit"
        s.audit_history.append(_pass_verdict("L25", 1))
        assert decide_next(s) == "L3_1_1"

    def test_l25_audit_fail_retries_l25(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 4)
        s.next_step = "L25_1_audit"
        s.audit_history.append(_fail_verdict("L25", 1))
        assert decide_next(s) == "L25_1"
        assert s.scene_lists[0].revision == 1

    def test_scene_advances_to_next_scene(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 4)
        s.next_step = "L3_1_2"
        assert decide_next(s) == "L3_1_3"

    def test_last_scene_advances_to_chapter_audit(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 3)       # 只有 3 场景
        s.next_step = "L3_1_3"
        assert decide_next(s) == "L3_1_chapter_audit"

    def test_chapter_audit_pass_goes_to_bible_update(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 3)
        s.l3_scenes = [L3SceneDraft(chapter_index=1, scene_index=i,
                                     content="x", word_count=1) for i in range(1, 4)]
        s.next_step = "L3_1_chapter_audit"
        s.audit_history.append(AuditVerdict(
            layer="L3", target_index=1,
            reports=[AuditReport(head="logic", passed=True, score=0.85),
                     AuditReport(head="pace", passed=True, score=0.75),
                     AuditReport(head="continuity", passed=True, score=0.82)],
            passed=True, retry_hint="",
        ))
        assert decide_next(s) == "bible_update_1"

    def test_chapter_audit_fail_retries_all_scenes_from_1(self):
        s = self._v4_state()
        self._add_scene_list(s, 1, 3)
        s.l3_scenes = [L3SceneDraft(chapter_index=1, scene_index=i,
                                     content="x", word_count=1) for i in range(1, 4)]
        s.next_step = "L3_1_chapter_audit"
        s.audit_history.append(_fail_verdict("L3", 1))
        assert decide_next(s) == "L3_1_1"
        for sd in s.l3_scenes:
            assert sd.revision == 1

    def test_bible_update_v4_mid_chapter_goes_to_next_l2(self):
        s = self._v4_state(chapters=3)
        s.next_step = "bible_update_2"
        assert decide_next(s) == "L2_3"

    def test_bible_update_v4_last_chapter_goes_to_final_audit(self):
        s = self._v4_state(chapters=3)
        s.next_step = "bible_update_3"
        assert decide_next(s) == "final_audit"


class TestV5BibleMerge:
    """V5: build_initial_bible + apply_bible_update 的 V5 字段合并。"""

    def _make_l1(self, visual_anchors=None, tracked=None):
        return L1Skeleton(
            title="T", logline="l", theme="t",
            protagonist=CharacterCard(name="沈砚", traits=["书生"], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=["r1"],
            visual_anchors=visual_anchors or [],
            tracked_object_names=tracked or [],
        )

    def test_build_initial_bible_copies_visual_anchors(self):
        l1 = self._make_l1(visual_anchors=["泥塑裂纹", "三碗见底"])
        bible = P.build_initial_bible(l1)
        assert bible.visual_anchors == ["泥塑裂纹", "三碗见底"]
        assert bible.fulfilled_anchors == []

    def test_build_initial_bible_seeds_tracked_objects(self):
        l1 = self._make_l1(tracked=["三碗酒", "半块木牌"])
        bible = P.build_initial_bible(l1)
        assert len(bible.tracked_objects) == 2
        assert bible.tracked_objects[0].name == "三碗酒"
        assert bible.tracked_objects[0].current_state == "初始 / 未使用"

    def test_apply_bible_update_updates_tracked_object_state(self):
        l1 = self._make_l1(tracked=["三碗酒"])
        bible = P.build_initial_bible(l1)
        update = BibleUpdate(
            chapter_index=3,
            object_state_changes=[TrackedObject(name="三碗酒", current_state="左碗裂")],
        )
        new_bible = P.apply_bible_update(bible, update)
        obj = next(o for o in new_bible.tracked_objects if o.name == "三碗酒")
        assert obj.current_state == "左碗裂"
        assert obj.last_changed_ch == 3
        assert "ch3: 左碗裂" in obj.state_history

    def test_apply_bible_update_character_status_change(self):
        l1 = self._make_l1()
        bible = P.build_initial_bible(l1)   # 沈砚 in bible.characters
        update = BibleUpdate(
            chapter_index=5,
            character_status_changes=[
                CharacterState(name="沈砚", status="fading", reliability=0.5),
            ],
        )
        new_bible = P.apply_bible_update(bible, update)
        shen = next(c for c in new_bible.characters if c.name == "沈砚")
        assert shen.status == "fading"
        assert shen.reliability == 0.5
        # 原有的 traits / arc_state 不应被覆盖
        assert shen.traits == ["书生"]

    def test_apply_bible_update_fulfilled_anchors_accumulate(self):
        l1 = self._make_l1(visual_anchors=["A", "B", "C"])
        bible = P.build_initial_bible(l1)
        update_a = BibleUpdate(chapter_index=1, visual_anchors_fulfilled=["A"])
        bible = P.apply_bible_update(bible, update_a)
        assert bible.fulfilled_anchors == ["A"]
        update_b = BibleUpdate(chapter_index=2, visual_anchors_fulfilled=["B"])
        bible = P.apply_bible_update(bible, update_b)
        assert bible.fulfilled_anchors == ["A", "B"]
        # 重复报告不累加
        update_dup = BibleUpdate(chapter_index=3, visual_anchors_fulfilled=["A", "C"])
        bible = P.apply_bible_update(bible, update_dup)
        assert bible.fulfilled_anchors == ["A", "B", "C"]


class TestV5FinalAuditEnforcement:
    """V5: unfulfilled_anchors 非空 → 强制 usable=False + suspect=L3。"""

    def test_unfulfilled_anchors_forces_not_usable(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v5", chapters=1)
        provider = StubProvider(overrides={
            "final_audit": {
                "usable": True,                              # LLM 说 OK
                "overall_score": 0.9,
                "symptoms": [],
                "suspect_layer": "none",
                "retry_hint": "",
                "slop_avg": 0.5,
                "unfulfilled_anchors": ["锚点 X 未兑现"],       # 但 V5 强制 flag
            },
        })
        # 模拟已经到 final_audit 步
        state.next_step = "final_audit"
        from novel_studio.engine import apply_responses
        provider.request("final_audit", "dummy", pdir)
        apply_responses(state, ["final_audit"], pdir, provider=provider)
        assert state.final_verdict.usable is False          # 强制翻转
        assert state.final_verdict.suspect_layer == "L3"
        assert "锚点 X 未兑现" in state.final_verdict.retry_hint

    def test_empty_unfulfilled_keeps_usable_as_is(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v5", chapters=1)
        provider = StubProvider()   # 默认 unfulfilled_anchors=[]
        state.next_step = "final_audit"
        from novel_studio.engine import apply_responses
        provider.request("final_audit", "dummy", pdir)
        apply_responses(state, ["final_audit"], pdir, provider=provider)
        assert state.final_verdict.usable is True  # 默认 stub 返 True


class TestV5PromptContent:
    """V5 prompt 渲染包含新注入块（time_marker 硬约束、tracked_objects、角色 status、
    unfulfilled anchors）。"""

    def _v5_state(self):
        s = _make_state("v5", chapters=3)
        # 补 L1 的 V5 字段
        s.l1.visual_anchors = ["A 视觉", "B 视觉"]
        s.l1.tracked_object_names = ["三碗酒", "木牌"]
        s.world_bible = P.build_initial_bible(s.l1)
        s.world_bible.time_markers_used = ["鸡鸣前", "第一声鸡鸣"]
        return s

    def test_l1_prompt_requires_visual_anchors(self):
        s = self._v5_state()
        prompt = P.l1_prompt(s)
        assert "visual_anchors" in prompt
        assert "tracked_object_names" in prompt
        assert "父亲化作泥塑上的裂纹" in prompt  # 正例

    def test_l25_prompt_lists_prior_time_markers(self):
        s = self._v5_state()
        s.l2.append(L2ChapterOutline(index=2, title="c2", summary="s", hook="h",
                                     pov="p", key_events=["e"], prev_connection="pc"))
        prompt = P.l25_prompt(s, chapter_idx=2)
        assert "全局时间轴锚点" in prompt or "time_markers" in prompt
        assert "鸡鸣前" in prompt and "第一声鸡鸣" in prompt
        assert "单调递进" in prompt

    def test_l3_scene_prompt_has_hard_time_marker(self):
        s = self._v5_state()
        # 添加 L2 + L2.5
        s.l2.append(L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                     pov="p", key_events=["e"], prev_connection="pc"))
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c",
                          approximate_words=300, time_marker="第二声鸡鸣")
        s.scene_lists.append(ChapterSceneList(chapter_index=1, scenes=[so]))
        import os
        os.environ["NOVEL_STUDIO_NO_RAG"] = "1"
        try:
            prompt = P.l3_scene_prompt(s, chapter_idx=1, scene_idx=1)
        finally:
            os.environ.pop("NOVEL_STUDIO_NO_RAG", None)
        assert "第二声鸡鸣" in prompt
        assert "禁止推进超过此 marker" in prompt
        # tracked_objects 当前状态注入
        assert "三碗酒" in prompt and "初始 / 未使用" in prompt
        # unfulfilled anchors 列出
        assert "A 视觉" in prompt

    def test_l3_scene_prompt_renders_fading_character(self):
        s = self._v5_state()
        # 手动添加 fading 角色
        s.world_bible.characters.append(
            CharacterState(name="沈父", status="fading", reliability=0.5)
        )
        s.l2.append(L2ChapterOutline(index=1, title="c1", summary="s", hook="h",
                                     pov="p", key_events=["e"], prev_connection="pc"))
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c",
                          approximate_words=300, time_marker="第二声鸡鸣")
        s.scene_lists.append(ChapterSceneList(chapter_index=1, scenes=[so]))
        import os
        os.environ["NOVEL_STUDIO_NO_RAG"] = "1"
        try:
            prompt = P.l3_scene_prompt(s, chapter_idx=1, scene_idx=1)
        finally:
            os.environ.pop("NOVEL_STUDIO_NO_RAG", None)
        assert "角色存续状态" in prompt
        assert "fading" in prompt
        assert "沈父" in prompt
        assert "模糊笔法" in prompt


class TestV5PipelineSmoke:
    """StubProvider 跑 V5 pipeline · 3 章 × 3 场景（V5 沿用 V4 路由）。"""

    def test_v5_full_run_populates_v5_bible_fields(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v5", chapters=3)
        provider = StubProvider()
        for _ in range(300):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed

        # V5 bible 字段都要填：
        assert state.world_bible is not None
        # 来自 L1 stub 模板的 visual_anchors / tracked_object_names
        assert state.world_bible.visual_anchors == ["测试视觉锚点 A", "测试视觉锚点 B"]
        # bible_init 生成的 2 个 tracked_objects
        assert len(state.world_bible.tracked_objects) == 2
        # 每章 L25 stub 模板有 3 个 time_markers，3 章共 9 条（allowing duplicates)
        assert len(state.world_bible.time_markers_used) >= 3


class TestV6BibleMerge:
    """V6: build_initial_bible 从 L1 copy plot_promises；apply_bible_update 维护
    promise.setup_ch / payoff_ch / fulfilled。"""

    def test_build_initial_copies_plot_promises(self):
        from novel_studio.state import PlotPromise, L1Skeleton, CharacterCard, ThreeAct
        l1 = L1Skeleton(
            title="t", logline="l", theme="T",
            protagonist=CharacterCard(name="沈清", traits=[], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=[],
            plot_promises=[
                PlotPromise(id="fs_1", content="埋下三颗死子"),
                PlotPromise(id="fs_2", content="揭露构陷证据"),
            ],
        )
        bible = P.build_initial_bible(l1)
        assert len(bible.plot_promises) == 2
        assert bible.plot_promises[0].id == "fs_1"
        assert bible.plot_promises[0].fulfilled is False

    def test_build_initial_propagates_faction_from_character_card(self):
        from novel_studio.state import L1Skeleton, CharacterCard, ThreeAct
        l1 = L1Skeleton(
            title="t", logline="l", theme="T",
            protagonist=CharacterCard(name="沈清", traits=[], want="w", need="n",
                                       faction="沈家"),
            antagonist=CharacterCard(name="顾衍之", traits=[], want="w", need="n",
                                      faction="顾府"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=[],
        )
        bible = P.build_initial_bible(l1)
        shen = next(c for c in bible.characters if c.name == "沈清")
        gu = next(c for c in bible.characters if c.name == "顾衍之")
        assert shen.faction == "沈家"
        assert gu.faction == "顾府"

    def test_apply_bible_update_marks_promise_setup(self):
        from novel_studio.state import PlotPromise, WorldBible, BibleUpdate
        bible = WorldBible(plot_promises=[
            PlotPromise(id="fs_1", content="埋死子"),
            PlotPromise(id="fs_2", content="引爆"),
        ])
        update = BibleUpdate(chapter_index=2, promise_setups_done=["fs_1"])
        new_bible = P.apply_bible_update(bible, update)
        fs1 = next(p for p in new_bible.plot_promises if p.id == "fs_1")
        fs2 = next(p for p in new_bible.plot_promises if p.id == "fs_2")
        assert fs1.setup_ch == 2
        assert fs1.fulfilled is False
        assert fs2.setup_ch == 0  # 其他 promise 不动

    def test_apply_bible_update_marks_payoff_fulfilled(self):
        from novel_studio.state import PlotPromise, WorldBible, BibleUpdate
        bible = WorldBible(plot_promises=[
            PlotPromise(id="fs_1", content="埋死子", setup_ch=1),
        ])
        update = BibleUpdate(chapter_index=5, promise_payoffs_done=["fs_1"])
        new_bible = P.apply_bible_update(bible, update)
        fs1 = next(p for p in new_bible.plot_promises if p.id == "fs_1")
        assert fs1.payoff_ch == 5
        assert fs1.fulfilled is True
        assert fs1.setup_ch == 1  # 原 setup_ch 不被覆盖

    def test_apply_bible_update_unknown_promise_id_ignored(self):
        from novel_studio.state import PlotPromise, WorldBible, BibleUpdate
        bible = WorldBible(plot_promises=[PlotPromise(id="fs_1", content="X")])
        update = BibleUpdate(chapter_index=2,
                             promise_setups_done=["fs_unknown"],
                             promise_payoffs_done=["also_unknown"])
        new_bible = P.apply_bible_update(bible, update)
        # 原 fs_1 不动
        assert new_bible.plot_promises[0].id == "fs_1"
        assert new_bible.plot_promises[0].setup_ch == 0

    def test_apply_bible_update_preserves_faction_on_character_update(self):
        from novel_studio.state import CharacterState, WorldBible, BibleUpdate
        bible = WorldBible(characters=[
            CharacterState(name="沈清", faction="沈家", arc_state="起点"),
        ])
        update = BibleUpdate(chapter_index=2, character_updates=[
            CharacterState(name="沈清", arc_state="觉醒", last_appeared_in=2),  # 没填 faction
        ])
        new_bible = P.apply_bible_update(bible, update)
        shen = next(c for c in new_bible.characters if c.name == "沈清")
        assert shen.faction == "沈家"           # 保留原 faction
        assert shen.arc_state == "觉醒"


class TestV6FinalAuditEnforcement:
    """V6: unfulfilled_promises 非空 → 强制 usable=False + suspect=L3。"""

    def test_unfulfilled_promises_forces_not_usable(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v6", chapters=1)
        provider = StubProvider(overrides={
            "final_audit": {
                "usable": True,
                "overall_score": 0.9,
                "symptoms": [],
                "suspect_layer": "none",
                "retry_hint": "",
                "slop_avg": 0.5,
                "unfulfilled_anchors": [],
                "unfulfilled_promises": ["fs_3 死子未引爆"],
            },
        })
        state.next_step = "final_audit"
        from novel_studio.engine import apply_responses
        provider.request("final_audit", "dummy", pdir)
        apply_responses(state, ["final_audit"], pdir, provider=provider)
        assert state.final_verdict.usable is False
        assert state.final_verdict.suspect_layer == "L3"
        assert "fs_3 死子未引爆" in state.final_verdict.retry_hint


class TestV6PromptContent:
    """V6 prompt 渲染包含新注入块（plot_promises / faction / technical_setup/payoff /
    unfulfilled_promises）。"""

    def _v6_state(self):
        from novel_studio.state import PlotPromise
        s = _make_state("v6", chapters=3)
        s.l1.visual_anchors = ["A 视觉"]
        s.l1.tracked_object_names = ["棋盘"]
        s.l1.plot_promises = [
            PlotPromise(id="fs_1", content="埋下跨越十年的死子"),
            PlotPromise(id="fs_2", content="揭露构陷证据"),
        ]
        s.l1.protagonist.faction = "沈家"
        if s.l1.antagonist:
            s.l1.antagonist.faction = "顾府"
        s.world_bible = P.build_initial_bible(s.l1)
        return s

    def test_l1_prompt_requires_plot_promises(self):
        s = self._v6_state()
        prompt = P.l1_prompt(s)
        assert "plot_promises" in prompt
        assert "faction" in prompt
        assert "死子" in prompt or "跨越十年的" in prompt

    def test_l3_scene_prompt_injects_v6_blocks(self):
        from novel_studio.state import L2ChapterOutline, SceneOutline, ChapterSceneList
        s = self._v6_state()
        s.l2.append(L2ChapterOutline(
            index=1, title="c1", summary="s", hook="h", pov="p",
            key_events=["e"], prev_connection="pc",
            promise_setups=["fs_1"], promise_payoffs=[],
        ))
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c",
                          approximate_words=300, time_marker="某时",
                          technical_setup="白72手留下死子")
        s.scene_lists.append(ChapterSceneList(chapter_index=1, scenes=[so]))
        import os
        os.environ["NOVEL_STUDIO_NO_RAG"] = "1"
        try:
            prompt = P.l3_scene_prompt(s, chapter_idx=1, scene_idx=1)
        finally:
            os.environ.pop("NOVEL_STUDIO_NO_RAG", None)
        # plot_promises 账本
        assert "叙事承诺账本" in prompt
        assert "fs_1" in prompt and "埋下跨越十年的死子" in prompt
        # 本章分配
        assert "本章（ch1）分配到的叙事承诺" in prompt
        assert "需要本章 setup" in prompt
        # 阵营图谱
        assert "阵营图谱" in prompt
        assert "沈家" in prompt and "顾府" in prompt
        # 技术性伏笔
        assert "技术性伏笔" in prompt
        assert "白72手留下死子" in prompt

    def test_l25_prompt_has_v6_technical_block(self):
        from novel_studio.state import L2ChapterOutline
        s = self._v6_state()
        s.l2.append(L2ChapterOutline(
            index=1, title="c1", summary="s", hook="h", pov="p",
            key_events=["e"], prev_connection="pc",
            promise_setups=["fs_1"],
        ))
        prompt = P.l25_prompt(s, chapter_idx=1)
        assert "技术性伏笔" in prompt or "Technical Setup" in prompt
        assert "fs_1" in prompt
        assert "technical_setup" in prompt

    def test_final_audit_prompt_has_v6_promise_check(self):
        s = self._v6_state()
        # 必须要有 L3 正文否则 final_audit 会爆
        from novel_studio.state import L3ChapterDraft
        for i in range(1, 4):
            s.l3.append(L3ChapterDraft(index=i, content="第 %d 章正文" % i,
                                        word_count=100))
        prompt = P.final_audit_prompt(s, "全书 markdown", slop_avg=1.0)
        assert "Plot Promises 强制检查" in prompt
        assert "fs_1" in prompt
        assert "unfulfilled_promises" in prompt


class TestV6PipelineSmoke:
    """StubProvider 跑 V6 pipeline · 验证 V6 字段流转。"""

    def test_v6_full_run_populates_plot_promises_in_bible(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v6", chapters=3)
        provider = StubProvider()
        for _ in range(300):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed

        # L1 stub 模板定义了 2 条 plot_promises → bible 应 copy 到
        assert state.world_bible is not None
        assert len(state.world_bible.plot_promises) == 2
        assert state.world_bible.plot_promises[0].id == "fs_1"
        # V6 字段结构完整
        assert state.final_verdict is not None
        assert state.final_verdict.unfulfilled_promises == []


class TestSceneMultiScaleContext:
    """V4: _scene_multi_scale_context 在不同场景位置上产出正确的多尺度 context。"""

    def _v4_with_scenes(self, tmp_path_like=None):
        s = _make_state("v4", chapters=3)
        # 加第 1 章完整设计 + 正文
        so_a = SceneOutline(index=1, purpose="a", opening_beat="ao", closing_beat="ac")
        so_b = SceneOutline(index=2, purpose="b", opening_beat="bo", closing_beat="bc")
        s.scene_lists.append(ChapterSceneList(chapter_index=1, scenes=[so_a, so_b]))
        s.l3_scenes.append(L3SceneDraft(chapter_index=1, scene_index=1,
                                         content="A" * 100 + "第一场景结尾",
                                         word_count=110))
        s.l3_scenes.append(L3SceneDraft(chapter_index=1, scene_index=2,
                                         content="B" * 100 + "第二场景结尾",
                                         word_count=110))
        s.scene_cards.append(SceneCard(chapter_index=1, scene_index=1, outline=so_a,
                                        actual_opening="A" * 50,
                                        actual_closing="第一场景结尾"))
        s.scene_cards.append(SceneCard(chapter_index=1, scene_index=2, outline=so_b,
                                        actual_opening="B" * 50,
                                        actual_closing="第二场景结尾"))
        return s

    def test_first_scene_first_chapter_has_no_context(self):
        s = _make_state("v4", chapters=3)
        ctx = P._scene_multi_scale_context(s, chapter_idx=1, scene_idx=1)
        assert ctx == ""   # 第一章第一场景，无任何历史

    def test_mid_chapter_scene_uses_same_chapter_prev(self):
        s = self._v4_with_scenes()
        ctx = P._scene_multi_scale_context(s, chapter_idx=1, scene_idx=2)
        # 应引用场景 1 的尾段
        assert "同章上一场景" in ctx
        assert "第一场景结尾" in ctx

    def test_new_chapter_first_scene_uses_prev_chapter_last_scene(self):
        s = self._v4_with_scenes()
        # 加第 2 章设计
        so_c = SceneOutline(index=1, purpose="c", opening_beat="co", closing_beat="cc")
        s.scene_lists.append(ChapterSceneList(chapter_index=2, scenes=[so_c]))
        ctx = P._scene_multi_scale_context(s, chapter_idx=2, scene_idx=1)
        # 应引用第 1 章最后场景（场景 2）的尾段
        assert "上一章最后一场景" in ctx
        assert "第二场景结尾" in ctx

    def test_l3_scene_prompt_includes_anti_cold_open(self):
        s = self._v4_with_scenes()
        so_c = SceneOutline(index=1, purpose="c", opening_beat="co", closing_beat="cc",
                            approximate_words=400)
        s.scene_lists.append(ChapterSceneList(chapter_index=2, scenes=[so_c]))
        import os
        os.environ["NOVEL_STUDIO_NO_RAG"] = "1"
        try:
            prompt = P.l3_scene_prompt(s, chapter_idx=2, scene_idx=1)
        finally:
            os.environ.pop("NOVEL_STUDIO_NO_RAG", None)
        assert "禁止冷开场" in prompt
        assert "指节攥得发白" in prompt  # 明确禁止的模板


class TestV4PipelineSmoke:
    """StubProvider 跑完整 V4 pipeline：3 章 × 3 场景 = 9 个 L3_scene + 章节级 audit + bible_update + final_audit + L4。"""

    def test_v4_full_run_completes(self, tmp_path):
        state, pdir = _setup_project(tmp_path, pipeline="v4", chapters=3)
        provider = StubProvider()
        for _ in range(500):
            result = advance(state, pdir, provider=provider)
            if result.get("status") == "completed":
                break
        assert state.completed, "v4 did not complete"

        # L2.5: 每章一条 scene_lists
        assert len(state.scene_lists) == 3, f"expected 3 scene_lists, got {len(state.scene_lists)}"
        for csl in state.scene_lists:
            assert len(csl.scenes) >= 2, f"ch{csl.chapter_index} scenes too few"

        # L3 scenes: 每章 N 个（stub 模板是 3 个）
        by_ch: dict[int, int] = {}
        for sd in state.l3_scenes:
            by_ch[sd.chapter_index] = by_ch.get(sd.chapter_index, 0) + 1
        for ch in range(1, 4):
            assert by_ch.get(ch, 0) == 3, f"ch{ch} scenes = {by_ch.get(ch, 0)}, expected 3"

        # scene_cards: 和 l3_scenes 数量一致，且 actual_opening 被填
        assert len(state.scene_cards) == 9
        for card in state.scene_cards:
            assert card.actual_opening != ""

        # state.l3 被同步（章节级拼接）
        assert len(state.l3) == 3
        for d in state.l3:
            assert d.word_count > 0

        # bible 每章都被更新
        assert state.world_bible is not None
        assert state.world_bible.last_updated_ch == 3

        # final_audit + L4 都跑了
        assert state.final_verdict is not None
        assert len(state.l4) == 3

        # trace 顺序抽查：L3_1_1 在 L25_1_audit 之后，L3_1_chapter_audit 在 L3_1_3 之后
        steps = [t["step"] for t in state.trace if "step" in t]
        # 所有新 step 都出现过
        assert any(s.startswith("L25_") and not s.endswith("audit") for s in steps)
        assert "L3_1_1" in steps
        assert "L3_1_chapter_audit" in steps
        # 场景严格先于章节级 audit
        assert steps.index("L3_1_3") < steps.index("L3_1_chapter_audit")
        # bible_update 在 chapter_audit 之后
        assert steps.index("L3_1_chapter_audit") < steps.index("bible_update_1")


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

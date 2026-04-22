"""Schema tests — Pydantic 模型序列化/反序列化 + V2 新字段。"""
from __future__ import annotations

import pytest

from novel_studio.state import (
    NovelState,
    UserInput,
    CharacterCard,
    ThreeAct,
    L1Skeleton,
    L2ChapterOutline,
    L3ChapterDraft,
    L4PolishedChapter,
    AdversarialCut,
    AuditReport,
    AuditVerdict,
    FinalVerdict,
    SceneOutline,
    ChapterSceneList,
    L3SceneDraft,
    SceneCard,
    TrackedObject,
    CharacterState,
    WorldBible,
    BibleUpdate,
)


# ---------- UserInput ----------


class TestUserInput:
    def test_defaults(self):
        ui = UserInput(premise="一个测试前提，足够长")
        assert ui.genre == "科幻"
        assert ui.chapter_count == 3
        assert ui.target_words_per_chapter == 1000
        assert ui.language == "zh"
        assert ui.pipeline_version == "v1"

    def test_v2_pipeline_opt_in(self):
        ui = UserInput(premise="test", pipeline_version="v2")
        assert ui.pipeline_version == "v2"

    def test_chapter_count_bounds(self):
        with pytest.raises(ValueError):
            UserInput(premise="t", chapter_count=0)
        with pytest.raises(ValueError):
            UserInput(premise="t", chapter_count=31)  # V3 升到 30 章上限

    def test_v3_pipeline_opt_in(self):
        ui = UserInput(premise="test", pipeline_version="v3")
        assert ui.pipeline_version == "v3"

    def test_chapter_count_v3_upper(self):
        ui = UserInput(premise="t", chapter_count=20, pipeline_version="v3")
        assert ui.chapter_count == 20

    def test_words_per_chapter_bounds(self):
        with pytest.raises(ValueError):
            UserInput(premise="t", target_words_per_chapter=100)

    def test_v4_pipeline_opt_in(self):
        ui = UserInput(premise="test", pipeline_version="v4")
        assert ui.pipeline_version == "v4"
        assert ui.scenes_per_chapter_hint == 4  # 默认

    def test_v4_scenes_hint_bounds(self):
        ui = UserInput(premise="t", pipeline_version="v4", scenes_per_chapter_hint=3)
        assert ui.scenes_per_chapter_hint == 3
        with pytest.raises(ValueError):
            UserInput(premise="t", pipeline_version="v4", scenes_per_chapter_hint=1)
        with pytest.raises(ValueError):
            UserInput(premise="t", pipeline_version="v4", scenes_per_chapter_hint=9)

    def test_v5_pipeline_opt_in(self):
        ui = UserInput(premise="test", pipeline_version="v5")
        assert ui.pipeline_version == "v5"


# ---------- V4: SceneOutline / ChapterSceneList / L3SceneDraft / SceneCard ----------


class TestV4Schema:
    def test_scene_outline_basic(self):
        so = SceneOutline(
            index=1, purpose="主角进庙避雨",
            opening_beat="雨打瓦", closing_beat="看见斗笠客",
            dominant_motifs=["雨", "土地庙"],
            pov="第三人称限知", approximate_words=300,
        )
        assert so.index == 1
        assert "雨" in so.dominant_motifs

    def test_scene_outline_approximate_words_bounds(self):
        with pytest.raises(ValueError):
            SceneOutline(index=1, purpose="p", opening_beat="o",
                         closing_beat="c", approximate_words=50)
        with pytest.raises(ValueError):
            SceneOutline(index=1, purpose="p", opening_beat="o",
                         closing_beat="c", approximate_words=2000)

    def test_chapter_scene_list_defaults_empty(self):
        csl = ChapterSceneList(chapter_index=1)
        assert csl.scenes == []
        assert csl.transition_notes == []
        assert csl.revision == 0

    def test_chapter_scene_list_with_scenes(self):
        so1 = SceneOutline(index=1, purpose="p1", opening_beat="o1", closing_beat="c1")
        so2 = SceneOutline(index=2, purpose="p2", opening_beat="o2", closing_beat="c2")
        csl = ChapterSceneList(chapter_index=1, scenes=[so1, so2],
                               transition_notes=["场景 1→2 用物件过渡"])
        assert len(csl.scenes) == 2
        assert csl.scenes[0].index == 1

    def test_l3_scene_draft(self):
        d = L3SceneDraft(chapter_index=2, scene_index=3,
                         content="场景正文", word_count=200)
        assert d.chapter_index == 2 and d.scene_index == 3
        assert d.revision == 0

    def test_scene_card_with_actual_excerpts(self):
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c")
        sc = SceneCard(chapter_index=1, scene_index=1, outline=so,
                       actual_opening="实际开场", actual_closing="实际结尾",
                       actual_word_count=300)
        assert sc.outline.index == 1
        assert sc.actual_opening == "实际开场"

    def test_novel_state_v4_fields_default_empty(self):
        ui = UserInput(premise="test premise long enough", pipeline_version="v4")
        ns = NovelState(user_input=ui)
        assert ns.scene_lists == []
        assert ns.l3_scenes == []
        assert ns.scene_cards == []
        assert ns.current_l25_idx == 0
        assert ns.current_scene_idx == 0

    def test_novel_state_v4_roundtrip(self):
        ui = UserInput(premise="test premise long enough", pipeline_version="v4")
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c")
        csl = ChapterSceneList(chapter_index=1, scenes=[so])
        sd = L3SceneDraft(chapter_index=1, scene_index=1, content="x", word_count=1)
        card = SceneCard(chapter_index=1, scene_index=1, outline=so,
                         actual_opening="o", actual_closing="c")
        ns = NovelState(user_input=ui, scene_lists=[csl], l3_scenes=[sd],
                        scene_cards=[card], current_l25_idx=1, current_scene_idx=2)
        import json
        round_tripped = NovelState(**json.loads(ns.model_dump_json()))
        assert round_tripped.scene_lists == ns.scene_lists
        assert round_tripped.l3_scenes == ns.l3_scenes
        assert round_tripped.scene_cards == ns.scene_cards
        assert round_tripped.current_l25_idx == 1
        assert round_tripped.current_scene_idx == 2


# ---------- CharacterCard (wound/lie V2) ----------


class TestCharacterCard:
    def test_v2_fields_default_empty(self):
        c = CharacterCard(name="林", traits=["a"], want="w", need="n")
        assert c.wound == ""
        assert c.lie == ""

    def test_v2_fields_accepted(self):
        c = CharacterCard(
            name="林", traits=["a"], want="w", need="n",
            wound="father left at 6", lie="I am unwanted",
        )
        assert c.wound == "father left at 6"
        assert c.lie == "I am unwanted"


# ---------- L2 Foreshadow Ledger ----------


class TestL2ForeshadowLedger:
    def test_default_empty_ledger(self):
        l2 = L2ChapterOutline(
            index=1, title="t", summary="s", hook="h", pov="p",
            key_events=["e"], prev_connection="pc",
        )
        assert l2.foreshadow_planted == []
        assert l2.foreshadow_paid == []

    def test_ledger_populated(self):
        l2 = L2ChapterOutline(
            index=1, title="t", summary="s", hook="h", pov="p",
            key_events=["e"], prev_connection="pc",
            foreshadow_planted=["a mysterious letter", "the scar"],
            foreshadow_paid=[],
        )
        assert len(l2.foreshadow_planted) == 2


# ---------- L4 Adversarial + Polished ----------


class TestL4Schema:
    def test_adversarial_cut_category_literal(self):
        c = AdversarialCut(category="TELL", quoted_text="他很害怕", reason="show dont tell")
        assert c.category == "TELL"

    def test_invalid_category_rejected(self):
        with pytest.raises(ValueError):
            AdversarialCut(category="INVALID", quoted_text="x", reason="y")

    def test_polished_chapter_with_cuts(self):
        p = L4PolishedChapter(
            index=1,
            content="清洗后的正文",
            adversarial_cuts=[
                AdversarialCut(category="FAT", quoted_text="废话", reason="redundant")
            ],
            polish_notes=["删除废话段"],
        )
        assert p.content == "清洗后的正文"
        assert len(p.adversarial_cuts) == 1
        assert p.revision == 0


# ---------- FinalVerdict ----------


class TestFinalVerdict:
    def test_passing_verdict(self):
        fv = FinalVerdict(usable=True, overall_score=0.85)
        assert fv.usable
        assert fv.suspect_layer == "none"
        assert fv.symptoms == []

    def test_failing_verdict_with_suspect(self):
        fv = FinalVerdict(
            usable=False,
            overall_score=0.4,
            symptoms=["timeline contradiction in ch2"],
            suspect_layer="L2",
            retry_hint="第 2 章时间线改 2023-03 → 2024-01",
        )
        assert not fv.usable
        assert fv.suspect_layer == "L2"

    def test_score_bounded(self):
        with pytest.raises(ValueError):
            FinalVerdict(usable=True, overall_score=1.5)
        with pytest.raises(ValueError):
            FinalVerdict(usable=True, overall_score=-0.1)


# ---------- NovelState roundtrip ----------


class TestNovelStateRoundtrip:
    def test_empty_state_roundtrip(self):
        s = NovelState(user_input=UserInput(premise="a test"))
        blob = s.model_dump_json()
        s2 = NovelState.model_validate_json(blob)
        assert s2.user_input.premise == "a test"
        assert s2.pipeline_version_is_v1()  # sanity

    def test_full_state_roundtrip(self):
        s = NovelState(
            user_input=UserInput(premise="t", pipeline_version="v2"),
            l1=L1Skeleton(
                title="T", logline="l", theme="th",
                protagonist=CharacterCard(name="p", traits=["a"], want="w", need="n",
                                          wound="ww", lie="ll"),
                three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
                world_rules=["r1", "r2", "r3"],
            ),
            l2=[L2ChapterOutline(
                index=1, title="c1", summary="s1", hook="h1", pov="p",
                key_events=["e"], prev_connection="pc",
                foreshadow_planted=["mystery"],
            )],
            l3=[L3ChapterDraft(index=1, content="abc", word_count=3)],
            l4=[L4PolishedChapter(index=1, content="clean",
                                  adversarial_cuts=[AdversarialCut(
                                      category="FAT", quoted_text="fat", reason="r")],
                                  polish_notes=["note"])],
            final_verdict=FinalVerdict(usable=True, overall_score=0.8),
        )
        blob = s.model_dump_json()
        s2 = NovelState.model_validate_json(blob)
        assert s2.l1.protagonist.wound == "ww"
        assert s2.l2[0].foreshadow_planted == ["mystery"]
        assert s2.l4[0].adversarial_cuts[0].category == "FAT"
        assert s2.final_verdict.usable

    def test_backward_compat_v1_state_loads(self):
        """旧 state.json 没有 pipeline_version / wound / lie / foreshadow 字段——应该用默认值填。"""
        legacy = {
            "user_input": {
                "premise": "old test", "genre": "科幻",
                "chapter_count": 3, "target_words_per_chapter": 1000,
            },
            "l1": None,
            "l2": [], "l3": [], "l4": [],
            "current_l2_idx": 0, "current_l3_idx": 0,
            "audit_history": [], "cross_chapter_notes": [],
            "next_step": "L1", "completed": False,
            "final_markdown": "", "trace": [],
        }
        import json
        s = NovelState.model_validate_json(json.dumps(legacy))
        assert s.user_input.pipeline_version == "v1"    # default
        assert s.current_l4_idx == 0                    # default
        assert s.final_verdict is None                  # default


def pipeline_version_is_v1(self):
    return self.user_input.pipeline_version == "v1"
NovelState.pipeline_version_is_v1 = pipeline_version_is_v1


# ---------- V5: premise 忠实度字段 ----------


class TestV5Schema:
    def test_tracked_object_basic(self):
        t = TrackedObject(name="三碗酒", current_state="初始满碗",
                          last_changed_ch=1, state_history=["ch1: 满"])
        assert t.name == "三碗酒"
        assert t.last_changed_ch == 1
        assert t.state_history == ["ch1: 满"]

    def test_tracked_object_defaults(self):
        t = TrackedObject(name="木牌", current_state="初始")
        assert t.last_changed_ch == 0
        assert t.state_history == []

    def test_l1_visual_anchors_and_tracked_names(self):
        l1 = L1Skeleton(
            title="t", logline="l", theme="T",
            protagonist=CharacterCard(name="p", traits=["a"], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=["r1"],
            visual_anchors=["泥塑裂纹", "三碗酒同时见底"],
            tracked_object_names=["三碗酒", "木牌"],
        )
        assert l1.visual_anchors == ["泥塑裂纹", "三碗酒同时见底"]
        assert l1.tracked_object_names == ["三碗酒", "木牌"]

    def test_l1_v5_fields_default_empty(self):
        l1 = L1Skeleton(
            title="t", logline="l", theme="T",
            protagonist=CharacterCard(name="p", traits=["a"], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=["r1"],
        )
        assert l1.visual_anchors == []
        assert l1.tracked_object_names == []

    def test_character_state_status_and_reliability(self):
        cs = CharacterState(name="沈父", status="fading", reliability=0.4)
        assert cs.status == "fading"
        assert cs.reliability == 0.4

    def test_character_state_reliability_bounds(self):
        with pytest.raises(ValueError):
            CharacterState(name="X", reliability=-0.1)
        with pytest.raises(ValueError):
            CharacterState(name="X", reliability=1.5)

    def test_character_state_defaults_active(self):
        cs = CharacterState(name="X")
        assert cs.status == "active"
        assert cs.reliability == 1.0

    def test_scene_outline_time_marker(self):
        so = SceneOutline(index=1, purpose="p", opening_beat="o", closing_beat="c",
                          time_marker="第一声鸡鸣")
        assert so.time_marker == "第一声鸡鸣"

    def test_world_bible_v5_fields(self):
        wb = WorldBible(
            visual_anchors=["A", "B"],
            tracked_objects=[TrackedObject(name="三碗酒", current_state="初始")],
            fulfilled_anchors=["A"],
            time_markers_used=["鸡鸣前", "第一声鸡鸣"],
        )
        assert wb.visual_anchors == ["A", "B"]
        assert len(wb.tracked_objects) == 1
        assert wb.fulfilled_anchors == ["A"]
        assert wb.time_markers_used == ["鸡鸣前", "第一声鸡鸣"]

    def test_bible_update_v5_increments(self):
        bu = BibleUpdate(
            chapter_index=3,
            object_state_changes=[TrackedObject(name="三碗酒", current_state="左碗裂")],
            character_status_changes=[CharacterState(name="沈父", status="gone")],
            visual_anchors_fulfilled=["父亲化作泥塑裂纹"],
        )
        assert bu.object_state_changes[0].current_state == "左碗裂"
        assert bu.character_status_changes[0].status == "gone"
        assert bu.visual_anchors_fulfilled == ["父亲化作泥塑裂纹"]

    def test_final_verdict_unfulfilled_anchors_default_empty(self):
        fv = FinalVerdict(usable=True, overall_score=0.9)
        assert fv.unfulfilled_anchors == []

    def test_final_verdict_unfulfilled_anchors_set(self):
        fv = FinalVerdict(usable=False, overall_score=0.4,
                          unfulfilled_anchors=["锚点 X 未兑现"])
        assert fv.unfulfilled_anchors == ["锚点 X 未兑现"]

    def test_novel_state_v5_roundtrip(self):
        import json
        ui = UserInput(premise="long enough premise text for length check",
                       pipeline_version="v5")
        l1 = L1Skeleton(
            title="t", logline="l", theme="T",
            protagonist=CharacterCard(name="p", traits=["a"], want="w", need="n"),
            three_act=ThreeAct(setup="s", confrontation="c", resolution="r"),
            world_rules=["r1"],
            visual_anchors=["泥塑裂纹"],
            tracked_object_names=["三碗酒"],
        )
        wb = WorldBible(visual_anchors=["A"], tracked_objects=[
            TrackedObject(name="X", current_state="s")])
        fv = FinalVerdict(usable=False, overall_score=0.3, unfulfilled_anchors=["B"])
        ns = NovelState(user_input=ui, l1=l1, world_bible=wb, final_verdict=fv)
        ns2 = NovelState.model_validate_json(json.dumps(json.loads(ns.model_dump_json())))
        assert ns2.l1.visual_anchors == ["泥塑裂纹"]
        assert ns2.world_bible.tracked_objects[0].name == "X"
        assert ns2.final_verdict.unfulfilled_anchors == ["B"]

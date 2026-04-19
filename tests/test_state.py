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
            UserInput(premise="t", chapter_count=20)

    def test_words_per_chapter_bounds(self):
        with pytest.raises(ValueError):
            UserInput(premise="t", target_words_per_chapter=100)


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

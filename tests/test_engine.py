"""Engine state-machine tests — routing between v1/v2 pipelines, bounce-back logic."""
from __future__ import annotations

from pathlib import Path

import pytest

from novel_studio.engine import advance, expected_prompts, decide_next, _bounce_back
from novel_studio.llm import StubProvider
from novel_studio.state import (
    NovelState, UserInput, L1Skeleton, CharacterCard, ThreeAct,
    L2ChapterOutline, L3ChapterDraft, AuditReport, AuditVerdict,
    FinalVerdict,
)
from novel_studio.utils import save_state


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

"""Engine state-machine tests — routing between v1/v2 pipelines, bounce-back logic."""
from __future__ import annotations

import pytest

from novel_studio.engine import expected_prompts, decide_next, _bounce_back
from novel_studio.state import (
    NovelState, UserInput, L1Skeleton, CharacterCard, ThreeAct,
    L2ChapterOutline, L3ChapterDraft, AuditReport, AuditVerdict,
    FinalVerdict,
)


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

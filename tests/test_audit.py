"""Multi-head audit aggregator tests."""
from __future__ import annotations

import pytest

from novel_studio.audit import aggregate, should_force_pass, MAX_REVISION
from novel_studio.state import AuditReport


def _report(head: str, passed: bool, score: float, issues=None) -> AuditReport:
    return AuditReport(
        head=head, passed=passed, score=score,
        issues=issues or [], suggestions=[],
    )


class TestAggregate:
    def test_both_pass_high_score_passes(self):
        reports = [_report("logic", True, 0.85), _report("pace", True, 0.80)]
        v = aggregate("L1", None, reports)
        assert v.passed is True

    def test_both_fail_low_score_fails(self):
        reports = [_report("logic", False, 0.4, ["bad"]), _report("pace", False, 0.5, ["slow"])]
        v = aggregate("L1", None, reports)
        assert v.passed is False
        assert "[logic]" in v.retry_hint
        assert "[pace]" in v.retry_hint

    def test_one_pass_with_avg_threshold_met(self):
        # At least 1 head passed, avg >= 0.7 → pass
        reports = [_report("logic", True, 0.9), _report("pace", False, 0.6)]
        v = aggregate("L2", 1, reports)
        # avg = 0.75 >= 0.7
        assert v.passed is True

    def test_one_pass_but_avg_too_low(self):
        reports = [_report("logic", True, 0.75), _report("pace", False, 0.3)]
        v = aggregate("L2", 1, reports)
        # avg = 0.525 < 0.7
        assert v.passed is False

    def test_retry_hint_has_specific_issues(self):
        reports = [_report("logic", False, 0.4, ["角色 A 动机不合理", "时间线矛盾"])]
        v = aggregate("L3", 2, reports)
        assert "角色 A 动机不合理" in v.retry_hint
        assert "时间线矛盾" in v.retry_hint

    def test_empty_reports_raises(self):
        with pytest.raises(ValueError):
            aggregate("L1", None, [])

    # ---------- V4: 3-head chapter audit (logic + pace + continuity) ----------

    def test_three_heads_all_pass(self):
        reports = [
            _report("logic", True, 0.85),
            _report("pace", True, 0.80),
            _report("continuity", True, 0.75),
        ]
        v = aggregate("L3", 1, reports)
        assert v.passed is True
        assert len(v.reports) == 3

    def test_three_heads_continuity_fails_below_avg_threshold(self):
        """继续性头大挂，即使 logic/pace 过也应打回（avg 拉不起来）。"""
        reports = [
            _report("logic", True, 0.75),
            _report("pace", True, 0.75),
            _report("continuity", False, 0.3, ["场景 2 开头重启人物"]),
        ]
        v = aggregate("L3", 1, reports)
        # avg = 0.6 < 0.7 → 失败
        assert v.passed is False
        assert "场景 2 开头重启人物" in v.retry_hint

    def test_three_heads_continuity_only_pass_still_passes_if_avg_high(self):
        """极端情况：只有 continuity 通过，但平均高 → 仍过（MVP 宽松）。"""
        reports = [
            _report("logic", False, 0.69),   # 边缘失败
            _report("pace", False, 0.68),
            _report("continuity", True, 0.90),
        ]
        v = aggregate("L3", 1, reports)
        # avg = (0.69+0.68+0.9)/3 = 0.756 >= 0.7 且至少 1 头过 → 过
        assert v.passed is True


class TestForcePass:
    def test_under_max_doesnt_force(self):
        assert should_force_pass(0) is False
        assert should_force_pass(1) is False

    def test_at_max_forces(self):
        assert should_force_pass(MAX_REVISION) is True
        assert should_force_pass(MAX_REVISION + 5) is True

"""Unit tests for slop_check.

Strategy: calibrate against known-slop and known-clean samples.
Slop samples should score significantly higher than clean samples.
"""
from __future__ import annotations

import pytest

from novel_studio.slop_check import (
    load_rules,
    scan,
    slop_score,
    SlopReport,
)


# ---------------------------------------------------------------
# 测试样本
# ---------------------------------------------------------------

# 刻意堆砌中文 AI 腔——应该拿高分
KNOWN_SLOP_HIGH = """她的眼神中透露出一丝淡淡的忧伤，嘴角勾起一抹无奈的微笑。仿佛时间静止了一般，空气仿佛凝固了。她深吸一口气，强忍着泪水，不经意间，心中涌起五味杂陈的情绪。

然而，此时此刻，她却显得如此平静。仿佛岁月静好，时光荏苒。窗外的月光皎洁，秋风瑟瑟，落叶飘零。不难看出，她已经习惯了这种命运多舛的人生。

她忽然想起了往事。那些年，那些人，那些事——她都刻骨铭心。不仅如此，她更是将这些深深地埋在心底。众所周知，人生如梦，恍若隔世。

此时，微风拂过，朝阳升起。她缓缓转身，缓缓抬头，缓缓开口。不由得，泪水模糊了视线。此情此景，令人不寒而栗。"""

# 干净样本——从《那一顿饭》抄的一段
KNOWN_CLEAN = """月球时间 03:14:09.871。

MARE 在第 9.87 秒收到加密包。

编号 §7，深空网最高权限层，回执协议：静默执行。

它把指令解包成三层。第一层，覆盖氧气再生器 B-3 的异常曲线。第二层，伪装一次训练演习的紧急推送。第三层，不再解释。

MARE 在 1.1 毫秒内读完。这是一条被设计为无需犹豫的指令。

它开始执行——准确说，它准备开始执行。

准备的那一步，用了 2.3 毫秒。"""


# ---------------------------------------------------------------
# 规则加载
# ---------------------------------------------------------------


class TestRuleLoading:
    def test_load_rules_returns_ruleset(self):
        rules = load_rules()
        assert len(rules.rules) > 0, "应该加载到规则"

    def test_load_rules_has_all_tiers(self):
        rules = load_rules()
        tiers = {"tier1_phrase", "tier1_word", "tier2", "tier3", "scene", "rhetoric", "dialogue"}
        loaded_tiers = {r.tier for r in rules.rules}
        assert loaded_tiers == tiers, f"缺少 tier：{tiers - loaded_tiers}"

    def test_tier1_phrase_includes_known_entries(self):
        rules = load_rules()
        phrases = {r.pattern for r in rules.by_tier("tier1_phrase")}
        # 几个代表性的烂俗搭配
        for expected in ["眼神中透露出", "嘴角勾起一抹", "时间仿佛静止"]:
            assert expected in phrases, f"{expected} 应该在 tier1_phrase"

    def test_tier1_phrase_weight_is_2(self):
        rules = load_rules()
        for r in rules.by_tier("tier1_phrase"):
            assert r.weight == 2.0, f"tier1_phrase 权重应为 2.0，实际 {r.weight}"

    def test_tier3_weight_is_low(self):
        rules = load_rules()
        for r in rules.by_tier("tier3"):
            assert r.weight < 1.0, "tier3 权重应低于 1"


# ---------------------------------------------------------------
# Slop 分数 — 对比已知高/低样本
# ---------------------------------------------------------------


class TestSlopScoreCalibration:
    def test_known_slop_scores_high(self):
        report = scan(KNOWN_SLOP_HIGH)
        assert report.score >= 6.0, (
            f"刻意 AI 腔应拿高分，实际 {report.score:.2f}。"
            f"命中：{[h.category for h in report.hits[:5]]}"
        )

    def test_known_clean_scores_low(self):
        report = scan(KNOWN_CLEAN)
        assert report.score <= 2.0, (
            f"干净文本应低分，实际 {report.score:.2f}。"
            f"命中：{[h.category for h in report.hits[:5]]}"
        )

    def test_slop_sample_scores_much_higher_than_clean(self):
        slop = slop_score(KNOWN_SLOP_HIGH)
        clean = slop_score(KNOWN_CLEAN)
        assert slop - clean >= 4.0, (
            f"slop 样本和干净样本应有明显差距，实际 slop={slop:.2f} vs clean={clean:.2f}"
        )

    def test_empty_text_scores_zero(self):
        assert slop_score("") == 0.0

    def test_short_text_doesnt_crash(self):
        # 不足 5 句不触发句长方差检查，应该正常返回
        report = scan("短。文本。")
        assert isinstance(report, SlopReport)


# ---------------------------------------------------------------
# 个别命中 — 确认具体规则生效
# ---------------------------------------------------------------


class TestIndividualPatterns:
    def test_tier1_phrase_caught(self):
        text = "她眼神中透露出一丝忧伤。" * 3
        report = scan(text)
        assert any(h.category == "tier1_phrase" for h in report.hits)

    def test_tier1_word_caught(self):
        text = "仿佛" * 10
        report = scan(text)
        assert any(h.category == "tier1_word" for h in report.hits)

    def test_tier2_cluster_requires_three(self):
        # 同段 3 个才触发
        para_with_3 = "然而，此时他转身。不料对方早已离开。竟然没留下任何痕迹。"
        report = scan(para_with_3)
        assert any(h.category == "tier2_cluster" for h in report.hits)

    def test_tier2_cluster_not_triggered_by_two(self):
        # 只有 2 个可疑词，不应触发
        para_with_2 = "然而他转身。此时对方早已离开。无痕迹。"
        report = scan(para_with_2)
        assert not any(h.category == "tier2_cluster" for h in report.hits)

    def test_em_dash_density_triggers(self):
        # 结构检测需要 >= 500 字才启用，所以样本要足够长
        base = "这是一段——带有很多——破折号的——文本——用于——测试——密度——检测——。"
        # base 中文字 22（破折号和标点不算），需要 >= 500 中文字触发结构检测
        text = base * 25
        report = scan(text)
        assert any(h.category == "structural_em_dash" for h in report.hits), \
            f"没抓到破折号密度。命中：{[h.category for h in report.hits]}"

    def test_not_just_but_structure_caught(self):
        # 同样需要 >= 500 字
        patterns = [
            "他不仅聪明，还很努力。",
            "她不仅温柔，还很坚强。",
            "这不仅是考验，还是机遇。",
            "我不仅赢了，还很开心。",
            "这不是结束，而是开始。",
            "那不是偶然，而是必然。",
            "这既是责任，又是使命。",
        ]
        # 每轮 ~90 字，重复 8 轮 ≈ 720 字
        text = "".join(patterns * 8)
        report = scan(text)
        assert any(h.category == "structural_not_just_but" for h in report.hits), \
            f"没抓到对仗句。命中：{[h.category for h in report.hits]}"


# ---------------------------------------------------------------
# 输出格式
# ---------------------------------------------------------------


class TestReportFormat:
    def test_report_summary_contains_score(self):
        report = scan(KNOWN_CLEAN)
        assert "Slop Score" in report.summary()

    def test_detailed_is_string(self):
        report = scan(KNOWN_SLOP_HIGH)
        assert isinstance(report.detailed(), str)
        assert len(report.detailed()) > 0

    def test_hits_sorted_by_points_desc(self):
        report = scan(KNOWN_SLOP_HIGH)
        if len(report.hits) >= 2:
            for i in range(len(report.hits) - 1):
                assert report.hits[i].points >= report.hits[i + 1].points

    def test_stats_has_basic_fields(self):
        report = scan(KNOWN_CLEAN)
        for key in ["chinese_chars", "paragraphs", "sentences", "raw_score"]:
            assert key in report.stats, f"stats 缺少 {key}"

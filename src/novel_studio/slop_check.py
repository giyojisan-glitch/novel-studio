"""Mechanical slop detector — 无需 LLM 的 AI 味检测。

灵感来自 autonovel/ANTI-SLOP.md + autonovel/evaluate.py，完全中文化重写。

工作方式：
1. 从 styles/_anti_slop.md 加载词表规则
2. 对文本做正则匹配（Tier 1/2/3 词汇层）
3. 对文本做结构分析（破折号密度、句长方差、段首转场词、否定句重复等）
4. 综合输出 0-10 slop 分数 + 命中明细

设计原则：
- **不调 LLM**（省钱、快、确定性）
- **规则透明**（所有词表放在 markdown，可编辑）
- **分数可解释**（每个扣分都有具体 hit 对应）
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .utils import STYLES_ROOT


# ============================================================
# 规则加载（从 markdown 解析词表）
# ============================================================

RULES_FILE = STYLES_ROOT / "_anti_slop.md"

# 规则 section 标题 → 权重 的映射
# 解析 markdown 里 "## Tier 1 · 烂俗固定搭配（权重 2.0）" 这种标题
_SECTION_WEIGHT_PATTERN = re.compile(r"^##\s+(.+?)[（(].*?权重\s*([\d.]+).*?[)）]")
_LIST_ITEM_PATTERN = re.compile(r"^-\s+(.+?)\s*$")

# 结构 slop 的算法阈值（代码层常量，不在 md 里）
EM_DASH_PER_1000_THRESHOLD = 5.0
SENT_LEN_CV_THRESHOLD = 0.30          # 句长变异系数小于此值 = 太均匀
PARA_TRANSITION_RATIO_THRESHOLD = 0.30  # 段首是转场词的比率
TRIADIC_PER_1000_THRESHOLD = 3.0      # "X、Y、Z" 三并列密度
NOT_JUST_PER_1000_THRESHOLD = 3.0     # "不是 X 而是 Y" / "不仅 X 还 Y" 密度

# 段首转场词（出现在段首时计数）
PARA_OPENER_TRANSITIONS = {
    "然而", "然则", "此时", "此刻", "忽然", "蓦然", "不料", "只见",
    "此外", "不仅如此", "与此同时", "紧接着", "随后", "接着",
}

# Tier 2 聚集判定：同段出现这些词 N 个
TIER2_CLUSTER_THRESHOLD = 3

# 最终 slop 分数上限
SCORE_CAP = 10.0

# 归一化/结构检测的最小字数基准：
# - 短于此值的文本，归一化按此值计算（防止短文本 1 次命中被放大成高分）
# - 结构密度类检测（em-dash/triadic/not-just-but）也要求至少这么多字才启用
MIN_STRUCTURAL_CHARS = 500


@dataclass
class SlopRule:
    """单个词/短语规则。"""
    tier: str          # 'tier1_phrase' / 'tier1_word' / 'tier2' / 'tier3' / 'scene' / 'rhetoric' / 'dialogue'
    pattern: str       # 原始文本
    weight: float      # 每次命中加多少分


@dataclass
class RuleSet:
    """完整规则集（从 markdown 加载）。"""
    rules: list[SlopRule] = field(default_factory=list)

    def by_tier(self, tier: str) -> list[SlopRule]:
        return [r for r in self.rules if r.tier == tier]


def load_rules(path: Path | None = None) -> RuleSet:
    """从 markdown 文件解析规则。

    识别规则：
    - '## Xxx（权重 N.N）' 开头的 section 标题
    - section 下的 '- 词' 列表项作为规则
    """
    path = path or RULES_FILE
    if not path.exists():
        return RuleSet()

    rs = RuleSet()
    current_tier: str | None = None
    current_weight: float = 0.0

    section_to_tier = {
        "烂俗固定搭配": "tier1_phrase",
        "烂俗单字词": "tier1_word",
        "可疑词": "tier2",
        "填充短语": "tier3",
        "烂俗景物抒情": "scene",
        "烂俗修辞搭配": "rhetoric",
        "烂俗对白开场": "dialogue",
    }

    for line in path.read_text(encoding="utf-8").splitlines():
        # 匹配 section 标题
        m = _SECTION_WEIGHT_PATTERN.match(line)
        if m:
            title = m.group(1).strip()
            weight = float(m.group(2))
            # 找到对应的 tier
            current_tier = None
            for keyword, tier_name in section_to_tier.items():
                if keyword in title:
                    current_tier = tier_name
                    break
            current_weight = weight
            continue

        # section 下的列表项
        if current_tier and (item := _LIST_ITEM_PATTERN.match(line)):
            pattern = item.group(1).strip()
            if pattern:
                rs.rules.append(SlopRule(tier=current_tier, pattern=pattern, weight=current_weight))

    return rs


# ============================================================
# 检测算法
# ============================================================


@dataclass
class SlopHit:
    """单次命中。"""
    category: str       # 命中的类别（tier1_phrase / structural_em_dash 等）
    detail: str         # 具体什么东西命中（命中的词 / 命中数值）
    count: int          # 命中次数
    points: float       # 本次加分

    def __str__(self) -> str:
        c = f" ×{self.count}" if self.count > 1 else ""
        return f"[{self.category}] {self.detail}{c}  (+{self.points:.1f})"


@dataclass
class SlopReport:
    """完整扫描报告。"""
    score: float                          # 0-10 slop 分数
    hits: list[SlopHit]                   # 所有命中
    stats: dict                           # 统计（字数、段落数等）

    def summary(self) -> str:
        lines = [f"Slop Score: {self.score:.2f} / 10.0"]
        lines.append(f"  字数：{self.stats.get('chinese_chars', 0)} 中文字")
        lines.append(f"  段落：{self.stats.get('paragraphs', 0)}")
        lines.append(f"  命中：{len(self.hits)} 条")
        return "\n".join(lines)

    def detailed(self) -> str:
        lines = [self.summary(), ""]
        if not self.hits:
            lines.append("(无命中)")
            return "\n".join(lines)

        # 按类别聚合展示
        by_cat: dict[str, list[SlopHit]] = {}
        for h in self.hits:
            by_cat.setdefault(h.category, []).append(h)

        for cat, hs in sorted(by_cat.items(), key=lambda kv: -sum(h.points for h in kv[1])):
            total = sum(h.points for h in hs)
            lines.append(f"## {cat} (共 +{total:.1f})")
            for h in hs:
                lines.append(f"  - {h}")
            lines.append("")
        return "\n".join(lines)


# --- 工具函数 ---


def _chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _split_sentences(text: str) -> list[str]:
    """按中文标点切句。"""
    parts = re.split(r"[。！？!?；;]", text)
    return [p.strip() for p in parts if p.strip() and _chinese_char_count(p) >= 2]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def _scale_per_1000(count: int, total_chars: int) -> float:
    """将命中数折算为每 1000 字命中率。"""
    if total_chars == 0:
        return 0.0
    return count * 1000.0 / total_chars


# --- 词表检查 ---


def _check_word_list(text: str, rules: list[SlopRule], category: str) -> list[SlopHit]:
    """对给定词表扫一遍文本，产出 hits。每个词命中 n 次算 n × weight 分。"""
    hits: list[SlopHit] = []
    for rule in rules:
        # 简单字面匹配（不用完整正则；AI slop 词通常就是确定字串）
        count = text.count(rule.pattern)
        if count > 0:
            hits.append(
                SlopHit(
                    category=category,
                    detail=rule.pattern,
                    count=count,
                    points=count * rule.weight,
                )
            )
    return hits


def _check_tier2_clusters(text: str, tier2_rules: list[SlopRule]) -> list[SlopHit]:
    """Tier 2：同一段出现 3+ 个才算命中。每段 +1 分（按 weight）。"""
    hits: list[SlopHit] = []
    patterns = [r.pattern for r in tier2_rules]
    weight = tier2_rules[0].weight if tier2_rules else 0.5

    for para_idx, para in enumerate(_split_paragraphs(text), 1):
        matches = []
        for p in patterns:
            c = para.count(p)
            if c:
                matches.extend([p] * c)
        if len(matches) >= TIER2_CLUSTER_THRESHOLD:
            hits.append(
                SlopHit(
                    category="tier2_cluster",
                    detail=f"段落 {para_idx} 聚集 {len(matches)} 个可疑词：{', '.join(set(matches))}",
                    count=1,
                    points=weight * len(matches),
                )
            )
    return hits


# --- 结构指标检查 ---


def _check_em_dash_density(text: str, total_chars: int) -> list[SlopHit]:
    count = text.count("——")
    if total_chars < MIN_STRUCTURAL_CHARS:
        return []
    per_1000 = _scale_per_1000(count, total_chars)
    if per_1000 > EM_DASH_PER_1000_THRESHOLD:
        over = per_1000 - EM_DASH_PER_1000_THRESHOLD
        points = min(2.0, over / EM_DASH_PER_1000_THRESHOLD)  # 每超一倍 +1 分，上限 2
        return [
            SlopHit(
                category="structural_em_dash",
                detail=f"破折号密度 {per_1000:.1f}/1000 字（阈值 {EM_DASH_PER_1000_THRESHOLD}）",
                count=count,
                points=points,
            )
        ]
    return []


def _check_sentence_length_variance(text: str) -> list[SlopHit]:
    sents = _split_sentences(text)
    if len(sents) < 5:
        return []
    lens = [_chinese_char_count(s) for s in sents]
    mean = statistics.mean(lens)
    if mean == 0:
        return []
    stdev = statistics.stdev(lens)
    cv = stdev / mean
    if cv < SENT_LEN_CV_THRESHOLD:
        return [
            SlopHit(
                category="structural_sentence_uniform",
                detail=f"句长变异系数 {cv:.2f}（< {SENT_LEN_CV_THRESHOLD} 说明句长太均匀）",
                count=len(sents),
                points=2.0 * (1 - cv / SENT_LEN_CV_THRESHOLD),
            )
        ]
    return []


def _check_paragraph_transition_ratio(text: str) -> list[SlopHit]:
    paras = _split_paragraphs(text)
    if len(paras) < 5:
        return []
    transition_count = 0
    transition_words: list[str] = []
    for p in paras:
        # 取每段开头最多 4 个字符
        head = p[:4]
        for tw in PARA_OPENER_TRANSITIONS:
            if head.startswith(tw):
                transition_count += 1
                transition_words.append(tw)
                break
    ratio = transition_count / len(paras)
    if ratio > PARA_TRANSITION_RATIO_THRESHOLD:
        return [
            SlopHit(
                category="structural_transition_opener",
                detail=f"{transition_count}/{len(paras)} 段以转场词开头（{ratio:.0%} > {PARA_TRANSITION_RATIO_THRESHOLD:.0%}）：{', '.join(set(transition_words))}",
                count=transition_count,
                points=2.0,
            )
        ]
    return []


_TRIADIC_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,4}[、，,]\s*[\u4e00-\u9fff]{1,4}[、，,]\s*[\u4e00-\u9fff]{1,4}"
)


def _check_triadic_listing(text: str, total_chars: int) -> list[SlopHit]:
    if total_chars < MIN_STRUCTURAL_CHARS:
        return []
    matches = _TRIADIC_PATTERN.findall(text)
    count = len(matches)
    per_1000 = _scale_per_1000(count, total_chars)
    if per_1000 > TRIADIC_PER_1000_THRESHOLD:
        samples = ", ".join(matches[:3])
        return [
            SlopHit(
                category="structural_triadic",
                detail=f"三并列密度 {per_1000:.1f}/1000 字（阈值 {TRIADIC_PER_1000_THRESHOLD}）。示例：{samples}",
                count=count,
                points=min(1.5, per_1000 / TRIADIC_PER_1000_THRESHOLD - 1),
            )
        ]
    return []


_NEGATIVE_REPEAT_PATTERN = re.compile(
    r"(没有?|不)[\u4e00-\u9fff]{1,8}[，。,.]\s*\1[\u4e00-\u9fff]{1,8}"
)


def _check_negative_repetition(text: str, total_chars: int) -> list[SlopHit]:
    matches = _NEGATIVE_REPEAT_PATTERN.findall(text)
    count = len(matches)
    if count >= 2:
        return [
            SlopHit(
                category="structural_negative_repeat",
                detail=f"'没...没...' / '不...不...' 结构出现 {count} 次",
                count=count,
                points=min(1.5, count * 0.5),
            )
        ]
    return []


_NOT_JUST_PATTERNS = [
    re.compile(r"不仅[\u4e00-\u9fff]{1,10}[，,]?\s*还"),
    re.compile(r"不是[\u4e00-\u9fff]{1,10}[，,]?\s*而是"),
    re.compile(r"既[\u4e00-\u9fff]{1,8}[，,]?\s*又"),
]


def _check_not_just_but(text: str, total_chars: int) -> list[SlopHit]:
    if total_chars < MIN_STRUCTURAL_CHARS:
        return []
    total = 0
    for p in _NOT_JUST_PATTERNS:
        total += len(p.findall(text))
    per_1000 = _scale_per_1000(total, total_chars)
    if per_1000 > NOT_JUST_PER_1000_THRESHOLD:
        return [
            SlopHit(
                category="structural_not_just_but",
                detail=f"'不仅/不是/既...' 对仗句密度 {per_1000:.1f}/1000 字（阈值 {NOT_JUST_PER_1000_THRESHOLD}）",
                count=total,
                points=min(1.5, per_1000 / NOT_JUST_PER_1000_THRESHOLD - 1),
            )
        ]
    return []


# ============================================================
# 主入口
# ============================================================


def scan(text: str, rules: RuleSet | None = None) -> SlopReport:
    """扫描一段中文文本，产出 slop 报告。

    流程：
    1. 词表匹配（Tier 1/2/3 + 场景/修辞/对白）
    2. 结构分析（破折号、句长、段首、三并列、否定重复、对仗句）
    3. 聚合分数（上限 10）
    """
    rules = rules or load_rules()
    total_chars = _chinese_char_count(text)
    paragraphs = _split_paragraphs(text)

    hits: list[SlopHit] = []

    # 词表层
    hits += _check_word_list(text, rules.by_tier("tier1_phrase"), "tier1_phrase")
    hits += _check_word_list(text, rules.by_tier("tier1_word"), "tier1_word")
    hits += _check_tier2_clusters(text, rules.by_tier("tier2"))
    hits += _check_word_list(text, rules.by_tier("tier3"), "tier3")
    hits += _check_word_list(text, rules.by_tier("scene"), "scene")
    hits += _check_word_list(text, rules.by_tier("rhetoric"), "rhetoric")
    hits += _check_word_list(text, rules.by_tier("dialogue"), "dialogue")

    # 结构层
    hits += _check_em_dash_density(text, total_chars)
    hits += _check_sentence_length_variance(text)
    hits += _check_paragraph_transition_ratio(text)
    hits += _check_triadic_listing(text, total_chars)
    hits += _check_negative_repetition(text, total_chars)
    hits += _check_not_just_but(text, total_chars)

    # 聚合
    raw_score = sum(h.points for h in hits)
    # 按字数归一化：越长的文本阈值越宽松
    # 最小基准 MIN_STRUCTURAL_CHARS，防止短文本少量命中被放大成高分
    basis = max(total_chars, MIN_STRUCTURAL_CHARS)
    normalized = raw_score * 1000.0 / basis if basis > 0 else 0.0
    score = min(SCORE_CAP, normalized)

    return SlopReport(
        score=score,
        hits=sorted(hits, key=lambda h: -h.points),
        stats={
            "chinese_chars": total_chars,
            "paragraphs": len(paragraphs),
            "sentences": len(_split_sentences(text)),
            "raw_score": raw_score,
        },
    )


def slop_score(text: str) -> float:
    """简化接口：只要分数。"""
    return scan(text).score

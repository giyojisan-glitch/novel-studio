"""Pydantic schema：所有层的 state 结构。"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


Genre = Literal["科幻", "悬疑", "武侠", "都市", "奇幻", "仙侠", "历史", "日轻", "志怪"]
Language = Literal["zh", "ja"]
Creativity = Literal["strict", "balanced", "creative"]
Layer = Literal["L1", "L2", "L3", "L4"]
AuditHead = Literal["logic", "pace", "style", "character"]


class UserInput(BaseModel):
    premise: str
    genre: Genre = "科幻"
    chapter_count: int = Field(3, ge=1, le=30)
    target_words_per_chapter: int = Field(1000, ge=300, le=3000)
    language: Language = "zh"
    # 创意档位：strict（严格按 premise）/ balanced（平衡）/ creative（大胆补全）
    # 影响 temperature + 每层 prompt 里的创意自由度指令
    creativity: Creativity = "balanced"
    # pipeline 版本：
    #   v1 = 原始（L3 全部写完 → finalize，L4 透传）
    #   v2 = V2 增强（L3 → final_audit → L4_adversarial → L4_scrubber → finalize）
    #   v3 = V3 长篇（interleaved L2_i → L3_i → bible_update_i，世界观知识库维护跨章一致性）
    pipeline_version: Literal["v1", "v2", "v3"] = "v1"


class CharacterCard(BaseModel):
    name: str
    traits: list[str]
    want: str                               # 外在目标（做什么）
    need: str                               # 内在成长（懂什么）
    # V2: Wound/Want/Need/Lie 框架（autonovel/CRAFT.md）
    wound: str = ""                         # 角色的心理创伤/过去经历
    lie: str = ""                           # 角色相信的谎言（need 的反面）


class ThreeAct(BaseModel):
    setup: str
    confrontation: str
    resolution: str


class L1Skeleton(BaseModel):
    title: str
    logline: str
    theme: str
    protagonist: CharacterCard
    antagonist: Optional[CharacterCard] = None
    three_act: ThreeAct
    world_rules: list[str]
    revision: int = 0


class L2ChapterOutline(BaseModel):
    index: int
    title: str
    summary: str
    hook: str
    pov: str
    key_events: list[str]
    prev_connection: str
    revision: int = 0
    # V2: 伏笔账本（autonovel 的 canon.md 思路）
    foreshadow_planted: list[str] = Field(default_factory=list)  # 本章新埋伏笔（需要在后续兑现）
    foreshadow_paid: list[str] = Field(default_factory=list)      # 本章兑现的前面章节的伏笔


class L3ChapterDraft(BaseModel):
    index: int
    content: str
    word_count: int
    revision: int = 0


class AdversarialCut(BaseModel):
    """对抗编辑一次切割的结果。"""
    category: Literal["FAT", "REDUNDANT", "OVER_EXPLAIN", "GENERIC", "TELL", "STRUCTURAL"]
    quoted_text: str          # 被建议切掉的原文片段
    reason: str               # 为什么要切


class L4PolishedChapter(BaseModel):
    """V2: 真实 L4 产出 = 原稿 + 对抗编辑反馈 + Scrubber 后成品。"""
    index: int
    content: str = ""                                       # 最终 scrubber 后的正文
    adversarial_cuts: list[AdversarialCut] = Field(default_factory=list)  # 对抗编辑产出的切割建议
    polish_notes: list[str] = Field(default_factory=list)                  # Scrubber 修改纪要
    revision: int = 0


class AuditReport(BaseModel):
    head: AuditHead
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AuditVerdict(BaseModel):
    layer: Layer
    target_index: Optional[int] = None
    reports: list[AuditReport]
    passed: bool
    retry_hint: str = ""


# ============ V3: 世界观知识库（长篇一致性） ============
class CharacterState(BaseModel):
    """角色状态快照——跨章节追踪的动态字段。"""
    name: str
    traits: list[str] = Field(default_factory=list)            # 稳定特质
    voice_markers: list[str] = Field(default_factory=list)     # 说话方式标记
    arc_state: str = ""                                        # 当前弧光阶段（"怀疑期"/"觉醒期"）
    last_appeared_in: int = 0                                  # 最后出场章节（0=未出场）
    notable_events: list[str] = Field(default_factory=list)    # 已发生的关键事件


class WorldFact(BaseModel):
    """世界观事实条目——任何后续不能推翻的设定。"""
    category: Literal["rule", "location", "item", "relationship", "event"]
    content: str                                               # ≤60 字
    ch_introduced: int                                         # 引入章节


class WorldBible(BaseModel):
    """V3 长篇专用：跨章节的真相账本。

    - L1 产出后，bible_init 用 L1 数据初始化（角色+规则）
    - 每章 L3 写完，bible_update_i 增量更新
    - L2_{i+1} 和 L3_{i+1} 读 bible 获取跨章上下文
    """
    characters: list[CharacterState] = Field(default_factory=list)
    facts: list[WorldFact] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)            # 按时序的大事记
    active_foreshadow: list[str] = Field(default_factory=list)   # 已埋未兑现的伏笔
    paid_foreshadow: list[str] = Field(default_factory=list)     # 已兑现的伏笔
    last_updated_ch: int = 0                                     # 最后一次 bible_update 覆盖到的章节


class BibleUpdate(BaseModel):
    """单次 bible_update 的产出：增量修改指令，而非整份 bible 重写。"""
    chapter_index: int
    new_characters: list[CharacterState] = Field(default_factory=list)
    character_updates: list[CharacterState] = Field(default_factory=list)  # 已存在角色的状态更新
    new_facts: list[WorldFact] = Field(default_factory=list)
    timeline_additions: list[str] = Field(default_factory=list)
    new_foreshadow: list[str] = Field(default_factory=list)      # 本章新埋的
    paid_foreshadow: list[str] = Field(default_factory=list)     # 本章兑现的（要从 active 移除）
    consistency_issues: list[str] = Field(default_factory=list)  # 与 bible 矛盾的地方（写给下次 L3 retry 的）


class FinalVerdict(BaseModel):
    """V2: 成品审——对照 premise 原文审整本书，能抓跨层 bug（时间线/伏笔/角色坍塌）。"""
    usable: bool
    overall_score: float = Field(ge=0.0, le=1.0)
    symptoms: list[str] = Field(default_factory=list)       # 具体症状：「时间线矛盾」「伏笔 X 未兑现」等
    suspect_layer: Literal["premise", "L1", "L2", "L3", "L4", "none"] = "none"  # 推断问题源自哪一层
    retry_hint: str = ""                                     # 打回时给该层的定向反馈
    # 机械检查附加
    slop_avg: float = 0.0                                    # 各章 slop 平均分


class NovelState(BaseModel):
    """全局 state，持久化到 projects/{slug}/state.json。"""
    user_input: UserInput
    l1: Optional[L1Skeleton] = None
    l2: list[L2ChapterOutline] = Field(default_factory=list)
    l3: list[L3ChapterDraft] = Field(default_factory=list)
    l4: list[L4PolishedChapter] = Field(default_factory=list)

    current_l2_idx: int = 0
    current_l3_idx: int = 0
    current_l4_idx: int = 0                                  # V2: L4 逐章推进

    audit_history: list[AuditVerdict] = Field(default_factory=list)
    final_verdict: Optional[FinalVerdict] = None             # V2: 成品审结果
    final_bounce_count: int = 0                              # V2/V3: 成品审被打回次数（防死循环，>=2 强制放行）
    cross_chapter_notes: list[str] = Field(default_factory=list)  # 通用跨章备忘（legacy 字段，保留兼容）

    # V3: 长篇专用——世界观知识库 + bible_update 指针
    world_bible: Optional[WorldBible] = None
    current_bible_update_idx: int = 0                        # 下一个要跑 bible_update 的章节（v3）

    next_step: str = "L1"
    completed: bool = False
    final_markdown: str = ""
    trace: list[dict] = Field(default_factory=list)

    def last_audit(self, step_key: str) -> Optional[AuditVerdict]:
        for v in reversed(self.audit_history):
            if _verdict_key(v) == step_key:
                return v
        return None


def _verdict_key(v: AuditVerdict) -> str:
    return f"{v.layer}" if v.target_index is None else f"{v.layer}_{v.target_index}"

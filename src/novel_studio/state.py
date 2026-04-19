"""Pydantic schema：所有层的 state 结构。"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


Genre = Literal["科幻", "悬疑", "武侠", "都市", "奇幻", "仙侠"]
Layer = Literal["L1", "L2", "L3", "L4"]
AuditHead = Literal["logic", "pace", "style", "character"]


class UserInput(BaseModel):
    premise: str
    genre: Genre = "科幻"
    chapter_count: int = Field(3, ge=1, le=10)
    target_words_per_chapter: int = Field(1000, ge=300, le=3000)
    language: str = "zh"
    # V2: pipeline 版本 —— v1 维持旧行为（L3 → finalize，L4 透传）
    #                    v2 启用新管道（L3 → final_audit → L4_adversarial → L4_scrubber → finalize）
    pipeline_version: Literal["v1", "v2"] = "v1"


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
    cross_chapter_notes: list[str] = Field(default_factory=list)  # V3 预留

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

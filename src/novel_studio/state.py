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


class CharacterCard(BaseModel):
    name: str
    traits: list[str]
    want: str
    need: str


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


class L3ChapterDraft(BaseModel):
    index: int
    content: str
    word_count: int
    revision: int = 0


class L4PolishedChapter(BaseModel):
    index: int
    content: str = ""
    polish_notes: list[str] = Field(default_factory=list)


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


class NovelState(BaseModel):
    """全局 state，持久化到 projects/{slug}/state.json。"""
    user_input: UserInput
    l1: Optional[L1Skeleton] = None
    l2: list[L2ChapterOutline] = Field(default_factory=list)
    l3: list[L3ChapterDraft] = Field(default_factory=list)
    l4: list[L4PolishedChapter] = Field(default_factory=list)

    current_l2_idx: int = 0
    current_l3_idx: int = 0

    audit_history: list[AuditVerdict] = Field(default_factory=list)
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

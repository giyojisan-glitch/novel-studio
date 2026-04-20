"""Inspiration RAG schemas."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


# 一个 chunk 可以按"用途"打标签——之后支持分层检索
# MVP 阶段允许留空（auto），后续可加自动分类
ChunkCategory = Literal[
    "unclassified",
    "scene",          # 场景描写（物件、环境、空镜）
    "dialogue",       # 对白段落
    "action",         # 动作段落
    "interior",       # 内心独白（少用 —— 志怪类慎入）
    "landscape",      # 景物/空间
    "opening",        # 段落开头（供学习如何起笔）
    "closing",        # 段落结尾（供学习如何收束）
    "transition",     # 过渡段
]


class InspirationChunk(BaseModel):
    """灵感库里的一个片段——从原文切出的可检索单元。"""
    chunk_id: str              # 唯一 ID：{author}__{work}__{index}
    author: str                # 作家标识（"汪曾祺"）
    work: str                  # 作品标识（"受戒"）
    text: str                  # chunk 原文（100-500 字）
    category: ChunkCategory = "unclassified"
    position: int              # 在原作中的序号（第 N 段）
    total_chunks: int          # 该作品总共切了多少块
    chinese_chars: int = 0     # 中文字数（用于过滤太短 chunk）

    def short_label(self) -> str:
        """给 prompt 注入时用的短标签。"""
        return f"【{self.author}·{self.work}】"


class StyleQuery(BaseModel):
    """检索请求——传给 retriever。"""
    query_text: str            # 要检索的参考语境（通常是 L2 summary + hook）
    top_k: int = 5             # 拿回多少个片段
    authors: list[str] | None = None        # 可选：只从某几位作家检索
    works: list[str] | None = None          # 可选：只从某几部作品检索
    categories: list[ChunkCategory] | None = None  # 可选：只要特定用途的片段
    min_chinese_chars: int = 60  # 过滤过短的片段
    max_chinese_chars: int = 500  # 过滤过长的片段

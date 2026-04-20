"""Inspiration library — Lora-style style transfer for fiction.

最初 vision 里的核心组件：把喜欢的作家作品喂进去，AI 消化成向量库，
生成时动态检索作参考，实现"像 Lora 一样"的风格迁移。

V2 路线图的 Phase 1：
- ingester.py: 原文 → chunk → embedding → Chroma
- retriever.py: query → top-K 相关片段
- L3 prompt 注入：让 AI 写新段落时看到 few-shot 参考片段

目录约定：
    inspirations/
    ├── 汪曾祺/
    │   ├── 受戒.txt
    │   └── 大淖记事.txt
    ├── 蒲松龄/
    │   └── 聊斋_聂小倩.txt
    └── ...
"""
from .schemas import InspirationChunk, StyleQuery, ChunkCategory

__all__ = ["InspirationChunk", "StyleQuery", "ChunkCategory"]

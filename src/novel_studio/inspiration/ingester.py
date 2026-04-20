"""Ingester：读 inspirations/{作家}/*.txt → chunk → embedding → Chroma。

设计要点：
- chunk 策略：按段落（空行分割），短段合并到 100 字以上，长段 split 到 500 字以下
- embedding：中文优先 BAAI/bge-large-zh-v1.5（首次会下载 ~1GB 模型）
- 存储：chroma_db/ 本地 persistent Collection
- 增量：已 ingested 的文件跳过（以 chunk_id 唯一性判重）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .schemas import InspirationChunk


INSPIRATIONS_ROOT = Path(__file__).parent.parent.parent.parent / "inspirations"
CHROMA_ROOT = Path(__file__).parent.parent.parent.parent / "chroma_db"
COLLECTION_NAME = "inspirations"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-zh-v1.5"

# Chunk 大小约束
MIN_CHUNK_CHARS = 60
MAX_CHUNK_CHARS = 500
IDEAL_CHUNK_CHARS = 250


def _chinese_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def split_into_chunks(text: str) -> list[str]:
    """按段落切，短段合并，长段拆分——目标 60-500 字的可检索 chunk。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    buffer = ""

    for para in paragraphs:
        para_len = _chinese_chars(para)

        # 段落本身就太长 → 按句号硬切
        if para_len > MAX_CHUNK_CHARS:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            # 按中文句号/问号/感叹号切
            sentences = re.split(r"(?<=[。！？])", para)
            current = ""
            for s in sentences:
                if _chinese_chars(current) + _chinese_chars(s) > IDEAL_CHUNK_CHARS:
                    if _chinese_chars(current) >= MIN_CHUNK_CHARS:
                        chunks.append(current.strip())
                    current = s
                else:
                    current += s
            if current.strip():
                if _chinese_chars(current) >= MIN_CHUNK_CHARS:
                    chunks.append(current.strip())
            continue

        # 正常段落：累积到 buffer 里
        if _chinese_chars(buffer) + para_len < IDEAL_CHUNK_CHARS:
            buffer = buffer + "\n\n" + para if buffer else para
        else:
            if buffer:
                chunks.append(buffer)
            buffer = para

    if buffer and _chinese_chars(buffer) >= MIN_CHUNK_CHARS:
        chunks.append(buffer)

    return chunks


def _iter_source_files() -> Iterable[Path]:
    if not INSPIRATIONS_ROOT.exists():
        return
    for author_dir in INSPIRATIONS_ROOT.iterdir():
        if not author_dir.is_dir():
            continue
        if author_dir.name.startswith("_") or author_dir.name.startswith("."):
            continue
        for f in author_dir.glob("*.txt"):
            yield f


def ingest_all(
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    rebuild: bool = False,
) -> dict:
    """扫描 inspirations/，切 chunk，embed，存 Chroma。

    返回统计字典：{authors, works, chunks, skipped}
    """
    import chromadb
    from sentence_transformers import SentenceTransformer

    CHROMA_ROOT.mkdir(exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_ROOT))

    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"embedding_model": model_name},
    )

    # 已有 chunk_id 跳过
    existing_ids: set[str] = set()
    if collection.count() > 0:
        # chromadb 没有直接"list all ids"的 API，借用 get()
        existing = collection.get(include=[])
        existing_ids = set(existing.get("ids", []))

    print(f"[ingest] 模型：{model_name}")
    print(f"[ingest] 现有 chunks：{len(existing_ids)}")

    model = SentenceTransformer(model_name)

    authors: set[str] = set()
    works: set[str] = set()
    new_chunk_count = 0
    skipped_count = 0

    for file_path in _iter_source_files():
        author = file_path.parent.name
        work = file_path.stem
        authors.add(author)
        works.add(f"{author}/{work}")

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        chunks = split_into_chunks(text)
        total = len(chunks)

        print(f"[ingest] {author}/{work}: {total} chunks")

        batch_ids = []
        batch_texts = []
        batch_metadatas = []

        for i, chunk_text in enumerate(chunks):
            chunk_id = f"{author}__{work}__{i}"
            if chunk_id in existing_ids:
                skipped_count += 1
                continue

            batch_ids.append(chunk_id)
            batch_texts.append(chunk_text)
            batch_metadatas.append({
                "author": author,
                "work": work,
                "position": i,
                "total_chunks": total,
                "category": "unclassified",
                "chinese_chars": _chinese_chars(chunk_text),
            })

        if not batch_ids:
            continue

        # 批量 embed
        embeddings = model.encode(batch_texts, show_progress_bar=False, normalize_embeddings=True).tolist()

        collection.add(
            ids=batch_ids,
            documents=batch_texts,
            metadatas=batch_metadatas,
            embeddings=embeddings,
        )
        new_chunk_count += len(batch_ids)

    return {
        "authors": sorted(authors),
        "works": sorted(works),
        "new_chunks": new_chunk_count,
        "skipped": skipped_count,
        "total_in_db": collection.count(),
    }

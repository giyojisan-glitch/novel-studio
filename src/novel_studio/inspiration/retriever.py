"""Retriever：query → Chroma → top-K 相关 InspirationChunk。"""
from __future__ import annotations

from typing import Optional

from .ingester import CHROMA_ROOT, COLLECTION_NAME, DEFAULT_EMBEDDING_MODEL
from .schemas import InspirationChunk, StyleQuery


class InspirationRetriever:
    """单例风格的检索器——懒加载 model + collection。"""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None
        self._collection = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def collection(self):
        if self._collection is None:
            import chromadb
            client = chromadb.PersistentClient(path=str(CHROMA_ROOT))
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"embedding_model": self.model_name},
            )
        return self._collection

    def retrieve(self, query: StyleQuery) -> list[InspirationChunk]:
        """执行检索。"""
        q_emb = self.model.encode(
            [query.query_text],
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

        # 构造 Chroma where 过滤
        where: dict = {}
        clauses: list[dict] = []
        if query.authors:
            if len(query.authors) == 1:
                clauses.append({"author": query.authors[0]})
            else:
                clauses.append({"author": {"$in": query.authors}})
        if query.works:
            if len(query.works) == 1:
                clauses.append({"work": query.works[0]})
            else:
                clauses.append({"work": {"$in": query.works}})
        if query.categories:
            if len(query.categories) == 1:
                clauses.append({"category": query.categories[0]})
            else:
                clauses.append({"category": {"$in": query.categories}})
        clauses.append({"chinese_chars": {"$gte": query.min_chinese_chars}})
        clauses.append({"chinese_chars": {"$lte": query.max_chinese_chars}})

        if len(clauses) == 1:
            where = clauses[0]
        elif len(clauses) > 1:
            where = {"$and": clauses}

        results = self.collection.query(
            query_embeddings=q_emb,
            n_results=query.top_k,
            where=where if where else None,
        )

        chunks: list[InspirationChunk] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        for cid, doc, meta in zip(ids, docs, metas):
            chunks.append(InspirationChunk(
                chunk_id=cid,
                author=meta.get("author", ""),
                work=meta.get("work", ""),
                text=doc,
                category=meta.get("category", "unclassified"),
                position=meta.get("position", 0),
                total_chunks=meta.get("total_chunks", 1),
                chinese_chars=meta.get("chinese_chars", 0),
            ))
        return chunks


# 模块级单例（懒加载）
_default_retriever: Optional[InspirationRetriever] = None


def get_retriever() -> InspirationRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = InspirationRetriever()
    return _default_retriever

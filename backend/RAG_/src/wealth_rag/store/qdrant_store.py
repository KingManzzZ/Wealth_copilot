"""Qdrant 本地向量库封装（零部署本地模式；后续可无缝切换服务模式）。

- 命名向量：``dense``（Qwen3-Embedding，余弦）+ ``sparse``（jieba 词频，IDF 修正，可选）
- 检索：Query API；qdrant_sparse 模式下使用原生 ``prefetch`` + ``RRF`` 融合
- 过滤：payload 过滤（doc_id / 文档类型）
"""

from __future__ import annotations

import atexit
import shutil
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient, models

from ..config import Settings
from ..models import Chunk

COLLECTION = "chunks"

# 本地模式通过文件锁独占数据库目录：同一进程内多个 QdrantStore 必须共享同一个客户端，
# 否则「先摄入、后检索」等组合用法会因锁冲突而报错。
_CLIENTS: dict[str, QdrantClient] = {}


def _close_all_clients() -> None:
    for key in list(_CLIENTS):
        try:
            _CLIENTS.pop(key).close()
        except Exception:
            pass


atexit.register(_close_all_clients)


class QdrantStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.collection = COLLECTION
        self.sparse_enabled = settings.sparse.method == "qdrant_sparse"
        self._key = str((settings.paths.db_dir / "qdrant").resolve())
        client = _CLIENTS.get(self._key)
        if client is None:
            client = QdrantClient(path=self._key)
            _CLIENTS[self._key] = client
        self.client = client

    def close(self) -> None:
        """释放本地库文件锁（同进程共享客户端时由 atexit 统一处理）。"""
        if _CLIENTS.get(self._key) is self.client:
            _CLIENTS.pop(self._key, None)
            try:
                self.client.close()
            except Exception:
                pass

    # ==================================================================
    # 建库 / 重建
    # ==================================================================
    def ensure(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        vectors = {
            "dense": models.VectorParams(
                size=self.settings.embedding.dim, distance=models.Distance.COSINE
            )
        }
        sparse_vectors = None
        if self.sparse_enabled:
            sparse_vectors = {
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            }
        self.client.create_collection(
            self.collection, vectors_config=vectors, sparse_vectors_config=sparse_vectors
        )

    def recreate(self) -> None:
        """彻底清空本地库并重建。

        不能直接调用 ``client.delete_collection``：Windows 下该集合的 ``storage.sqlite``
        仍被 sqlite 连接占用，qdrant 内部用 ``rmtree(..., ignore_errors=True)`` 会静默失败，
        重建集合时旧数据被重新加载。这里先关闭客户端（释放文件句柄与目录锁），删除整个
        持久化目录，再重新打开并建集合。
        """
        self.close()
        qdrant_dir = Path(self._key)
        if qdrant_dir.exists():
            shutil.rmtree(qdrant_dir)  # 不吞异常：残留即视为失败，避免旧库静默复用
        self.client = QdrantClient(path=self._key)
        _CLIENTS[self._key] = self.client
        self.ensure()

    # ==================================================================
    # 写入
    # ==================================================================
    def delete_doc(self, doc_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
        )

    def upsert(
        self,
        chunks: list[Chunk],
        dense_vecs: np.ndarray,
        sparse_vecs: list[tuple[list[int], list[float]]] | None = None,
    ) -> None:
        """写入子块（带向量）+ 父块（无向量）。

        【P2-2】父块按 ``parent_id`` 去重后**只存一次**，且不带任何向量——
        这样它既不会出现在向量/稀疏检索结果里，也不会被同一节的 N 个子块
        各复制一份。子块 payload 只保留 ``parent_id`` 引用。
        """
        points: list[models.PointStruct] = []
        parents: dict[str, dict] = {}
        for i, chunk in enumerate(chunks):
            vector: dict = {"dense": dense_vecs[i].tolist()}
            if self.sparse_enabled and sparse_vecs is not None:
                indices, values = sparse_vecs[i]
                if indices:
                    vector["sparse"] = models.SparseVector(indices=indices, values=values)
            points.append(
                models.PointStruct(id=chunk.id, vector=vector, payload=chunk.to_payload())
            )
            if chunk.parent_id and chunk.parent_id not in parents:
                payload = chunk.parent_payload()
                if payload is not None:
                    parents[chunk.parent_id] = payload

        batch = 256
        for i in range(0, len(points), batch):
            self.client.upsert(self.collection, points=points[i : i + batch])
        parent_points = [
            models.PointStruct(id=pid, vector={}, payload=pl) for pid, pl in parents.items()
        ]
        for i in range(0, len(parent_points), batch):
            self.client.upsert(self.collection, points=parent_points[i : i + batch])

    # ==================================================================
    # 检索
    # ==================================================================
    @staticmethod
    def _filter(doc_id: str | None) -> models.Filter | None:
        if not doc_id:
            return None
        return models.Filter(
            must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
        )

    def dense_search(
        self, vector: np.ndarray, limit: int, doc_id: str | None = None
    ) -> list[tuple[str, dict, float]]:
        resp = self.client.query_points(
            collection_name=self.collection,
            query=vector.tolist(),
            using="dense",
            limit=limit,
            query_filter=self._filter(doc_id),
            with_payload=True,
        )
        return [(p.id, p.payload or {}, p.score) for p in resp.points]

    def hybrid_search(
        self,
        dense_vector: np.ndarray,
        sparse_vector: tuple[list[int], list[float]],
        dense_limit: int,
        sparse_limit: int,
        limit: int,
        doc_id: str | None = None,
    ) -> list[tuple[str, dict, float]]:
        """Qdrant 原生混合检索：dense + sparse 双 prefetch → RRF 融合。"""
        indices, values = sparse_vector
        if not indices:
            return self.dense_search(dense_vector, limit, doc_id)
        resp = self.client.query_points(
            collection_name=self.collection,
            prefetch=[
                models.Prefetch(query=dense_vector.tolist(), using="dense", limit=dense_limit),
                models.Prefetch(
                    query=models.SparseVector(indices=indices, values=values),
                    using="sparse",
                    limit=sparse_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            query_filter=self._filter(doc_id),
            with_payload=True,
        )
        return [(p.id, p.payload or {}, p.score) for p in resp.points]

    def get_payloads(self, ids: list[str]) -> dict[str, dict]:
        records = self.client.retrieve(self.collection, ids=ids, with_payload=True)
        return {r.id: (r.payload or {}) for r in records}

    def get_parents(self, ids: list[str]) -> dict[str, str]:
        """【P2-2】按 parent_id 批量回查父块全文（命中子块后做上下文扩展）。"""
        if not ids:
            return {}
        records = self.client.retrieve(self.collection, ids=ids, with_payload=True)
        return {
            r.id: (r.payload or {}).get("raw_text", "")
            for r in records
            if (r.payload or {}).get("chunk_type") == "parent"
        }

    # ==================================================================
    # 遍历 / 统计
    # ==================================================================
    #: 【P2-2】排除父块：父块是无向量的辅助点，既不该进本地 BM25 语料，
    #: 也不该被计入「切块数」。
    _NOT_PARENT = models.Filter(
        must_not=[models.FieldCondition(key="chunk_type", match=models.MatchValue(value="parent"))]
    )

    def scroll_texts(self, batch: int = 512) -> tuple[list[str], list[str]]:
        """全量遍历 (ids, texts)，用于本地 BM25 索引重建（不含父块）。"""
        ids: list[str] = []
        texts: list[str] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                limit=batch,
                offset=offset,
                scroll_filter=self._NOT_PARENT,
                with_payload=["text"],
                with_vectors=False,
            )
            for p in points:
                ids.append(p.id)
                texts.append((p.payload or {}).get("text", ""))
            if offset is None:
                break
        return ids, texts

    def stats(self) -> dict:
        """知识库统计（切块数**不含**父块点）。"""
        chunks = self.client.count(
            self.collection, count_filter=self._NOT_PARENT, exact=True
        ).count
        doc_ids: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                limit=512,
                offset=offset,
                with_payload=["doc_id"],
                with_vectors=False,
            )
            for p in points:
                doc_ids.add((p.payload or {}).get("doc_id", ""))
            if offset is None:
                break
        doc_ids.discard("")
        return {"chunks": chunks, "docs": len(doc_ids), "doc_ids": sorted(doc_ids)}
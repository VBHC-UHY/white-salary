"""
white_salary/core/services/vector_search.py

向量搜索服务 — 基于SiliconFlow BAAI/bge-m3的语义相似度搜索。

借鉴v2的vector_search.py：
  - 调SiliconFlow API获取文本embedding
  - 余弦相似度计算
  - 本地缓存避免重复调API
  - Top-K检索
  - 接入记忆检索流程（先关键词初筛，再向量重排序）

API: https://api.siliconflow.cn/v1/embeddings
模型: BAAI/bge-m3
"""

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger


@dataclass
class SearchResult:
    """搜索结果。"""
    content: str
    score: float          # 相似度 0-1
    entry_id: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class VectorSearchService:
    """
    向量搜索服务。

    使用方式:
        vs = VectorSearchService(api_key="sk-xxx")
        vs.add_text("m1", "今天吃了蛋糕")
        vs.add_text("m2", "明天要考试")
        results = await vs.search("甜点", top_k=5)
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "BAAI/bge-m3",
        cache_dir: str = "data/memory",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

        # 缓存
        self._cache_path = Path(cache_dir) / "vector_cache.json"
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, list[float]] = {}  # text_hash → embedding
        self._load_cache()

        # 文档存储
        self._documents: dict[str, dict] = {}  # entry_id → {"content": str, "embedding": list}

        # 请求限制
        self._last_request_time = 0.0
        self._min_interval = 0.1  # 100ms最小间隔

    # ================================================================
    # 文档管理
    # ================================================================

    def add_text(self, entry_id: str, content: str, embedding: list[float] = None) -> None:
        """添加文档（同步，embedding可预计算）。"""
        self._documents[entry_id] = {
            "content": content,
            "embedding": embedding,
        }

    def remove_text(self, entry_id: str) -> None:
        """删除文档。"""
        self._documents.pop(entry_id, None)

    def add_texts_batch(self, entries: list[tuple[str, str]]) -> None:
        """批量添加文档。"""
        for entry_id, content in entries:
            self.add_text(entry_id, content)

    # ================================================================
    # 搜索
    # ================================================================

    async def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.5,
    ) -> list[SearchResult]:
        """
        语义搜索。

        Args:
            query: 查询文本
            top_k: 返回最多K个结果
            threshold: 最低相似度阈值

        Returns:
            SearchResult列表，按相似度降序
        """
        if not self._documents:
            return []

        # 获取查询的embedding
        query_emb = await self._get_embedding(query)
        if not query_emb:
            return []

        # 计算与所有文档的相似度
        scores = []
        for entry_id, doc in self._documents.items():
            doc_emb = doc.get("embedding")
            if not doc_emb:
                # 还没embedding的文档，尝试获取
                doc_emb = await self._get_embedding(doc["content"])
                if doc_emb:
                    doc["embedding"] = doc_emb
                else:
                    continue

            sim = self._cosine_similarity(query_emb, doc_emb)
            if sim >= threshold:
                scores.append(SearchResult(
                    content=doc["content"],
                    score=sim,
                    entry_id=entry_id,
                ))

        # 排序
        scores.sort(key=lambda r: r.score, reverse=True)
        return scores[:top_k]

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],  # [(entry_id, content), ...]
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        重排序 — 对已有候选结果按语义相似度重新排序。

        用于：先关键词初筛，再向量重排序。

        Returns:
            [(entry_id, score), ...] 按分数降序
        """
        if not candidates:
            return []

        query_emb = await self._get_embedding(query)
        if not query_emb:
            # 无法获取embedding，返回原始顺序
            return [(eid, 1.0) for eid, _ in candidates]

        scored = []
        for entry_id, content in candidates:
            emb = await self._get_embedding(content)
            if emb:
                sim = self._cosine_similarity(query_emb, emb)
                scored.append((entry_id, sim))
            else:
                scored.append((entry_id, 0.0))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ================================================================
    # Embedding API
    # ================================================================

    async def _get_embedding(self, text: str) -> Optional[list[float]]:
        """获取文本的embedding向量（带缓存）。"""
        if not text:
            return None

        # 截断过长文本
        text = text[:500]

        # 检查缓存
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in self._cache:
            return self._cache[text_hash]

        # 调API
        if not self._api_key:
            return self._fallback_embedding(text)

        try:
            # 限流
            now = time.time()
            if now - self._last_request_time < self._min_interval:
                import asyncio
                await asyncio.sleep(self._min_interval)
            self._last_request_time = time.time()

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": text,
                        "encoding_format": "float",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                embedding = data["data"][0]["embedding"]

                # 缓存
                self._cache[text_hash] = embedding
                self._save_cache_debounced()
                return embedding

        except Exception as e:
            logger.debug(f"[VectorSearch] embedding API失败: {e}")
            return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> list[float]:
        """
        本地回退embedding — 简单的字符级hash向量。
        不精确，但保证基本可用。
        """
        dim = 64
        vec = [0.0] * dim
        for i, ch in enumerate(text):
            idx = ord(ch) % dim
            vec[idx] += 1.0
        # 归一化
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    # ================================================================
    # 相似度计算
    # ================================================================

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """余弦相似度。"""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
        norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (norm_a * norm_b)

    # ================================================================
    # 缓存持久化
    # ================================================================

    _cache_save_counter = 0

    def _save_cache_debounced(self) -> None:
        self._cache_save_counter += 1
        if self._cache_save_counter % 10 == 0:
            self._save_cache()

    def _save_cache(self) -> None:
        try:
            # 只保存最近1000条缓存
            if len(self._cache) > 1000:
                keys = list(self._cache.keys())[-1000:]
                self._cache = {k: self._cache[k] for k in keys}
            self._cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                self._cache = json.loads(
                    self._cache_path.read_text(encoding="utf-8")
                )
                logger.debug(f"[VectorSearch] 缓存加载: {len(self._cache)}条")
            except Exception:
                pass

    def force_save_cache(self) -> None:
        self._save_cache()

    @property
    def stats(self) -> dict:
        return {
            "documents": len(self._documents),
            "cached_embeddings": len(self._cache),
            "model": self._model,
            "has_api_key": bool(self._api_key),
        }

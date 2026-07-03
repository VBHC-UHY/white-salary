"""
white_salary/core/bilibili_learning.py

B站学习系统 — 追踪点赞/评论/UP主关系。

借鉴v2的bilibili/learning.py（936行），简化版：
  - 记录点赞过的视频（分区+标签+UP主）
  - 记录评论过的视频（评论内容+效果）
  - UP主关系追踪（互动次数+偏好标签）
  - 视频内容缓存（标题+分区+摘要）

数据存在data/bilibili/目录下。
"""

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger


class BiliLearningManager:
    """B站学习管理器（单例）。"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, data_dir: str = "data/bilibili") -> None:
        if self._initialized:
            return
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._likes: list[dict] = []        # 点赞记录
        self._comments: list[dict] = []     # 评论记录
        self._up_relations: dict = {}       # UP主关系
        self._video_cache: dict = {}        # 视频缓存

        self._load_all()
        self._initialized = True

    # ================================================================
    # 记录
    # ================================================================

    def record_like(self, bvid: str, title: str = "", author: str = "",
                    tags: list[str] = None) -> None:
        """记录一次点赞。"""
        self._likes.append({
            "bvid": bvid, "title": title, "author": author,
            "tags": tags or [], "time": time.strftime("%Y-%m-%d %H:%M"),
        })
        if len(self._likes) > 200:
            self._likes = self._likes[-200:]
        # 更新UP主关系
        if author:
            self._update_up_relation(author, "like", tags)
        self._save("likes")

    def record_comment(self, bvid: str, title: str = "", author: str = "",
                       comment: str = "") -> None:
        """记录一次评论。"""
        self._comments.append({
            "bvid": bvid, "title": title, "author": author,
            "comment": comment[:100], "time": time.strftime("%Y-%m-%d %H:%M"),
        })
        if len(self._comments) > 100:
            self._comments = self._comments[-100:]
        if author:
            self._update_up_relation(author, "comment")
        self._save("comments")

    def cache_video(self, bvid: str, title: str = "", author: str = "",
                    tname: str = "", desc: str = "") -> None:
        """缓存视频信息。"""
        self._video_cache[bvid] = {
            "title": title, "author": author, "tname": tname,
            "desc": desc[:200], "cached_at": time.strftime("%Y-%m-%d"),
        }
        if len(self._video_cache) > 500:
            # 删最旧的
            keys = sorted(self._video_cache, key=lambda k: self._video_cache[k].get("cached_at", ""))
            for k in keys[:100]:
                del self._video_cache[k]
        self._save("cache")

    def _update_up_relation(self, author: str, action: str,
                            tags: list[str] = None) -> None:
        """更新UP主关系。"""
        if author not in self._up_relations:
            self._up_relations[author] = {
                "like_count": 0, "comment_count": 0, "share_count": 0,
                "common_tags": [], "first_interact": time.strftime("%Y-%m-%d"),
            }
        rel = self._up_relations[author]
        if action == "like":
            rel["like_count"] += 1
        elif action == "comment":
            rel["comment_count"] += 1
        elif action == "share":
            rel["share_count"] += 1
        if tags:
            for t in tags:
                if t not in rel["common_tags"]:
                    rel["common_tags"].append(t)
            rel["common_tags"] = rel["common_tags"][:20]
        self._save("up_relations")

    # ================================================================
    # 查询
    # ================================================================

    def get_favorite_ups(self, limit: int = 10) -> list[dict]:
        """获取最喜欢的UP主。"""
        ups = []
        for name, rel in self._up_relations.items():
            score = rel["like_count"] * 2 + rel["comment_count"] * 3 + rel["share_count"]
            ups.append({"name": name, "score": score, **rel})
        ups.sort(key=lambda x: x["score"], reverse=True)
        return ups[:limit]

    def get_favorite_tags(self, limit: int = 10) -> list[str]:
        """获取最常出现的标签。"""
        from collections import Counter
        all_tags = []
        for like in self._likes:
            all_tags.extend(like.get("tags", []))
        return [t for t, _ in Counter(all_tags).most_common(limit)]

    @property
    def stats(self) -> dict:
        return {
            "likes": len(self._likes),
            "comments": len(self._comments),
            "ups_tracked": len(self._up_relations),
            "videos_cached": len(self._video_cache),
        }

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self, which: str = "") -> None:
        try:
            if which in ("likes", ""):
                (self._data_dir / "likes.json").write_text(
                    json.dumps(self._likes, ensure_ascii=False, indent=2), encoding="utf-8")
            if which in ("comments", ""):
                (self._data_dir / "comments.json").write_text(
                    json.dumps(self._comments, ensure_ascii=False, indent=2), encoding="utf-8")
            if which in ("up_relations", ""):
                (self._data_dir / "up_relations.json").write_text(
                    json.dumps(self._up_relations, ensure_ascii=False, indent=2), encoding="utf-8")
            if which in ("cache", ""):
                (self._data_dir / "video_cache.json").write_text(
                    json.dumps(self._video_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_all(self) -> None:
        for name, attr in [("likes", "_likes"), ("comments", "_comments"),
                           ("up_relations", "_up_relations"), ("video_cache", "_video_cache")]:
            path = self._data_dir / f"{name}.json"
            if path.exists():
                try:
                    setattr(self, attr, json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    pass

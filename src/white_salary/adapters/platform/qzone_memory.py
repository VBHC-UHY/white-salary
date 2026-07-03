"""
white_salary/adapters/platform/qzone_memory.py

QQ空间记忆 — 记录AI在QQ空间的动态和互动。

借鉴v2的qzone_memory.py：
  - v2的记录格式和搜索功能，保留
  - v2的context注入（生成LLM可读的摘要），保留
  - v2存JSON，我们也用JSON
  - v2保留100条说说+200条评论，合理

功能：
  - 记录发表的说说（内容、时间、是否有图）
  - 记录回复的评论（目标、内容、时间）
  - 生成记忆摘要注入LLM上下文
  - 关键词搜索历史内容
"""

import json
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger


class QzoneMemory:
    """
    QQ空间记忆管理。

    使用方式:
        qm = QzoneMemory(data_dir="data/memory")
        qm.add_post("今天天气真好", tid="123", has_image=True)
        qm.add_comment("小白", "你在吗", "在呢～", tid="456")
        summary = qm.get_summary()
    """

    MAX_POSTS = 100
    MAX_COMMENTS = 200

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._path = Path(data_dir) / "qzone_memory.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 线程安全
        self._posts: list[dict] = []
        self._comments: list[dict] = []
        self._load()

    def add_post(self, content: str, tid: str = "", has_image: bool = False) -> None:
        """记录发表的说说。"""
        with self._lock:
            self._posts.append({
                "time": time.strftime("%Y-%m-%d %H:%M"),
                "content": content[:200],
                "tid": tid,
                "has_image": has_image,
            })
            if len(self._posts) > self.MAX_POSTS:
                self._posts = self._posts[-self.MAX_POSTS:]
            self._save()

    def add_comment(
        self,
        target_name: str,
        target_content: str,
        my_reply: str,
        tid: str = "",
        comment_id: str = "",
        owner_uin: str = "",
    ) -> None:
        """记录回复的评论。"""
        with self._lock:
            self._comments.append({
                "time": time.strftime("%Y-%m-%d %H:%M"),
                "target_name": target_name,
                "target_content": target_content[:100],
                "my_reply": my_reply[:200],
                "tid": tid,
                "commentid": comment_id,
                "owner_uin": owner_uin,
            })
            if len(self._comments) > self.MAX_COMMENTS:
                self._comments = self._comments[-self.MAX_COMMENTS:]
            self._save()

    def get_recent_posts(self, count: int = 5) -> list[dict]:
        """获取最近的说说。"""
        return self._posts[-count:]

    def get_recent_comments(self, count: int = 5) -> list[dict]:
        """获取最近的评论。"""
        return self._comments[-count:]

    def get_summary(self, max_posts: int = 3, max_comments: int = 3) -> str:
        """
        生成QQ空间记忆摘要（可注入LLM上下文）。

        Returns:
            格式化的摘要文本，或空字符串
        """
        parts = []

        recent_posts = self._posts[-max_posts:]
        if recent_posts:
            parts.append("[最近在QQ空间发的说说]")
            for p in recent_posts:
                img_tag = "（附图）" if p.get("has_image") else ""
                parts.append(f"  [{p['time']}] {p['content'][:50]}{img_tag}")

        recent_comments = self._comments[-max_comments:]
        if recent_comments:
            parts.append("[最近回复的QQ空间评论]")
            for c in recent_comments:
                parts.append(
                    f"  [{c['time']}] {c['target_name']}说「{c['target_content'][:30]}」"
                    f"→ 回复「{c['my_reply'][:30]}」"
                )

        return "\n".join(parts) if parts else ""

    def search(self, keyword: str) -> list[dict]:
        """搜索历史内容。"""
        results = []
        for p in self._posts:
            if keyword in p.get("content", ""):
                results.append({"type": "post", **p})
        for c in self._comments:
            if keyword in c.get("my_reply", "") or keyword in c.get("target_content", ""):
                results.append({"type": "comment", **c})
        return results[-20:]

    def get_replied_comment_keys(self) -> set[str]:
        """获取所有已回复评论的key（tid_commentid），供监控服务初始化。"""
        keys = set()
        for c in self._comments:
            tid = c.get("tid", "")
            cid = c.get("commentid", "")
            if tid and cid:
                keys.add(f"{tid}_{cid}")
        return keys

    def get_recent_tids(self, count: int = 10) -> list[str]:
        """获取最近发的说说ID列表（供启动时检查未回复评论）。"""
        tids = []
        for p in reversed(self._posts):
            tid = p.get("tid", "")
            if tid and tid not in tids:
                tids.append(tid)
                if len(tids) >= count:
                    break
        return tids

    @property
    def stats(self) -> dict:
        return {"posts": len(self._posts), "comments": len(self._comments)}

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._posts = data.get("posts", [])
                self._comments = data.get("comments", [])
            except Exception:
                pass

    def _save(self) -> None:
        try:
            data = {"posts": self._posts, "comments": self._comments}
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


# 全局单例
_instance: Optional[QzoneMemory] = None


def get_qzone_memory() -> QzoneMemory:
    """获取QQ空间记忆单例。"""
    global _instance
    if _instance is None:
        _instance = QzoneMemory()
    return _instance

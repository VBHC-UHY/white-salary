"""
white_salary/core/services/topic_association.py

话题关联服务 — 12种话题类型→记忆关联追踪。

借鉴v2的services/topic_association.py：
  - 12种预定义话题类型
  - 消息→话题分类（关键词匹配）
  - 话题下存储关联的对话片段
  - 查询"之前聊过什么游戏相关的"
  - JSON持久化

不用LLM，纯关键词分类。
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


# 12种话题类型及关键词
TOPIC_CATEGORIES = {
    "游戏": [
        "游戏", "打游戏", "王者", "原神", "MC", "Minecraft", "Steam",
        "LOL", "吃鸡", "手游", "网游", "主机", "Switch", "PS5",
        "通关", "段位", "排位", "副本", "装备",
    ],
    "动漫": [
        "动漫", "番", "漫画", "二次元", "coser", "声优",
        "海贼王", "火影", "鬼灭", "进击", "新番", "追番",
        "角色", "cv", "动画",
    ],
    "音乐": [
        "音乐", "歌", "听歌", "歌手", "专辑", "演唱会",
        "旋律", "节奏", "rap", "摇滚", "古典", "民谣",
    ],
    "美食": [
        "吃", "美食", "做饭", "菜", "餐厅", "外卖",
        "火锅", "烧烤", "蛋糕", "奶茶", "好吃", "饿",
        "食谱", "厨房", "料理",
    ],
    "学习": [
        "学习", "考试", "作业", "课", "老师", "成绩",
        "高考", "考研", "大学", "毕业", "论文", "实验",
        "编程", "代码", "算法",
    ],
    "工作": [
        "工作", "上班", "加班", "老板", "同事", "工资",
        "面试", "简历", "公司", "项目", "deadline", "汇报",
        "升职", "辞职", "跳槽",
    ],
    "感情": [
        "喜欢", "爱", "表白", "分手", "恋爱", "男朋友", "女朋友",
        "暧昧", "约会", "单身", "结婚", "对象", "暗恋",
    ],
    "生活": [
        "搬家", "租房", "装修", "家务", "超市", "购物",
        "快递", "天气", "堵车", "地铁", "公交",
    ],
    "健康": [
        "生病", "感冒", "发烧", "医院", "吃药", "头痛",
        "失眠", "减肥", "锻炼", "健身", "跑步", "运动",
    ],
    "心情": [
        "开心", "难过", "烦", "焦虑", "压力", "累",
        "无聊", "孤独", "放松", "自由", "舒服",
    ],
    "旅行": [
        "旅行", "旅游", "出去玩", "景点", "攻略", "机票",
        "酒店", "自驾", "海边", "山", "国外", "签证",
    ],
    "购物": [
        "买", "购物", "淘宝", "京东", "双十一", "打折",
        "优惠", "种草", "拔草", "开箱", "评测",
    ],
}


class TopicAssociationService:
    """
    话题关联服务。

    使用方式:
        service = TopicAssociationService(data_dir="data/memory")
        topics = service.detect_topics("今天打了一把王者荣耀")
        # → ["游戏"]
        service.record("游戏", "今天打了一把王者荣耀")
        history = service.get_topic_history("游戏")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "topic_associations.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        # {topic: [{time, content, context}, ...]}
        self._topics: dict[str, list[dict]] = {}
        self._load()

    def detect_topics(self, text: str) -> list[str]:
        """检测消息涉及的话题类型。"""
        if not text:
            return []
        detected = []
        for topic, keywords in TOPIC_CATEGORIES.items():
            for kw in keywords:
                if kw in text:
                    detected.append(topic)
                    break
        return detected

    def record(self, topic: str, content: str, context: str = "") -> None:
        """记录一条话题关联。"""
        if topic not in self._topics:
            self._topics[topic] = []

        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "content": content[:200],
            "context": context[:100],
        }
        self._topics[topic].append(entry)

        # 每个话题最多保留100条
        if len(self._topics[topic]) > 100:
            self._topics[topic] = self._topics[topic][-100:]

        self._save_debounced()

    def record_from_message(self, text: str, context: str = "") -> list[str]:
        """从消息自动检测并记录话题。返回检测到的话题列表。"""
        topics = self.detect_topics(text)
        for topic in topics:
            self.record(topic, text, context)
        return topics

    def get_topic_history(self, topic: str, limit: int = 10) -> list[dict]:
        """获取某个话题的历史记录。"""
        entries = self._topics.get(topic, [])
        return entries[-limit:]

    def get_all_topics(self) -> list[str]:
        """获取所有有记录的话题。"""
        return [t for t, entries in self._topics.items() if entries]

    def get_topic_summary(self) -> dict[str, int]:
        """获取各话题的记录数。"""
        return {t: len(entries) for t, entries in self._topics.items() if entries}

    def search_across_topics(self, keyword: str, limit: int = 10) -> list[dict]:
        """跨话题搜索关键词。"""
        results = []
        for topic, entries in self._topics.items():
            for entry in entries:
                if keyword in entry.get("content", ""):
                    results.append({"topic": topic, **entry})
                    if len(results) >= limit:
                        return results
        return results

    @property
    def stats(self) -> dict:
        total = sum(len(e) for e in self._topics.values())
        return {
            "total_records": total,
            "active_topics": len([t for t, e in self._topics.items() if e]),
            "topic_counts": self.get_topic_summary(),
        }

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 10 == 0:
            self._save()

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps(self._topics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[TopicAssoc] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            self._topics = json.loads(
                self._data_path.read_text(encoding="utf-8")
            )
            total = sum(len(e) for e in self._topics.values())
            logger.debug(f"[TopicAssoc] 加载: {total}条话题关联")
        except Exception as e:
            logger.warning(f"[TopicAssoc] 加载失败: {e}")

    def force_save(self) -> None:
        self._save()

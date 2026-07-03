"""
white_salary/core/qzone/interest_matcher.py

兴趣匹配器 — 分析内容类型，推荐@谁。

借鉴v2的interest_matcher.py：
  - 12种内容类型（日常/情感/美食/游戏/动漫/音乐/学习/工作/宠物/旅行/拍照/恋爱）
  - 关键词匹配用户兴趣
  - @人推荐逻辑
  - 从聊天中学习用户兴趣

重写适配我们的架构：
  - 优先从好感度系统获取用户信息
  - 异步学习+同步查询
  - JSON持久化
  - 单例模式
"""

import atexit
import json
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger


# 12类内容及其关键词
CONTENT_TYPES: dict[str, list[str]] = {
    "daily":    ["今天", "早上", "下午", "晚上", "明天", "昨天", "天气", "起床", "睡觉", "吃饭", "上班", "下班"],
    "emotion":  ["开心", "难过", "伤心", "生气", "烦", "累", "无聊", "寂寞", "孤独", "想你", "心情", "感动", "哭"],
    "food":     ["吃", "喝", "美食", "好吃", "火锅", "奶茶", "烧烤", "蛋糕", "甜点", "外卖", "饿", "零食", "做饭"],
    "game":     ["游戏", "打游戏", "原神", "王者", "吃鸡", "steam", "ps5", "switch", "联机", "开黑", "段位", "排位"],
    "anime":    ["动漫", "番剧", "二次元", "漫画", "cos", "声优", "新番", "追番", "萌", "老婆", "推", "角色"],
    "music":    ["音乐", "听歌", "歌", "唱歌", "播放", "专辑", "演唱会", "乐队", "旋律", "歌词"],
    "study":    ["学习", "考试", "作业", "上课", "论文", "复习", "成绩", "高考", "大学", "毕业", "知识"],
    "work":     ["工作", "加班", "项目", "老板", "同事", "工资", "开会", "面试", "简历", "创业"],
    "pet":      ["猫", "狗", "宠物", "猫咪", "狗狗", "铲屎", "喵", "汪", "可爱", "毛孩子"],
    "travel":   ["旅游", "旅行", "出去玩", "景点", "拍照", "打卡", "酒店", "机票", "高铁", "海边", "山"],
    "photo":    ["拍照", "自拍", "美颜", "相机", "照片", "滤镜", "修图", "好看"],
    "love":     ["喜欢", "爱", "恋爱", "表白", "约会", "男朋友", "女朋友", "暧昧", "暗恋", "分手"],
}


class InterestMatcher:
    """
    兴趣匹配器。

    功能：
      1. 从聊天消息中学习用户兴趣（12类，按频次统计）
      2. 分析内容属于哪几个类别
      3. 推荐@谁（匹配度最高的用户）
    """

    MAX_USERS = 200  # 最多记录200个用户

    def __init__(self, data_dir: str = "data/qzone") -> None:
        self._path = Path(data_dir) / "interests.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 线程安全
        self._dirty = False  # 脏标记（攒到一定量再写）
        self._dirty_count = 0  # 累计未保存的修改次数
        # {uin: {"nick": str, "interests": {type: count}, "last_msg": float}}
        self._users: dict[str, dict] = {}
        self._load()

    # ================================================================
    # 学习
    # ================================================================

    SAVE_INTERVAL = 50  # 每50次修改写一次磁盘

    def learn_from_message(self, uin: str, nick: str, message: str) -> None:
        """从聊天消息中学习用户兴趣。"""
        if not uin or not message:
            return

        types = self._analyze_content(message)
        if not types:
            return

        with self._lock:
            if uin not in self._users:
                self._users[uin] = {"nick": nick, "interests": {}, "last_msg": 0}

            user = self._users[uin]
            user["nick"] = nick or user.get("nick", "")
            user["last_msg"] = time.time()

            for t in types:
                user["interests"][t] = user["interests"].get(t, 0) + 1

            # 淘汰最旧的用户
            if len(self._users) > self.MAX_USERS:
                oldest = sorted(self._users, key=lambda u: self._users[u].get("last_msg", 0))
                for old_uin in oldest[:len(self._users) - self.MAX_USERS]:
                    del self._users[old_uin]

            # 批量写磁盘（不是每条消息都写）
            self._dirty_count += 1
            if self._dirty_count >= self.SAVE_INTERVAL:
                self._save()
                self._dirty_count = 0

    def flush(self) -> None:
        """强制写入磁盘（关闭时调用）。"""
        with self._lock:
            if self._dirty_count > 0:
                self._save()
                self._dirty_count = 0

    # ================================================================
    # 匹配
    # ================================================================

    def analyze_content(self, content: str) -> list[str]:
        """分析内容属于哪几个类别（公开接口）。"""
        return self._analyze_content(content)

    def match_users(
        self,
        content: str,
        exclude_uins: set[str] | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        """
        根据内容匹配最相关的用户。

        评分逻辑：
          1. 兴趣匹配分（聊天学到的兴趣类别重合度）
          2. 好感度加权（关系好的人优先@）
          3. 用户画像补充（从user_learning获取已有兴趣数据）

        Returns:
            [{"uin": "123", "nick": "小白", "score": 0.85}, ...]
        """
        content_types = self._analyze_content(content)
        if not content_types:
            return []

        exclude = exclude_uins or set()
        scored = []

        # 拿聊天学到的兴趣快照
        with self._lock:
            users_snapshot = dict(self._users)

        # 补充：从用户画像系统获取兴趣数据（已有的分析结果）
        self._enrich_from_profiles(users_snapshot)

        for uin, user in users_snapshot.items():
            if uin in exclude:
                continue
            interests = user.get("interests", {})
            if not interests:
                continue

            # 1. 兴趣匹配分
            total = sum(interests.values())
            if total == 0:
                continue
            match_count = sum(interests.get(t, 0) for t in content_types)
            score = match_count / total

            if score <= 0:
                continue

            # 2. 好感度加权（关系好的人分数更高）
            affinity_boost = self._get_affinity_boost(uin)
            score *= affinity_boost

            scored.append({
                "uin": uin,
                "nick": user.get("nick", ""),
                "score": round(score, 3),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    _affinity_cache: dict[str, float] = {}  # {uin: points} 缓存，避免每次读磁盘
    _affinity_cache_time: float = 0

    def _get_affinity_boost(self, uin: str) -> float:
        """根据好感度给分数加权（缓存5分钟）。"""
        # 缓存过期时重建
        if time.time() - self._affinity_cache_time > 300:
            self.__class__._affinity_cache = {}
            self.__class__._affinity_cache_time = time.time()

        if uin in self._affinity_cache:
            points = self._affinity_cache[uin]
        else:
            try:
                from white_salary.core.affinity.manager import AffinityManager
                mgr = AffinityManager.get_for_user(uin)
                points = mgr._affinity.points
                self._affinity_cache[uin] = points
            except Exception:
                return 1.0

        # 好感度分数 → 加权倍率
        if points >= 100:
            return 2.0
        elif points >= 60:
            return 1.6
        elif points >= 30:
            return 1.3
        elif points >= 0:
            return 1.0
        elif points >= -50:
            return 0.7
        else:
            return 0.3

    _profile_cache: dict = {}  # 画像缓存
    _profile_cache_time: float = 0

    def _enrich_from_profiles(self, users: dict) -> None:
        """从用户画像系统补充兴趣数据（缓存10分钟，不覆盖已有的聊天数据）。"""
        try:
            # 缓存10分钟，不用每次都读磁盘
            if time.time() - self._profile_cache_time > 600 or not self._profile_cache:
                from white_salary.core.services.user_learning import UserLearningService
                service = UserLearningService(memory_manager=None, data_dir="data/memory")
                self.__class__._profile_cache = service.get_all_profiles()
                self.__class__._profile_cache_time = time.time()
            profiles = self._profile_cache
            for uid, profile in profiles.items():
                if uid in users:
                    continue  # 聊天数据已有，不覆盖
                # 从画像的interests/likes字段推断兴趣类别
                profile_interests = profile.get("interests", []) + profile.get("likes", [])
                if not profile_interests:
                    continue
                inferred = {}
                for item in profile_interests:
                    for category, keywords in CONTENT_TYPES.items():
                        if any(kw in str(item) for kw in keywords):
                            inferred[category] = inferred.get(category, 0) + 1
                if inferred:
                    users[uid] = {
                        "nick": profile.get("user_name", uid),
                        "interests": inferred,
                        "last_msg": 0,
                    }
        except Exception:
            pass

    def get_at_targets(
        self,
        content: str,
        exclude_uins: set[str] | None = None,
        owner_uin: str = "",
        owner_nick: str = "",
    ) -> list[dict]:
        """
        获取发说说/评论时应该@的人。

        策略：
          1. 匹配到相关用户 → 返回最相关的（最多2人）
          2. 没匹配到 → 默认@主人
          3. 都没有 → 空列表

        Returns:
            [{"uin": "123", "nick": "小白"}, ...]
        """
        matches = self.match_users(content, exclude_uins, top_k=2)

        if matches:
            return [{"uin": m["uin"], "nick": m["nick"]} for m in matches]

        # 默认@主人（从conf.yaml读family_qq）
        if not owner_uin:
            owner_uin, owner_nick = self._get_owner()
        if owner_uin:
            return [{"uin": owner_uin, "nick": owner_nick or owner_uin}]

        return []

    _owner_cache: tuple = ("", "")

    def _get_owner(self) -> tuple[str, str]:
        """从conf.yaml获取主人QQ号（缓存）。"""
        if self._owner_cache[0]:
            return self._owner_cache
        try:
            import yaml
            from pathlib import Path
            # 2026-07-03 审计修复（批5）：conf.yaml 改为从模块位置推导项目根的
            # 绝对路径，不再依赖 CWD（此前从其它工作目录启动会静默拿空配置，
            # 依据 docs/audit-2026-07-02/config-audit.json）
            # 本文件位于 src/white_salary/core/qzone/，项目根 = parents[4]
            _project_root = Path(__file__).resolve().parents[4]
            conf = yaml.safe_load((_project_root / "conf.yaml").read_text(encoding="utf-8"))
            family_qq = conf.get("qq", {}).get("family_qq", [])
            if family_qq:
                uin = str(family_qq[0])
                nick = conf.get("qq", {}).get("bot_name", "主人")
                InterestMatcher._owner_cache = (uin, nick)
                return (uin, nick)
        except Exception:
            pass
        return ("", "")

    def get_user_interests(self, uin: str) -> dict[str, int]:
        """获取某用户的兴趣分布。"""
        user = self._users.get(uin, {})
        return dict(user.get("interests", {}))

    def learn_from_qzone(self, uin: str, nick: str, content: str) -> None:
        """从QQ空间评论/说说中学习兴趣（跟聊天学习一样的逻辑）。"""
        self.learn_from_message(uin, nick, content)

    # ================================================================
    # 内部
    # ================================================================

    def _analyze_content(self, content: str) -> list[str]:
        """分析内容属于哪几个类别。"""
        matched = []
        for category, keywords in CONTENT_TYPES.items():
            for kw in keywords:
                if kw in content:
                    matched.append(category)
                    break  # 每类只匹配一次
        return matched

    # ================================================================
    # 持久化
    # ================================================================

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._users = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"[QZone兴趣] 加载失败: {e}")

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._users, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[QZone兴趣] 保存失败: {e}")


# 全局单例
_instance: Optional[InterestMatcher] = None


def _atexit_flush() -> None:
    """程序退出时保存未写入的数据。"""
    if _instance is not None:
        _instance.flush()


def get_interest_matcher() -> InterestMatcher:
    """获取兴趣匹配器单例。"""
    global _instance
    if _instance is None:
        _instance = InterestMatcher()
        atexit.register(_atexit_flush)
    return _instance

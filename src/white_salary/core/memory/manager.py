"""
white_salary/core/memory/manager.py

记忆管理器 — 统一调度四层记忆系统。

职责：
  1. 对话后自动分析，提取值得记住的信息
  2. 用规则匹配 + LLM分析双重机制判断记忆层级
  3. 将记忆存到合适的层（核心/长期/对话）
  4. 对话前自动检索相关记忆，注入LLM上下文
  5. 管理记忆的生命周期（过期、清理）

触发记忆存储的关键词：
  记住、要记住、很重要、秘密、最喜欢、生日、
  纪念日、送你、对我很好、聊天很开心

参考: WhiteSalary-v2 memory_manager.py (2453行, 108KB)
"""

import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.memory.core_store import CoreMemoryStore
from white_salary.core.memory.long_term_store import LongTermMemoryStore
from white_salary.core.memory.important_store import ImportantMemoryStore
from white_salary.core.memory.knowledge_graph import KnowledgeGraph
from white_salary.core.memory.llm_extractor import LLMMemoryExtractor
from white_salary.core.memory.emotion_tracker import EmotionTracker
from white_salary.core.memory.cross_session import CrossSessionLinker, DynamicRenderer
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 记忆触发关键词（简繁体都覆盖）
# ================================================================

# 触发长期记忆存储的关键词
LONG_TERM_KEYWORDS = [
    "记住", "要记住", "别忘了", "很重要", "秘密", "最喜欢",
    "生日", "纪念日", "送你", "对我很好", "聊天很开心",
    "記住", "要記住", "別忘了", "很重要", "秘密", "最喜歡",
    "紀念日", "對我很好", "聊天很開心",
    "remember", "important", "birthday", "secret",
]

# 触发情感记忆的关键词
EMOTION_KEYWORDS = {
    "positive_strong": ["开心", "兴奋", "感动", "破防", "太好了", "好开心", "超级开心",
                        "開心", "興奮", "感動", "破防", "太好了"],
    "negative_strong": ["难过", "伤心", "崩溃", "气死", "好难过", "想哭", "心痛",
                        "難過", "傷心", "崩潰", "氣死"],
    "milestone":       ["成功", "毕业", "表白", "分手", "结婚", "升职", "录取",
                        "畢業", "表白", "分手", "結婚", "升職", "錄取"],
    "attachment":      ["好想你", "舍不得", "想见你", "离不开", "依赖",
                        "好想你", "捨不得", "想見你", "離不開"],
}

# 核心记忆提取的正则模式
CORE_PATTERNS = {
    "user_name": [
        r"我(?:叫|是|名字是|的名字是)\s*([^\s，。！？,!?\n]{1,10})",
        r"(?:叫我|称呼我|喊我)\s*([^\s，。！？,!?\n]{1,10})",
        r"(?:我的名字|我叫做)\s*([^\s，。！？,!?\n]{1,10})",
    ],
    "user_birthday": [
        r"我(?:的)?生日(?:是)?[在]?\s*(\d{1,2}月\d{1,2}[日号])",
        r"我(?:的)?生日(?:是)?[在]?\s*(\d{1,2}[./]\d{1,2})",
    ],
    "user_age": [
        r"我(?:今年)?(\d{1,3})\s*岁",
        r"我(?:今年)?(\d{1,3})\s*歲",
    ],
    "user_job": [
        r"我(?:是|做|当|在做)\s*([^\s，。！？,!?\n]{2,8}?)(?:的|工作|$)",
    ],
    "user_location": [
        r"我(?:在|住在|来自)\s*([^\s，。！？,!?\n]{2,10})",
    ],
}

# 喜好提取模式
PREFERENCE_PATTERNS = {
    "like": [
        r"我(?:喜欢|爱|很喜欢|特别喜欢|最喜欢|超喜欢)\s*([^\s，。！？,!?\n]{2,15})",
        r"我(?:喜歡|愛|很喜歡|特別喜歡|最喜歡)\s*([^\s，。！？,!?\n]{2,15})",
    ],
    "dislike": [
        r"我(?:讨厌|不喜欢|不爱|最讨厌|烦|受不了)\s*([^\s，。！？,!?\n]{2,15})",
        r"我(?:討厭|不喜歡|不愛|最討厭)\s*([^\s，。！？,!?\n]{2,15})",
    ],
}


# ================================================================
# 2026-07-02 审计修复（批4）：主人统一身份判定（核心档案写入白名单闸门用）
#
# 背景：core/long_term等store没有用户维度，此前任何QQ用户说"我叫X"都会以
# importance=8覆盖主人的核心档案（生产数据实锤：user_name被QQ用户"星月"覆盖）。
# 本闸门确保核心档案类提取（姓名/生日/职业/喜好等）只接受主人消息。
# ================================================================

# 模块级可注入覆盖：测试或上层可通过 set_owner_user_id 显式指定主人id。
# None = 未注入（惰性从 conf.yaml 读取并缓存）；"" = 明确表示"没有主人id配置"。
_owner_user_id_override: Optional[str] = None
_owner_user_id_cache: Optional[str] = None


def set_owner_user_id(owner_id: Optional[str]) -> None:
    """
    2026-07-02 审计修复（批4）：注入主人统一 user_id（测试/上层显式配置用）。

    Args:
        owner_id: 主人统一id字符串；传 None 表示清除注入与缓存，
                  下次调用 get_owner_user_id 时重新从 conf.yaml 读取
    """
    global _owner_user_id_override, _owner_user_id_cache
    _owner_user_id_override = owner_id
    _owner_user_id_cache = None


def get_owner_user_id() -> str:
    """
    2026-07-02 审计修复（批4）：读取主人统一 user_id。

    优先级：set_owner_user_id 注入值 > conf.yaml 的 qq.family_qq[0]。
    与桌面端 websocket_handler._resolve_owner_id 的解析口径保持一致。

    Returns:
        主人统一id字符串；配置缺失/读取失败时返回 ""（此时仅 "desktop" 被视为主人）
    """
    global _owner_user_id_cache
    if _owner_user_id_override is not None:
        return _owner_user_id_override
    if _owner_user_id_cache is not None:
        return _owner_user_id_cache

    resolved = ""
    try:
        import yaml

        candidates = [
            Path("conf.yaml"),
            # 兜底：不依赖CWD，按本文件位置定位项目根目录
            Path(__file__).resolve().parents[4] / "conf.yaml",
        ]
        for _p in candidates:
            if _p.exists():
                _raw = yaml.safe_load(_p.read_text(encoding="utf-8")) or {}
                _family = (_raw.get("qq") or {}).get("family_qq") or []
                if _family:
                    resolved = str(_family[0])
                break  # 配置读到了但 family_qq 为空 → 视为无主人id
    except Exception as e:
        logger.warning(f"[Memory] 解析主人统一user_id失败（核心档案闸门降级为仅desktop）: {e}")

    _owner_user_id_cache = resolved
    return resolved


def is_owner_user(user_id: str) -> bool:
    """
    2026-07-02 审计修复（批4）：判断 user_id 是否为主人。

    "desktop" 是桌面端历史身份，始终视为主人；
    其余仅当等于 conf.yaml qq.family_qq[0]（或注入值）时才是主人。
    """
    if user_id == "desktop":
        return True
    owner = get_owner_user_id()
    return bool(owner) and user_id == owner


class MemoryManager:
    """
    记忆管理器 — 统一调度四层记忆。

    四层架构：
      核心记忆 (CoreMemoryStore)  — 永久事实
      长期记忆 (LongTermMemoryStore) — 带过期的大容量记忆
      对话记忆 (ShortTermMemory)  — 当前会话上下文（外部管理）
      情感记忆                   — 通过长期记忆的emotion层实现

    2026-07-03 审计修复（批5）：进程级共享实例（按 data_dir 归一化路径缓存）。
    审计实锤：context_reviewer / settings_api 等处直接 new 本类，每次都会
    重开全部 store 并重新加载几十个扩展模块。改为同一 data_dir 全进程只
    保留一个实例（命中缓存时构造参数以首次创建为准）。
    """

    # 进程级共享实例注册表：归一化路径 -> 实例
    _shared_instances: dict[str, "MemoryManager"] = {}
    _shared_lock: threading.Lock = threading.Lock()

    def __new__(
        cls,
        data_dir: str = "data/memory",
        max_long_term: int = 5000,
        memory_llm: LLMInterface | None = None,
        emotion_llm: LLMInterface | None = None,
    ) -> "MemoryManager":
        # 2026-07-03 审计修复（批5）：按归一化路径复用实例，禁止整套重实例化
        key = str(Path(data_dir).resolve())
        with cls._shared_lock:
            inst = cls._shared_instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._shared_instances[key] = inst
        return inst

    def __init__(
        self,
        data_dir: str = "data/memory",
        max_long_term: int = 5000,
        memory_llm: LLMInterface | None = None,
        emotion_llm: LLMInterface | None = None,
    ) -> None:
        # 2026-07-03 审计修复（批5）：命中共享实例时跳过重复初始化
        # （标志在初始化末尾才置位，若上次初始化中途抛异常会自动重试）
        if getattr(self, "_shared_inited", False):
            return

        self._data_dir = data_dir

        # 加载记忆配置
        self._config = self._load_config()

        self._core = CoreMemoryStore(data_dir=data_dir)
        self._long_term = LongTermMemoryStore(data_dir=data_dir, max_entries=max_long_term)
        self._important = ImportantMemoryStore(data_dir=data_dir)
        self._knowledge_graph = KnowledgeGraph(data_dir=data_dir)
        self._emotion_tracker = EmotionTracker(data_dir=data_dir)
        self._cross_linker = CrossSessionLinker(self._long_term, self._knowledge_graph)
        self._dynamic_renderer = DynamicRenderer(self._core, self._long_term)

        # LLM记忆提取器（使用独立的memory_llm通道）
        self._llm_extractor = LLMMemoryExtractor(llm=memory_llm, max_calls_per_day=20)

        # 独立的情感分析LLM（不占用memory_llm的配额）
        self._emotion_llm = emotion_llm

        # 情感分析的每日调用次数限制
        self._emotion_analysis_count = 0
        self._emotion_analysis_date = ""
        self._max_emotion_analysis_per_day = 20

        # 自动发现并加载功能模块
        self._modules = []
        self._discover_modules(data_dir)

        # 2026-07-03 审计修复（批5）：模块落盘后台任务（懒启动）。
        # 审计实锤：6处模块定义了 on_session_end 但全项目0调用，
        # emotion_impressions.json 停在4月1日。由 manager 自建每5分钟的
        # flush 任务；构造线程可能没有事件循环，故此处只"尝试"启动，
        # 失败则等首次 async 调用（extract_and_store）时再懒启动。
        self._flush_interval_seconds: float = 300.0
        self._flush_task: Optional[asyncio.Task] = None
        self._ensure_flush_task()

        logger.info(
            f"[Memory] 初始化完成: "
            f"核心 {self._core.count}, "
            f"重要 {self._important.count}, "
            f"长期 {self._long_term.count}, "
            f"人物 {self._knowledge_graph.count}, "
            f"扩展模块 {len(self._modules)}"
        )
        self._shared_inited: bool = True

    def _discover_modules(self, data_dir: str) -> None:
        """自动发现memory/目录下的功能模块。"""
        import importlib
        from pathlib import Path

        module_dir = Path(__file__).parent
        kwargs = {
            "core_store": self._core,
            "long_term_store": self._long_term,
            "knowledge_graph": self._knowledge_graph,
        }

        # 扫描memory/*.py + memory/enhanced/*.py
        scan_dirs = [
            (module_dir, "white_salary.core.memory"),
            (module_dir / "enhanced", "white_salary.core.memory.enhanced"),
        ]
        skip_names = {
            "manager", "module_base", "core_store", "long_term_store",
            "important_store", "knowledge_graph", "emotion_tracker",
            "cross_session", "llm_extractor", "short_term", "summarizer",
            "conversation_log",
        }

        # 2026-07-03 审计修复（批5）：模块启用开关（计划书9.2决策落地）。
        # config/memory_settings.json 的 modules.disabled 列出按文件名(stem)
        # 禁用的模块——文件保留不删、想恢复改配置即可。
        disabled_names_raw = self.get_config("modules", "disabled", default=[]) or []
        disabled_names: set[str] = {str(n) for n in disabled_names_raw}
        disabled_count = 0

        for scan_dir, module_prefix in scan_dirs:
            if not scan_dir.exists():
                continue
            for py_file in sorted(scan_dir.glob("*.py")):
                if py_file.name.startswith("_") or py_file.stem in skip_names:
                    continue

                # 2026-07-03 审计修复（批5）：按配置跳过禁用模块（不import不实例化）
                if py_file.stem in disabled_names:
                    disabled_count += 1
                    logger.debug(f"[Memory] 扩展模块 {py_file.stem} 已按配置禁用，跳过")
                    continue

                try:
                    mod = importlib.import_module(f"{module_prefix}.{py_file.stem}")
                    module_cls = getattr(mod, "MODULE", None)
                    if module_cls is None:
                        continue
                    instance = module_cls()
                    instance.init(data_dir=data_dir, **kwargs)
                    self._modules.append(instance)
                    logger.debug(f"[Memory] 加载扩展模块: {instance.name}")
                except Exception as e:
                    logger.debug(f"[Memory] 模块 {py_file.stem} 跳过: {e}")

        logger.info(
            f"[Memory] 扩展模块: 加载 {len(self._modules)} 个 / "
            f"按配置禁用 {disabled_count} 个"
        )

    # ================================================================
    # 2026-07-03 审计修复（批5）：模块落盘后台任务
    # ================================================================

    def _ensure_flush_task(self) -> None:
        """
        2026-07-03 审计修复（批5）：懒启动模块落盘后台任务（防重）。

        manager 可能在无事件循环的线程被构造（如测试/脚本），此时静默跳过；
        首次在事件循环内调用（extract_and_store 等）时自动补启动。
        已有存活任务则不重复创建。
        """
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 当前线程没有运行中的事件循环，等下次再试
        self._flush_task = loop.create_task(self._module_flush_loop())
        logger.info(
            f"[Memory] 模块落盘后台任务已启动"
            f"（每 {self._flush_interval_seconds:.0f} 秒调用各模块 on_session_end）"
        )

    async def _module_flush_loop(self) -> None:
        """后台循环：每5分钟对所有模块执行 on_session_end 语义的落盘。"""
        while True:
            try:
                await asyncio.sleep(self._flush_interval_seconds)
                flushed = self.flush_modules()
                if flushed:
                    logger.debug(f"[Memory] 后台落盘: {flushed} 个模块已保存")
            except asyncio.CancelledError:
                # 任务被取消（如停服），最后落盘一次再退出
                try:
                    self.flush_modules()
                except Exception as e:
                    logger.warning(f"[Memory] 停止前最后落盘失败: {e}")
                raise
            except Exception as e:
                logger.warning(f"[Memory] 模块落盘循环异常（继续运行）: {e}")

    def flush_modules(self) -> int:
        """
        对所有扩展模块调用 on_session_end（落盘语义）。

        只调用真正覆写了 on_session_end 的模块（基类空实现跳过）；
        单个模块失败不影响其它模块。

        Returns:
            实际执行落盘的模块数量
        """
        flushed = 0
        for m in self._modules:
            try:
                hook = getattr(type(m), "on_session_end", None)
                if hook is None or hook is MemoryModule.on_session_end:
                    continue  # 模块没有实现该钩子，跳过
                m.on_session_end()
                flushed += 1
            except Exception as e:
                name = getattr(m, "name", "unknown")
                logger.warning(f"[Memory] 模块 {name} on_session_end 落盘失败: {e}")
        return flushed

    def get_modules_context(self, message: str = "",
                           user_id: str = "desktop",
                           is_group: bool = False) -> str:
        """获取所有功能模块的上下文提示（注入system prompt）。"""
        parts = []
        for m in self._modules:
            try:
                ctx = m.get_context_prompt(message, user_id=user_id, is_group=is_group)
                if ctx:
                    parts.append(ctx)
            except TypeError:
                # 老模块不支持新参数，退回旧调用
                try:
                    ctx = m.get_context_prompt(message)
                    if ctx:
                        parts.append(ctx)
                except Exception:
                    pass
            except Exception as e:
                name = getattr(m, 'name', 'unknown')
                logger.debug(f"[Memory] 模块 {name} get_context_prompt异常: {e}")
        return "\n".join(parts)

    def notify_modules_message(self, user_msg: str = "", ai_reply: str = "",
                               user_id: str = "desktop",
                               is_group: bool = False) -> None:
        """通知所有功能模块有新消息。"""
        for m in self._modules:
            try:
                m.on_message(user_msg, ai_reply, user_id=user_id, is_group=is_group)
            except TypeError:
                try:
                    m.on_message(user_msg, ai_reply)
                except Exception:
                    pass
            except Exception as e:
                name = getattr(m, 'name', 'unknown')
                logger.debug(f"[Memory] 模块 {name} on_message异常: {e}")

    def _load_config(self) -> dict:
        """加载记忆系统配置。"""
        import json
        from pathlib import Path
        config_path = Path("config/memory_settings.json")
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                logger.debug(f"[Memory] 配置已加载: {len(config)} 个配置项")
                return config
            except Exception as e:
                logger.warning(f"[Memory] 配置加载失败: {e}")
        return {}

    def get_config(self, section: str, key: str = "", default=None):
        """获取配置项。"""
        if not key:
            return self._config.get(section, default or {})
        return self._config.get(section, {}).get(key, default)

    @property
    def core(self) -> CoreMemoryStore:
        return self._core

    @property
    def long_term(self) -> LongTermMemoryStore:
        return self._long_term

    @property
    def important(self) -> ImportantMemoryStore:
        return self._important

    @property
    def knowledge_graph(self) -> KnowledgeGraph:
        return self._knowledge_graph

    @property
    def emotion(self) -> EmotionTracker:
        return self._emotion_tracker

    # ================================================================
    # 记忆提取（对话后自动调用）
    # ================================================================

    async def extract_and_store(self, user_message: str, ai_reply: str,
                               user_id: str = "desktop",
                               is_group: bool = False) -> list[str]:
        """
        分析一轮对话，提取值得记住的信息并存储。

        提取机制（双重保障）：
          1. 正则模式匹配 → 核心记忆（名字、生日等硬事实）— 即时、零成本
          2. 喜好匹配 → 核心记忆（喜欢/讨厌）— 即时、零成本
          3. 关键词触发 → 长期记忆（"记住"、"很重要"等）— 即时、零成本
          4. 情感关键词 → 长期记忆emotion层 — 即时、零成本
          5. LLM智能分析 → 核心/长期记忆 — 每日限额、更智能

        Args:
            user_message: 用户说的话
            ai_reply: AI的回复

        Returns:
            新提取的记忆描述列表
        """
        # 2026-07-03 审计修复（批5）：首次进入事件循环时补启动模块落盘后台任务
        self._ensure_flush_task()

        extracted = []

        # 2026-07-02 审计修复（批4）：核心档案写入白名单闸门。
        # 核心档案类提取（姓名/生日/职业/位置/喜好等）只允许主人消息写入，
        # 防止QQ群任意用户的"我叫X/我在X"覆盖主人的核心事实（生产数据已实锤污染）。
        # 其它记忆层（长期/情感/重要/知识图谱）不受影响。
        owner_ok = is_owner_user(user_id)

        if owner_ok:
            # 1. 核心信息提取（正则匹配，零成本）
            extracted.extend(self._extract_core_info(user_message))

            # 2. 喜好提取（零成本）
            extracted.extend(self._extract_preferences(user_message))
        else:
            logger.debug(
                f"[Memory] 白名单闸门: 非主人用户 {user_id} 的消息跳过核心档案提取"
            )

        # 3. 关键词触发的长期记忆（零成本）
        extracted.extend(self._extract_long_term_by_keywords(user_message, ai_reply))

        # 4. 情感记忆（零成本）
        extracted.extend(self._extract_emotion_memory(user_message))

        # 5. 重要记忆触发（关键词检测：答应我、别忘了等）
        extracted.extend(self._important.check_and_store(user_message))

        # 6. 知识图谱提取（人物关系：我妈妈叫xxx等）
        extracted.extend(self._knowledge_graph.extract_from_text(user_message))

        # 7. 情绪记忆联动（强烈情绪自动存入长期记忆）
        if self._emotion_tracker.should_store_to_memory():
            content = self._emotion_tracker.get_memory_content()
            self._long_term.add(
                content=content,
                layer="emotion",
                source="emotion_tracker",
                importance=6,
            )
            extracted.append(f"情绪联动:{content[:30]}")

        # 8. LLM情感分析（用LLM判断情绪，比关键词更准）
        # 2026-07-03 审计修复（批5）：
        #   - 门槛从查 _llm_extractor._llm 改为查 self._emotion_llm——
        #     实际发调用的是 emotion_llm，两者配置不一致时原判断为张冠李戴；
        #   - 每日限额 _max_emotion_analysis_per_day 归位到这条真正花钱的
        #     LLM 路径（原来错挂在第4步零成本关键词路径上，这里无限额）。
        if self._emotion_llm and user_message and len(user_message) > 10:
            if self._check_emotion_analysis_quota():
                try:
                    self._emotion_analysis_count += 1  # 发起调用即计数（无论结果是否有效）
                    emotion_result = await self._analyze_emotion_by_llm(user_message)
                    if emotion_result:
                        self._emotion_tracker.record_emotion(
                            emotion_result, intensity=0.6, trigger="llm_analysis", user_id=user_id
                        )
                        extracted.append(f"LLM情感:{emotion_result}")
                except Exception as e:
                    logger.debug(f"[Memory] LLM emotion analysis skipped: {e}")

        # 9. LLM智能提取（有限额，更智能）
        # 2026-07-02 审计修复（批4）：LLM提取的core层写入同样受白名单闸门约束
        llm_memories = await self._extract_by_llm(user_message, ai_reply, allow_core=owner_ok)
        extracted.extend(llm_memories)

        # 10. 通知所有自动发现模块（39个模块的on_message）
        self.notify_modules_message(user_message, ai_reply, user_id=user_id, is_group=is_group)

        if extracted:
            logger.info(f"[Memory] 提取了 {len(extracted)} 条新记忆: {extracted}")

        return extracted

    def _extract_core_info(self, text: str) -> list[str]:
        """用正则提取核心事实信息。"""
        results = []
        for key, patterns in CORE_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    value = match.group(1).strip()
                    if value and 1 <= len(value) <= 20:
                        old = self._core.get(key)
                        if old != value:
                            self._core.set(
                                key=key,
                                value=value,
                                category="basic_info",
                                source="user_said",
                                importance=8,
                            )
                            results.append(f"核心:{key}={value}")
                    break  # 每个key只匹配第一个模式
        return results

    def _extract_preferences(self, text: str) -> list[str]:
        """提取喜好偏好。"""
        results = []
        for pref_type, patterns in PREFERENCE_PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    value = match.group(1).strip()
                    if value and 2 <= len(value) <= 15:
                        key = f"{pref_type}_{value}"
                        full_text = match.group(0)
                        self._core.set(
                            key=key,
                            value=full_text,
                            category="preference",
                            source="user_said",
                            importance=6,
                        )
                        results.append(f"偏好:{key}")
        return results

    def _extract_long_term_by_keywords(self, user_msg: str, ai_reply: str) -> list[str]:
        """关键词触发的长期记忆存储。"""
        results = []

        # 检查用户消息中的关键词
        for keyword in LONG_TERM_KEYWORDS:
            if keyword in user_msg:
                # 截取关键内容（最多120字）
                content = user_msg[:120]
                keywords_str = keyword

                self._long_term.add(
                    content=content,
                    layer="event",
                    source="keyword_trigger",
                    keywords=keywords_str,
                    importance=7,
                )
                results.append(f"长期:{keyword}触发")
                break  # 一条消息只触发一次

        return results

    def _check_emotion_analysis_quota(self) -> bool:
        """
        2026-07-03 审计修复（批5）：LLM情感分析每日限额检查（含跨日重置）。

        限额只约束真正花钱的 LLM 情感分析路径（extract_and_store 第8步）；
        零成本的关键词路径不受限。

        Returns:
            True=今日还有剩余额度，False=已用完
        """
        today = time.strftime("%Y-%m-%d")
        if self._emotion_analysis_date != today:
            self._emotion_analysis_date = today
            self._emotion_analysis_count = 0
        return self._emotion_analysis_count < self._max_emotion_analysis_per_day

    def _extract_emotion_memory(self, text: str) -> list[str]:
        """提取情感相关的记忆。"""
        results = []

        # 2026-07-03 审计修复（批5）：取消本路径的每日限额——
        # 这里是纯关键词匹配（零成本），原限额挂错了地方，导致真正花钱的
        # LLM情感分析路径反而无限额。限额已移至 extract_and_store 第8步。

        for category, keywords in EMOTION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    content = f"[{category}] {text[:100]}"

                    # 根据类型决定层级
                    if category == "milestone":
                        layer = "event"
                        importance = 9
                        is_highlight = True
                    elif category in ("positive_strong", "negative_strong"):
                        layer = "emotion"
                        importance = 6
                        is_highlight = False
                    else:
                        layer = "emotion"
                        importance = 7
                        is_highlight = False

                    self._long_term.add(
                        content=content,
                        layer=layer,
                        source="emotion_detect",
                        keywords=keyword,
                        importance=importance,
                        is_highlight=is_highlight,
                    )
                    # 2026-07-03 审计修复（批5）：关键词路径不再消耗LLM情感分析限额
                    results.append(f"情感:{category}/{keyword}")
                    break  # 每个类别只触发一次

        return results

    async def _analyze_emotion_by_llm(self, text: str) -> Optional[str]:
        """用独立的emotion_llm分析文本的情绪。"""
        # 优先用独立的emotion_llm，没有则不分析（不占用其他模型的资源）
        llm = self._emotion_llm
        if not llm:
            return None

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            response = await llm.chat_completion(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=(
                        "判断以下文字的情绪。只回复一个词："
                        "happy/sad/angry/surprised/shy/excited/touched/calm/bored/frustrated/scared/neutral"
                    )),
                    Message(role=MessageRole.USER, content=text[:200]),
                ],
                temperature=0.1,
                max_tokens=10,
            )
            emotion = response.strip().lower().split()[0] if response else None
            valid_emotions = {"happy", "sad", "angry", "surprised", "shy", "excited",
                            "touched", "calm", "bored", "frustrated", "scared", "neutral"}
            if emotion in valid_emotions:
                return emotion
        except Exception:
            pass
        return None

    async def _extract_by_llm(self, user_msg: str, ai_reply: str,
                              allow_core: bool = True) -> list[str]:
        """
        用LLM智能分析对话，提取记忆。

        Args:
            user_msg: 用户消息
            ai_reply: AI回复
            allow_core: 2026-07-02 审计修复（批4）：是否允许写入核心记忆层
                        （非主人消息为False，core层提取被跳过，其它层照常）
        """
        results = []
        try:
            memories = await self._llm_extractor.extract(user_msg, ai_reply)
            for m in memories:
                layer = m.get("layer", "event")
                if layer == "core":
                    # 2026-07-02 审计修复（批4）：白名单闸门——非主人消息不写核心档案
                    if not allow_core:
                        logger.debug(
                            f"[Memory] 白名单闸门: 跳过非主人的LLM核心提取 {m.get('key')}"
                        )
                        continue
                    # 存入核心记忆
                    self._core.set(
                        key=m["key"],
                        value=m["value"],
                        category=m.get("category", "other"),
                        source="llm_extract",
                        importance=m.get("importance", 5),
                    )
                    results.append(f"LLM核心:{m['key']}={m['value']}")
                else:
                    # 存入长期记忆
                    self._long_term.add(
                        content=m["value"],
                        layer=layer,
                        source="llm_extract",
                        keywords=m.get("keywords", ""),
                        importance=m.get("importance", 5),
                    )
                    results.append(f"LLM长期:[{layer}]{m['value'][:30]}")
        except Exception as e:
            logger.warning(f"[Memory] LLM提取失败: {e}")
        return results

    # ================================================================
    # 上下文注入（对话前调用）
    # ================================================================

    def get_context_injection(self, current_message: str = "",
                             user_id: str = "desktop",
                             is_group: bool = False) -> str:
        """
        获取要注入到LLM上下文中的记忆信息。

        包含：
          - 核心记忆（全量注入，用户的基本信息）
          - 长期记忆（与当前话题最相关的记忆）
          - 精华记忆（重要时刻的回忆）

        Args:
            current_message: 当前用户消息（用于检索相关长期记忆）

        Returns:
            格式化的记忆文本，注入到系统提示词之后
        """
        parts = []

        # 动态渲染核心记忆（根据当前话题选择最相关的）
        dynamic_ctx = self._dynamic_renderer.render_context(current_message)
        if dynamic_ctx:
            parts.append(dynamic_ctx)

        # 知识图谱（用户的社交关系网络）
        kg_ctx = self._knowledge_graph.get_context_string()
        if kg_ctx:
            parts.append(kg_ctx)

        # 重要记忆（用户的承诺、约定、特殊请求）
        imp_ctx = self._important.get_context_string()
        if imp_ctx:
            parts.append(imp_ctx)

        # 最近跨平台对话（同一 user_id 在桌面/QQ 的最近几轮）
        recent_ctx = self._get_recent_conversation_context(user_id=user_id)
        if recent_ctx:
            parts.append(recent_ctx)

        # 跨会话关联记忆（之前对话中提到的相关人/事）
        cross_ctx = self._cross_linker.find_related_memories(current_message)
        if cross_ctx:
            parts.append(cross_ctx)

        # 当前情绪状态
        emo_ctx = self._emotion_tracker.get_context_hint()
        if emo_ctx:
            parts.append(emo_ctx)

        # 所有自动发现模块的上下文（39个模块的get_context_prompt）
        modules_ctx = self.get_modules_context(current_message, user_id=user_id, is_group=is_group)
        if modules_ctx:
            parts.append(modules_ctx)

        if not parts:
            return ""

        return "\n\n".join(parts)

    def _get_recent_conversation_context(
        self,
        user_id: str = "desktop",
        limit: int = 4,
    ) -> str:
        """
        获取同一用户最近的跨平台对话片段。

        ConversationLog 是 QQ/桌面共同写入的轻量日志。这里按 user_id 过滤后
        注入最近几轮，解决“QQ 刚聊过，桌面端自然对话不知道；桌面刚聊过，
        QQ 端自然对话也不知道”的断层。只按同一 user_id 注入，避免群友内容
        串进主人的桌面上下文。
        """
        if not user_id:
            return ""
        try:
            from white_salary.core.memory.conversation_log import ConversationLog

            entries = ConversationLog.get_instance().get_recent_by_user(
                user_id=str(user_id),
                limit=limit,
            )
        except Exception as exc:
            logger.debug(f"[Memory] 最近跨平台对话读取失败: {exc}")
            return ""

        if not entries:
            return ""

        def _short(text: str, max_len: int = 120) -> str:
            text = (text or "").replace("\n", " ").strip()
            return text[:max_len] + ("..." if len(text) > max_len else "")

        lines = ["[最近跨平台对话上下文]"]
        for entry in reversed(entries):
            platform = "QQ" if entry.platform == "qq" else "桌面"
            if entry.platform == "qq" and entry.group_id:
                platform = f"QQ群{entry.group_id}"
            user_label = entry.user_name or entry.user_id or "用户"
            if entry.user_msg:
                lines.append(f"- [{entry.time_str} {platform}] {user_label}: {_short(entry.user_msg)}")
            if entry.ai_reply:
                lines.append(f"  白: {_short(entry.ai_reply)}")
        return "\n".join(lines) if len(lines) > 1 else ""

    # ================================================================
    # 统计和管理
    # ================================================================

    def get_stats(self) -> dict:
        """获取记忆系统统计信息。"""
        return {
            "core": self._core.get_stats(),
            "important": {"total": self._important.count},
            "long_term": self._long_term.get_stats(),
            "knowledge_graph": self._knowledge_graph.get_stats(),
            "emotion": self._emotion_tracker.get_stats(),
            "emotion_analysis_today": self._emotion_analysis_count,
            "llm_extract_remaining": self._llm_extractor.calls_remaining_today,
        }

    def clear_all(self) -> None:
        """清空所有记忆（危险操作！）。"""
        # 核心记忆
        for key in list(self._core._cache.keys()):
            self._core.delete(key)
        # 长期记忆需要直接操作数据库
        import sqlite3
        conn = sqlite3.connect(str(self._long_term._db_path))
        conn.execute("DELETE FROM long_term_memory")
        conn.commit()
        conn.close()
        logger.warning("[Memory] 所有记忆已清空！")

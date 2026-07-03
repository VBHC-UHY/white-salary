"""记忆工具 — 搜索/添加/更新/删除/记住/回忆对话。"""
import time
from ._helpers import tool, P, S, I, NONE_PARAMS


@tool("memory_search", "搜索AI记忆库（核心记忆和对话日志）",
      P(keyword=S("搜索关键词", True), memory_type=S("类型:all/core/conversation")))
async def memory_search(keyword: str = "", memory_type: str = "all") -> str:
    results = []
    if memory_type in ("all", "core"):
        try:
            from white_salary.core.memory.core_store import CoreMemoryStore
            core = CoreMemoryStore()
            for key, entry in core._cache.items():
                val = str(entry.get("value", ""))
                if keyword.lower() in key.lower() or keyword.lower() in val.lower():
                    results.append(f"[核心] {key}: {val}")
        except Exception:
            pass
    if memory_type in ("all", "conversation"):
        try:
            from white_salary.core.memory.conversation_log import ConversationLog
            log = ConversationLog.get_instance()
            for e in log.search(keyword=keyword, limit=10):
                results.append(f"[{e.platform_label} {e.time_str}] {e.user_name}: {e.user_msg[:30]}")
        except Exception:
            pass
    return f"找到{len(results)}条:\n" + "\n".join(results[:15]) if results else f"没找到「{keyword}」"


@tool("memory_add", "主动添加一条核心记忆",
      P(key=S("键名", True), value=S("内容", True), category=S("分类")))
async def memory_add(key: str = "", value: str = "", category: str = "other") -> str:
    if not key or not value:
        return "请提供key和value"
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        CoreMemoryStore().set(key, value, category=category)
        return f"已记住: {key} = {value}"
    except Exception as e:
        return f"失败: {e}"


@tool("memory_update", "更新已有的核心记忆", P(key=S("键名", True), value=S("新内容", True)))
async def memory_update(key: str = "", value: str = "") -> str:
    if not key or not value:
        return "请提供key和新value"
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        CoreMemoryStore().set(key, value)
        return f"已更新: {key} = {value}"
    except Exception as e:
        return f"失败: {e}"


@tool("memory_remove", "删除一条核心记忆", P(key=S("键名", True)))
async def memory_remove(key: str = "") -> str:
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        CoreMemoryStore().delete(key)
        return f"已删除: {key}"
    except Exception as e:
        return f"失败: {e}"


@tool("remember", "记住用户告诉的信息", P(content=S("要记住的内容", True)))
async def remember(content: str = "") -> str:
    if not content:
        return "请告诉我要记住什么"
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        CoreMemoryStore().set(f"user_note_{int(time.time())}", content, category="note")
        return f"好的，我记住了: {content[:50]}"
    except Exception as e:
        return f"失败: {e}"


@tool("remember_important", "记住重要信息（高优先级）",
      P(content=S("重要内容", True), importance=I("优先级1-10")))
async def remember_important(content: str = "", importance: int = 8) -> str:
    if not content:
        return "请告诉我要记住什么"
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        CoreMemoryStore().set(f"important_{int(time.time())}", content, category="important")
        return f"已记住重要信息: {content[:50]}"
    except Exception as e:
        return f"失败: {e}"


@tool("recall_conversation", "回忆之前在QQ和桌面端聊过的内容",
      P(keyword=S("搜索关键词", True), platform=S("平台:qq/desktop/空=全部"), user_name=S("用户名")))
async def recall_conversation(keyword: str = "", platform: str = "", user_name: str = "") -> str:
    from white_salary.core.memory.conversation_log import ConversationLog
    log = ConversationLog.get_instance()
    entries = log.search(keyword=keyword, platform=platform, user_name=user_name, limit=15, days=30)
    return log.format_results(entries)


@tool("memory_migrate", "记忆数据迁移（管理员功能）", P(source=S("来源"), target=S("目标")))
async def memory_migrate(source: str = "", target: str = "") -> str:
    return f"[记忆迁移] {source} → {target}\n需要管理员权限"


@tool("learning_stats", "查看AI学习统计")
async def learning_stats() -> str:
    try:
        from white_salary.core.memory.core_store import CoreMemoryStore
        from white_salary.core.memory.conversation_log import ConversationLog
        core = CoreMemoryStore()
        log = ConversationLog.get_instance()
        return f"学习统计:\n  核心记忆: {len(core._cache)}条\n  对话记录: {log.total_count}条"
    except Exception as e:
        return f"获取失败: {e}"


TOOLS = [fn._tool_def for fn in [
    memory_search, memory_add, memory_update, memory_remove, remember,
    remember_important, recall_conversation, memory_migrate, learning_stats,
]]

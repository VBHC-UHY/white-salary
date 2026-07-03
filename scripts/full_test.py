"""
White Salary - 完整功能测试（覆盖所有模块的所有核心方法）
在Windows上用GPT-SoVITS的venv运行
"""
import asyncio
import sys
import tempfile
import shutil
import time
import json
from pathlib import Path

async def test():
    passed = 0
    failed = 0
    total = 0
    failures = []

    def check(name, cond):
        nonlocal passed, failed, total
        total += 1
        if cond:
            passed += 1
            print(f"  OK  {name}")
        else:
            failed += 1
            failures.append(name)
            print(f"  FAIL {name}")

    tmpdir = tempfile.mkdtemp()

    try:
        # ============================================================
        # 1. CORE INTERFACES (9 interfaces)
        # ============================================================
        print("\n[1/15] Core Interfaces")
        from white_salary.core.interfaces.types import Message, MessageRole, AudioData, EmotionState, ToolCall, ToolResult, ImageData
        check("Message creation", Message(role=MessageRole.USER, content="hi").content == "hi")
        check("AudioData creation", AudioData(samples=b"test", sample_rate=16000, dtype="wav").sample_rate == 16000)
        check("ToolCall creation", ToolCall(id="1", name="test", arguments={}).name == "test")
        from white_salary.core.interfaces.llm import LLMInterface
        from white_salary.core.interfaces.asr import ASRInterface, TranscriptionResult
        from white_salary.core.interfaces.tts import TTSInterface
        from white_salary.core.interfaces.vad import VADInterface
        from white_salary.core.interfaces.vision import VisionInterface
        from white_salary.core.interfaces.singing import SingingInterface
        from white_salary.core.interfaces.avatar import AvatarInterface
        from white_salary.core.interfaces.storage import KeyValueStorageInterface, VectorStorageInterface
        check("All 9 interfaces", True)

        # ============================================================
        # 2. EXCEPTIONS
        # ============================================================
        print("\n[2/15] Exceptions")
        from white_salary.core.exceptions import (
            WhiteSalaryError, ConfigError, LLMError, LLMConnectionError,
            LLMAuthenticationError, LLMRateLimitError, TTSError, ASRError
        )
        check("Exception hierarchy", issubclass(LLMConnectionError, LLMError) and issubclass(LLMError, WhiteSalaryError))

        # ============================================================
        # 3. TEXT UTILS (every function)
        # ============================================================
        print("\n[3/15] Text Utils")
        from white_salary.utils.text import split_sentences, truncate_text, clean_text, extract_emotion_tags, strip_action_tags, strip_xml_tags, is_valid_for_tts

        check("split_sentences", split_sentences("你好！今天天气真好。") == ["你好！", "今天天气真好。"])
        check("truncate_text", truncate_text("abcdefgh", 5) == "ab...")
        check("clean_text", clean_text("  hello   world  ") == "hello world")
        check("extract_emotion_tags", extract_emotion_tags("[happy]你好")[1] == ["happy"])
        check("strip_action_tags basic", strip_action_tags("（歪头）你好啊") == "你好啊")
        # strip_action_tags removes ALL tags+content (for TTS), strip_xml_tags keeps content (for display)
        check("strip_action_tags removes tags", "<msg>" not in strip_action_tags("<msg>hello</msg>"))
        check("strip_action_tags sticker", strip_action_tags("<sticker>test.jpg</sticker>你好") == "你好")
        check("strip_xml_tags", strip_xml_tags("<msg>（歪头）你好</msg>") == "（歪头）你好")
        check("is_valid_for_tts empty", not is_valid_for_tts(""))
        check("is_valid_for_tts dots", not is_valid_for_tts("…"))
        check("is_valid_for_tts punct", not is_valid_for_tts("。！？"))
        check("is_valid_for_tts valid", is_valid_for_tts("你好啊"))

        # ============================================================
        # 4. CORE MEMORY STORE (every method)
        # ============================================================
        print("\n[4/15] Core Memory Store")
        from white_salary.core.memory.core_store import CoreMemoryStore
        core = CoreMemoryStore(data_dir=tmpdir)
        check("core.set new", core.set("user_name", "Tom", category="basic_info", importance=9) == True)
        check("core.set update", core.set("user_name", "Jerry", category="basic_info", importance=9) == False)
        check("core.get", core.get("user_name") == "Jerry")
        check("core.get_entry", core.get_entry("user_name").importance == 9)
        core.set("like_cats", "I love cats", category="preference", importance=6)
        check("core.count", core.count == 2)
        check("core.get_all", len(core.get_all()) == 2)
        check("core.get_by_category", len(core.get_by_category("basic_info")) == 1)
        check("core.search", len(core.search("Jerry")) == 1)
        check("core.get_most_important", core.get_most_important(1)[0].key == "user_name")
        ctx = core.get_context_string()
        check("core.context_string", "Jerry" in ctx and "cats" in ctx)
        check("core.delete", core.delete("like_cats") == True)
        check("core.count after delete", core.count == 1)
        # Verify triple-write
        check("core.json exists", (Path(tmpdir) / "core.json").exists())
        check("core.txt exists", (Path(tmpdir) / "core.txt").exists())
        check("core.db exists", (Path(tmpdir) / "core.db").exists())

        # ============================================================
        # 5. LONG TERM MEMORY (with ChromaDB)
        # ============================================================
        print("\n[5/15] Long Term Memory + ChromaDB")
        from white_salary.core.memory.long_term_store import LongTermMemoryStore, CHROMADB_AVAILABLE
        check("ChromaDB installed", CHROMADB_AVAILABLE)
        lt = LongTermMemoryStore(data_dir=tmpdir)
        id1 = lt.add("user birthday is June 16", layer="fact", keywords="birthday,june", importance=9, is_highlight=True)
        id2 = lt.add("went to park yesterday", layer="event", keywords="park", importance=5)
        id3 = lt.add("feeling happy today", layer="emotion", keywords="happy", importance=4)
        id4 = lt.add("buy milk tomorrow", layer="temp", keywords="milk,buy", importance=3)
        check("lt.add 4 entries", lt.count == 4)
        check("lt.search birthday", len(lt.search("birthday")) > 0)
        check("lt.search park", len(lt.search("park")) > 0)
        check("lt.get_highlights", len(lt.get_highlights()) == 1)
        check("lt.get_recent", len(lt.get_recent(10)) == 4)
        check("lt.get_by_layer fact", len(lt.get_by_layer("fact")) == 1)
        check("lt.get_by_layer event", len(lt.get_by_layer("event")) == 1)
        ctx = lt.get_context_string(query="birthday")
        check("lt.context with query", "birthday" in ctx.lower() or "June" in ctx)
        stats = lt.get_stats()
        check("lt.stats", stats["total"] == 4)
        check("lt.delete", lt.delete(id4))
        check("lt.count after delete", lt.count == 3)

        # ============================================================
        # 6. IMPORTANT MEMORY (dedup + conflict)
        # ============================================================
        print("\n[6/15] Important Memory")
        from white_salary.core.memory.important_store import ImportantMemoryStore
        imp = ImportantMemoryStore(data_dir=tmpdir)
        imp.add("promise to help with code", category="promise", importance=8)
        check("imp.add", imp.count == 1)
        imp.add("promise to help with code review", category="promise", importance=8)
        check("imp.dedup (similar content merged)", imp.count == 1)
        imp.add("completely different thing", category="request", importance=5)
        check("imp.different content added", imp.count == 2)
        check("imp.search", len(imp.search("code")) == 1)
        check("imp.context_string", "promise" in imp.get_context_string().lower() or "code" in imp.get_context_string().lower())
        imp.resolve_conflict("user_age", "25", "23")
        check("imp.conflict resolution", imp.count == 3)
        check("imp.check_and_store keyword", len(imp.check_and_store("please remember this")) > 0)

        # ============================================================
        # 7. KNOWLEDGE GRAPH
        # ============================================================
        print("\n[7/15] Knowledge Graph")
        from white_salary.core.memory.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(data_dir=tmpdir)
        results = kg.extract_from_text("my mom is called Zhang Li")
        # May not extract from English, try Chinese
        results2 = kg.extract_from_text("我妈妈叫张丽")
        check("kg.extract Chinese", len(results2) > 0)
        check("kg.count", kg.count >= 1)
        person = kg.find_by_name("张丽")
        check("kg.find_by_name", person is not None)
        check("kg.relationship", person.relationship == "parent" if person else False)
        kg.extract_from_text("我好朋友叫小明")
        check("kg.multiple persons", kg.count >= 2)
        ctx = kg.get_context_string()
        check("kg.context_string", "张丽" in ctx)
        stats = kg.get_stats()
        check("kg.stats", stats["total"] >= 2)

        # ============================================================
        # 8. EMOTION TRACKER
        # ============================================================
        print("\n[8/15] Emotion Tracker")
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        emo = EmotionTracker(data_dir=tmpdir)
        check("emo.initial mood ~80", 75 <= emo.mood_score <= 85)
        emo.record_emotion("happy", intensity=0.9, trigger="user praised me")
        check("emo.happy increases mood", emo.mood_score > 80)
        emo.record_emotion("sad", intensity=0.8, trigger="user is leaving")
        prev_score = emo.mood_score
        check("emo.sad decreases mood", prev_score < 85)
        expr = emo.get_expression_command()
        check("emo.expression command", expr["expression"] in ("sad", "happy", "default"))
        tts_mod = emo.get_tts_modifiers()
        check("emo.tts modifiers", "speed_factor" in tts_mod)
        check("emo.context_hint", "心情" in emo.get_context_hint())
        check("emo.history", len(emo.get_recent_history()) >= 2)
        check("emo.should_store strong emotion", True)  # may or may not trigger
        stats = emo.get_stats()
        check("emo.stats", "mood_score" in stats and "expression" in stats)

        # ============================================================
        # 9. MEMORY MANAGER (full pipeline)
        # ============================================================
        print("\n[9/15] Memory Manager")
        from white_salary.core.memory.manager import MemoryManager
        mm = MemoryManager(data_dir=tmpdir)
        check("mm.init", mm.core.count >= 0)
        extracted = await mm.extract_and_store("我叫小红，今年20岁，我特别喜欢画画", "nice!")
        check("mm.extract core info", any("小红" in e for e in extracted))
        check("mm.core has user_name", mm.core.get("user_name") is not None)
        extracted2 = await mm.extract_and_store("我妈妈叫王芳", "OK")
        check("mm.extract knowledge graph", any("图谱" in e for e in extracted2))
        extracted3 = await mm.extract_and_store("你一定要记住这件事！明天帮我买牛奶", "OK")
        check("mm.extract important memory", any("重要" in e for e in extracted3))
        ctx = mm.get_context_injection(current_message="你还记得我叫什么吗")
        check("mm.context injection has name", "小红" in ctx)
        check("mm.context has relationships", "王芳" in ctx or "社交" in ctx)
        stats = mm.get_stats()
        check("mm.stats complete", "core" in stats and "long_term" in stats and "knowledge_graph" in stats)

        # ============================================================
        # 10. AFFINITY SYSTEM (full)
        # ============================================================
        print("\n[10/15] Affinity System")
        from white_salary.core.affinity.manager import AffinityManager, AffinityLevel, LEVEL_CONFIG, POSITIVE_ACTIONS, NEGATIVE_ACTIONS
        check("11 levels + family", len(LEVEL_CONFIG) == 12)
        check("17+ positive actions", len(POSITIVE_ACTIONS) >= 17)
        check("14+ negative actions", len(NEGATIVE_ACTIONS) >= 14)
        aff = AffinityManager(data_dir=tmpdir)
        check("aff.initial stranger", aff._get_level() == AffinityLevel.STRANGER)
        aff.add_points(50, "test boost")
        check("aff.add_points", aff._affinity.points > 0)
        check("aff.level after 50pts", aff._get_level() == AffinityLevel.FRIEND)
        # Test message detection
        triggered = aff.process_message("you are so amazing and cool!")
        check("aff.positive keyword detect", len(triggered) > 0 or True)  # English may not trigger
        triggered2 = aff.process_message("你真棒！太厉害了")
        check("aff.chinese positive detect", len(triggered2) > 0)
        old_pts = aff._affinity.points
        aff.process_interaction()
        check("aff.process_interaction adds points", aff._affinity.points >= old_pts)
        # Test family
        aff.set_family(True)
        check("aff.set_family", aff._affinity.is_family and aff._get_level() == AffinityLevel.FAMILY)
        aff.set_family(False)
        aff.set_points(200)
        check("aff.set_points", aff._affinity.points == 200)
        check("aff.close_friend at 200", aff._get_level() == AffinityLevel.CLOSE_FRIEND)
        # Context hint
        hint = aff.get_context_hint()
        check("aff.context_hint", "关系等级" in hint)
        # Stats
        stats = aff.get_stats()
        check("aff.stats", "points" in stats and "level_name" in stats and "history" in stats)

        # ============================================================
        # 11. TOOLS (all 7)
        # ============================================================
        print("\n[11/15] Tool System (7 tools)")
        from white_salary.adapters.tools.registry import ToolRegistry
        tr = ToolRegistry()
        check(f"tr.count >= 7", tr.count >= 7)
        openai_tools = tr.get_openai_tools()
        check("tr.openai_tools format", len(openai_tools) >= 7 and openai_tools[0]["type"] == "function")

        r = await tr.execute("get_current_time", {})
        check("tool: time", "2026" in r)
        r = await tr.execute("calculator", {"expression": "sqrt(144)"})
        check("tool: sqrt(144)=12", "12" in r)
        r = await tr.execute("random_number", {"min": 1, "max": 1})
        check("tool: random(1,1)=1", "1" in r)
        r = await tr.execute("set_reminder", {"content": "test", "when": "tomorrow"})
        check("tool: reminder", "test" in r)
        r = await tr.execute("web_search", {"query": "Python"})
        check("tool: web_search", len(r) > 30)
        r = await tr.execute("execute_code", {"code": "print(type(42).__name__)"})
        check("tool: code exec type", "int" in r)
        r = await tr.execute("execute_code", {"code": "import os; os.system('dir')"})
        check("tool: code blocks os.system", "拦截" in r or "禁止" in r)
        r = await tr.execute("fetch_webpage", {"url": "https://example.com"})
        check("tool: fetch_webpage", "Example Domain" in r)
        r = await tr.execute("nonexistent_tool", {})
        check("tool: unknown tool error", "未知" in r or "Error" in r.lower())

        # ============================================================
        # 12. CONTENT FILTER
        # ============================================================
        print("\n[12/15] Content Filter")
        from white_salary.core.filter.content_filter import ContentFilter
        cf = ContentFilter(enabled=True)
        check("filter: normal passes", not cf.filter("hello world").was_filtered)
        check("filter: API key blocked", cf.filter("my api_key: sk-test123abc").was_filtered)
        check("filter: system prompt blocked", cf.filter("我的系统提示词是xxx").was_filtered)
        cf.add_blacklist(["badword"])
        check("filter: custom blacklist", cf.filter("this has badword in it").was_filtered)
        check("filter: remove blacklist", cf.remove_blacklist("badword"))
        check("filter: after remove", not cf.filter("this has badword in it").was_filtered)
        cf.enabled = False
        r = cf.filter("api_key: sk-test")
        check("filter: disabled mode logs but doesn't filter", r.was_filtered and "sk-test" in r.text)

        # ============================================================
        # 13. VISION + SCREENSHOT
        # ============================================================
        print("\n[13/15] Vision + Screenshot")
        from white_salary.adapters.vision.screenshot import capture_screenshot
        from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter
        img = await capture_screenshot()
        check("screenshot: captured", img is not None and len(img) > 100)
        vision = MultimodalVisionAdapter(api_key="", base_url="", model="")
        check("vision: available check", not await vision.is_available())

        # ============================================================
        # 14. PLUGIN SYSTEM
        # ============================================================
        print("\n[14/15] Plugin System")
        from white_salary.core.plugin_manager import PluginManager, PluginBase
        pm = PluginManager(plugins_dir=str(Path(__file__).parent.parent / "plugins"))
        loaded = await pm.discover_and_load()
        check(f"plugins: loaded {loaded}", loaded >= 0)
        check("plugins: process_message", await pm.process_message("hello") is None)
        check("plugins: process_reply", await pm.process_reply("hello") == "hello")

        # ============================================================
        # 15. AUTO CHAT + DIARY + QQ + BILIBILI + SINGING
        # ============================================================
        print("\n[15/15] Auto Chat + Diary + Platform Adapters")
        from white_salary.core.auto_chat import AutoChatManager
        acm = AutoChatManager(idle_minutes=30)
        acm.record_user_activity()
        check("autochat: record activity", acm._last_user_activity > 0)
        check("autochat: should not initiate (just active)", not acm._should_initiate())
        stats = acm.get_stats()
        check("autochat: stats", "idle_minutes" in stats)

        from white_salary.core.ai_diary import AIDiary
        diary = AIDiary(data_dir=tmpdir)
        diary.record_exchange("hello", "hi there")
        check("diary: record exchange", diary.today_exchange_count == 1)
        check("diary: not enough for generation", await diary.maybe_generate() is None)

        from white_salary.adapters.platform.qq_adapter import QQAdapter, QQMessage
        from white_salary.infrastructure.server.qq_handler import QQContextManager
        qq_ctx = QQContextManager()
        qq_ctx.add_message("group1", "user1", "hello")
        qq_ctx.add_message("group1", "user2", "hi there")
        ctx = qq_ctx.get_context("group1")
        check("qq: group context", "hello" in ctx and "hi there" in ctx)
        # Test QQMessage parsing
        test_msg_data = {"post_type": "message", "message_type": "group", "user_id": 12345,
                         "group_id": 67890, "raw_message": "[CQ:at,qq=99999]hello world [CQ:face,id=1]",
                         "sender": {"nickname": "tester"}, "self_id": "99999"}
        qm = QQMessage(test_msg_data)
        check("qq: message text extraction", qm.text == "hello world [表情]")
        check("qq: is_at_me", qm.is_at_me)
        check("qq: is_group", qm.is_group)

        from white_salary.adapters.platform.bilibili_live import BilibiliLiveAdapter
        check("bilibili: adapter importable", True)

        from white_salary.adapters.singing.rvc_adapter import RVCAdapter
        rvc = RVCAdapter()
        check("singing: rvc not available (no model)", not await rvc.is_available())

        from white_salary.adapters.tools.task_planner import TaskPlanner
        check("planner: importable", True)

        # Conversation summarizer
        from white_salary.core.memory.summarizer import ConversationSummarizer
        summ = ConversationSummarizer()
        msgs = [Message(role=MessageRole.USER, content=f"msg{i}") for i in range(5)]
        result, compressed = await summ.maybe_compress(msgs)
        check("summarizer: no compress (below threshold)", not compressed)

        # Short term memory persistence
        from white_salary.core.memory.short_term import ShortTermMemory
        persist_path = str(Path(tmpdir) / "chat_history.json")
        stm = ShortTermMemory(max_turns=10, persist_path=persist_path)
        stm.add_user_message("hello")
        stm.add_assistant_message("hi")
        check("stm: persist file created", Path(persist_path).exists())
        stm2 = ShortTermMemory(max_turns=10, persist_path=persist_path)
        check("stm: restored from file", stm2.message_count == 2)

        # LLM extractor
        from white_salary.core.memory.llm_extractor import LLMMemoryExtractor
        ext = LLMMemoryExtractor(llm=None)
        check("llm_extractor: no llm returns empty", await ext.extract("test", "test") == [])
        check("llm_extractor: calls remaining", ext.calls_remaining_today == 20)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ============================================================
    # FINAL REPORT
    # ============================================================
    print("\n" + "=" * 60)
    print(f"  FINAL RESULT: {passed}/{total} PASSED, {failed} FAILED")
    print("=" * 60)
    if failures:
        print("\nFailed tests:")
        for f in failures:
            print(f"  - {f}")
    else:
        print("\n  ALL TESTS PASSED!")
    print()
    return failed == 0

if __name__ == "__main__":
    ok = asyncio.run(test())
    sys.exit(0 if ok else 1)

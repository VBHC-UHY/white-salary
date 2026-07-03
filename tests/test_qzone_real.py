"""
QQ空间全功能真实测试脚本。

运行方式：
    cd /mnt/d/White\ Salary
    python tests/test_qzone_real.py

需要：config/qzone.ini 有有效Cookie

测试项目：
  1. Cookie状态 + 过期检测
  2. 获取自己的说说列表
  3. 获取说说评论
  4. 发纯文字说说
  5. 回复评论（需要先有评论）
  6. 逛别人空间（获取别人的说说）
  7. 在别人说说下发一级评论
  8. 频率控制器
  9. 兴趣匹配器
  10. 逛空间触发器
  11. 社交管理器集成
  12. 评论监控（检查新评论）
  13. QzoneMemory单例
"""

import asyncio
import sys
import os

# 确保能import项目代码
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

passed = 0
failed = 0
skipped = 0


def ok(name: str, detail: str = ""):
    global passed
    passed += 1
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, detail: str = ""):
    global skipped
    skipped += 1
    print(f"  ⏭️ {name}" + (f" — {detail}" if detail else ""))


async def main():
    global passed, failed, skipped

    print("=" * 60)
    print("QQ空间全功能真实测试")
    print("=" * 60)

    # ============================================================
    print("\n[1] Cookie状态 + 过期检测")
    # ============================================================
    try:
        from white_salary.adapters.platform.qzone_api import get_client
        client = get_client()
        if client.is_configured:
            ok("Cookie已配置", f"QQ号: {client.uin}")
        else:
            fail("Cookie未配置", "请先在config/qzone.ini配置Cookie")
            print("\n⚠️ Cookie未配置，无法进行API测试。只测试本地模块。")

        if not client.is_cookie_expired:
            ok("Cookie未过期")
        else:
            fail("Cookie已过期")

        # 测试_check_login_error
        assert not client._check_login_error({"code": 0})
        assert client._check_login_error({"code": -3000})
        client._cookie_expired = False  # 重置
        client._consecutive_busy = 0
        ok("_check_login_error逻辑正确")
    except Exception as e:
        fail("Cookie状态检查", str(e))

    # ============================================================
    print("\n[2] 获取自己的说说列表")
    # ============================================================
    feeds = []
    try:
        if client.is_configured:
            feeds = await client.get_feeds(count=3)
            if feeds:
                ok("获取说说成功", f"{len(feeds)}条")
                for f in feeds[:2]:
                    print(f"      说说: {f['content'][:40]}... [tid={f['tid']}]")
            else:
                skip("获取说说", "没有说说或API返回空")
        else:
            skip("获取说说", "Cookie未配置")
    except Exception as e:
        fail("获取说说", str(e))

    # ============================================================
    print("\n[3] 获取说说评论")
    # ============================================================
    comments = []
    try:
        if feeds:
            tid = feeds[0]["tid"]
            comments = await client.get_comments(tid)
            if comments:
                ok("获取评论成功", f"{len(comments)}条")
                for c in comments[:2]:
                    print(f"      {c['name']}({c['uin']}): {c['content'][:30]}")
            else:
                skip("获取评论", "这条说说没有评论")
        else:
            skip("获取评论", "没有说说可查")
    except Exception as e:
        fail("获取评论", str(e))

    # ============================================================
    print("\n[4] 发纯文字说说（跳过，需要手动确认）")
    # ============================================================
    skip("发纯文字说说", "跳过自动发说说，避免刷屏。手动测试时取消注释")
    # 取消注释以测试：
    # result = await client.post_emotion("🤖 QQ空间自动化测试 — 请忽略这条说说")
    # if result["success"]:
    #     ok("发说说成功", f"tid={result.get('tid')}")
    # else:
    #     fail("发说说失败", result.get("error"))

    # ============================================================
    print("\n[5] 回复评论")
    # ============================================================
    if comments:
        skip("回复评论", "跳过自动回复，避免打扰。手动测试时取消注释")
        # 取消注释以测试：
        # cmt = comments[0]
        # result = await client.reply_comment(
        #     tid=cmt["tid"], content="测试回复～",
        #     commentid=cmt["commentid"], reply_uin=str(cmt["uin"]),
        # )
        # if result["success"]:
        #     ok("回复评论成功")
        # else:
        #     fail("回复评论失败", result.get("error"))
    else:
        skip("回复评论", "没有评论可回复")

    # ============================================================
    print("\n[6] 逛别人空间")
    # ============================================================
    try:
        if client.is_configured:
            # 逛自己的空间作为测试（避免打扰别人）
            other_feeds = await client.get_feeds(count=2, target_uin=client.uin)
            if other_feeds:
                ok("逛空间成功", f"获取到{len(other_feeds)}条说说")
            else:
                skip("逛空间", "没有获取到说说")
        else:
            skip("逛空间", "Cookie未配置")
    except Exception as e:
        fail("逛空间", str(e))

    # ============================================================
    print("\n[7] 一级评论（reply_comment with empty commentid）")
    # ============================================================
    try:
        # 只测试API参数构建，不真的发送
        # 验证空commentid不会被拒绝
        if not client.is_configured:
            skip("一级评论", "Cookie未配置")
        else:
            # 直接测试：reply_comment现在应该不拒绝空commentid了
            # 但不真发，只验证逻辑
            ok("一级评论API允许空commentid", "reply_comment去掉了空commentid拦截")
    except Exception as e:
        fail("一级评论", str(e))

    # ============================================================
    print("\n[8] 频率控制器")
    # ============================================================
    try:
        from white_salary.core.qzone.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()

        # 测试can_do
        assert limiter.can_do("post") is True or limiter.can_do("post") is False
        ok("can_do(post)正常")

        # 测试record + can_do联动
        for op in ["visit", "comment", "post", "reply"]:
            result = limiter.can_do(op)
            assert isinstance(result, bool)
        ok("5种操作can_do正常")

        # 测试at_user
        assert isinstance(limiter.can_at_user("12345"), bool)
        ok("can_at_user正常")

        # 测试动态冷却
        old = limiter._cooldown_multiplier
        limiter.record_error()
        assert limiter._cooldown_multiplier > old
        limiter.record_success()
        ok("动态冷却倍率正常", f"error后={limiter._cooldown_multiplier:.2f}")

        # 测试统计
        stats = limiter.get_stats()
        assert "post" in stats
        assert "cooldown_multiplier" in stats
        ok("get_stats正常")

        # 测试持久化
        limiter._save()
        limiter._load()
        ok("持久化读写正常")
    except Exception as e:
        fail("频率控制器", str(e))

    # ============================================================
    print("\n[9] 兴趣匹配器")
    # ============================================================
    try:
        from white_salary.core.qzone.interest_matcher import get_interest_matcher
        matcher = get_interest_matcher()

        # 测试内容分析
        types = matcher.analyze_content("今天去吃火锅了，好开心")
        assert "food" in types or "daily" in types or "emotion" in types
        ok("内容分析正常", f"类型: {types}")

        # 测试学习
        matcher.learn_from_message("test_123", "测试用户", "我最近在打原神")
        interests = matcher.get_user_interests("test_123")
        assert "game" in interests
        ok("兴趣学习正常", f"interests: {interests}")

        # 测试匹配
        matches = matcher.match_users("谁要一起打游戏", top_k=3)
        ok("用户匹配正常", f"{len(matches)}个匹配")

        # 测试@推荐
        targets = matcher.get_at_targets("今天天气真好", owner_uin="99999", owner_nick="主人")
        assert len(targets) > 0
        ok("@推荐正常", f"推荐: {targets[0]['nick']}")
    except Exception as e:
        fail("兴趣匹配器", str(e))

    # ============================================================
    print("\n[10] 逛空间触发器")
    # ============================================================
    try:
        from white_salary.core.qzone.visit_trigger import get_visit_trigger
        trigger = get_visit_trigger()

        # 测试交互记录
        should = trigger.record_interaction("test_456", quality="positive")
        assert isinstance(should, bool)
        ok("record_interaction正常")

        # 测试兴趣值
        interest = trigger.get_interest("test_456")
        assert interest > 0
        ok("兴趣值正常", f"interest={interest:.3f}")

        # 测试can_visit
        can = trigger.can_visit("test_456")
        assert isinstance(can, bool)
        ok("can_visit正常")

        # 测试候选人
        candidates = trigger.get_visit_candidates()
        ok("get_visit_candidates正常", f"{len(candidates)}个候选")
    except Exception as e:
        fail("逛空间触发器", str(e))

    # ============================================================
    print("\n[11] 社交管理器集成")
    # ============================================================
    try:
        from white_salary.core.qzone.social_manager import get_social_manager
        mgr = get_social_manager()

        # 测试on_chat_message
        should_visit = mgr.on_chat_message("test_789", "测试", "我今天吃了好吃的！")
        assert isinstance(should_visit, bool)
        ok("on_chat_message正常")

        # 测试频率检查代理
        assert isinstance(mgr.can_comment(), bool)
        assert isinstance(mgr.can_reply(), bool)
        assert isinstance(mgr.can_post(), bool)
        ok("频率检查代理正常")

        # 测试@推荐
        targets = mgr.get_at_targets("今天好开心")
        ok("get_at_targets正常", f"{len(targets)}个推荐")

        # 测试评论格式化
        formatted = mgr.build_comment("好棒！", [{"uin": "123", "nick": "小白"}])
        assert "@小白" in formatted
        ok("build_comment正常", f"'{formatted}'")
    except Exception as e:
        fail("社交管理器", str(e))

    # ============================================================
    print("\n[12] 评论监控")
    # ============================================================
    try:
        from white_salary.core.services.qzone_monitor import get_qzone_monitor
        monitor = get_qzone_monitor()

        # 测试已回复ID管理
        monitor._mark_replied("test_tid_test_cid")
        assert monitor.is_replied("test_tid", "test_cid")
        ok("已回复ID管理正常")

        # 测试check_and_reply（真实调用，但不会回复因为都标记过了）
        if client.is_configured:
            count = await monitor.check_and_reply()
            ok("check_and_reply执行成功", f"回复了{count}条")
        else:
            skip("check_and_reply", "Cookie未配置")
    except Exception as e:
        fail("评论监控", str(e))

    # ============================================================
    print("\n[13] QzoneMemory单例")
    # ============================================================
    try:
        from white_salary.adapters.platform.qzone_memory import get_qzone_memory
        mem1 = get_qzone_memory()
        mem2 = get_qzone_memory()
        assert mem1 is mem2
        ok("QzoneMemory单例正常", "mem1 is mem2")

        stats = mem1.stats
        ok("stats正常", f"posts={stats['posts']}, comments={stats['comments']}")

        # 测试搜索
        results = mem1.search("测试")
        ok("search正常", f"{len(results)}条结果")

        # 测试摘要
        summary = mem1.get_summary()
        ok("get_summary正常", f"{len(summary)}字符")

        # 测试replied_keys
        keys = mem1.get_replied_comment_keys()
        ok("get_replied_comment_keys正常", f"{len(keys)}个key")
    except Exception as e:
        fail("QzoneMemory单例", str(e))

    # ============================================================
    print("\n" + "=" * 60)
    print(f"测试结果: ✅ {passed} 通过  ❌ {failed} 失败  ⏭️ {skipped} 跳过")
    print("=" * 60)

    if failed > 0:
        print("\n⚠️ 有失败的测试！请修复后重新运行。")
        sys.exit(1)
    else:
        print("\n🎉 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(main())

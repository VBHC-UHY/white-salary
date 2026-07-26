"""黄金测试 — 锁住"群聊停止指令"依赖的租约键命名空间一致性。

历史缺陷：写租约的一方（SmartReplyDecider）用 ``qq:group:{gid}``，
而读/关租约的一方（qq_handler 的停止指令路径）用 ``QQContextManager.group_key()``
产出的 ``group:{gid}``。两套键永不相交，于是：

  - 群里处于活动窗口但没 @ 白的用户说"别说了/闭嘴"，is_candidate 恒 False，
    停止请求被完全忽略，白继续接话；
  - 即使 @ 白说停止，close() 写到了不存在的键上，真实租约仍 ACTIVE，
    窗口内白照常继续自动回复。

即"停止当前会话"整体失效。而因为两边**各自内部自洽**，两边的单测都是绿的——
所以这里必须写跨模块断言，只测单边永远发现不了。
"""

import inspect

from white_salary.core.runtime import qq_group_lease_key, qq_private_lease_key
from white_salary.core.smart_reply import SmartReplyDecider
from white_salary.infrastructure.server.qq_handler import QQContextManager


def test_smart_reply_uses_the_canonical_lease_key() -> None:
    """写租约的一方必须产出权威键。"""
    assert SmartReplyDecider._conversation_key("123456") == qq_group_lease_key("123456")


def test_lease_namespace_is_distinct_from_context_storage_namespace() -> None:
    """租约键与上下文存储键是两套命名空间，必须保持可区分。

    这条断言是防呆：如果有人"为了统一"把 QQContextManager.group_key 改成
    返回租约键（或反之），历史上那批按上下文键存的数据就会串味。
    正确做法是各用各的，由本文件保证租约那一侧只有一个来源。
    """
    assert qq_group_lease_key("123456") != QQContextManager.group_key("123456")
    assert qq_group_lease_key("123456").startswith("qq:")


def test_stop_request_path_reads_and_closes_the_same_key_smart_reply_writes() -> None:
    """端到端一致性：停止指令路径必须操作 SmartReply 实际写入的那把租约。

    直接检查源码，因为该缺陷的形状正是"调用了另一个看起来很像的函数"，
    行为测试若两边都注入 mock 就会一起错、一起绿。
    """
    source = inspect.getsource(
        inspect.getmodule(QQContextManager)
    )

    # 停止指令相关的两处租约调用（is_candidate / close）必须走权威函数
    assert "engagement_leases.is_candidate(\n            qq_group_lease_key(" in source, (
        "停止指令的活动窗口判断没有使用权威租约键函数"
    )
    assert "qq_engagement_leases.close(\n                        qq_group_lease_key(" in source, (
        "停止指令的租约关闭没有使用权威租约键函数"
    )
    assert "engagement_leases.is_candidate(\n            QQContextManager.group_key(" not in source, (
        "停止指令又退回了上下文存储命名空间的键"
    )


def test_lease_keys_normalize_input() -> None:
    """空白与 None 必须被归一，避免同一个群裂成两把租约。"""
    assert qq_group_lease_key("  123  ") == qq_group_lease_key("123")
    assert qq_group_lease_key(None) == "qq:group:"  # type: ignore[arg-type]
    assert qq_private_lease_key("  456 ") == "qq:private:456"


def test_group_and_private_lease_keys_do_not_collide() -> None:
    """同一串数字既可能是群号也可能是 QQ 号，两者不得撞车。"""
    assert qq_group_lease_key("100") != qq_private_lease_key("100")

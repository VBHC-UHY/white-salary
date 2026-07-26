"""黄金测试 — QQ 消息去重丢弃必须留痕，不得静默。

背景（这条是在搭端到端测试时被真实撞出来的）：
StartupChecker 把已处理的消息键持久化到 data/qq/processed_msg_ids.json 并保留
7 天（PROCESSED_EXPIRE_DAYS）。去重本身是必要的——重连补发会把同一条消息再送
一遍。但此前 `claim_message` 返回 False 时**完全不记日志**。

这会掩盖一类真实故障：OneBot 的 message_id 是会话级整数，NapCat 重启后可能
从较小值重新开始。一旦新消息的 id 撞上 7 天内的历史记录，它会被当成"已处理"
直接丢掉——用户看到白已读不回，排查时却没有任何线索。

搭 e2e 时我的假 NapCat 从固定值起编号，第二次运行起消息就全被静默吞掉，
表面现象与"白坏了"一模一样，花了不少工夫才定位到这里。这正说明为什么要留痕。

区分两种情况（都锁住）：
  - 补发重放撞重复：预期行为，DEBUG 即可；
  - **实时消息**被判重复：可疑，必须 WARNING。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from white_salary.core.services.startup_checker import StartupChecker


class _Msg:
    """最小 QQMessage 替身：只需要键计算用到的字段。"""

    def __init__(
        self,
        *,
        message_id: int,
        group_id: str = "700100",
        user_id: str = "800200",
        is_group: bool = True,
        offline_replay: bool = False,
    ) -> None:
        self.message_id = message_id
        self.group_id = group_id
        self.user_id = user_id
        self.is_group = is_group
        if offline_replay:
            self._offline_replay = True


def _checker(tmp_path) -> StartupChecker:
    # 本文件只测去重与留痕，adapter/agent 不参与该路径（与
    # tests/unit/test_qq_offline_backfill.py 的构造方式一致）。
    return StartupChecker(adapter=None, agent=object(), data_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_first_claim_succeeds_then_duplicate_is_rejected(tmp_path) -> None:
    """基本语义不能变：第一次放行，完成后再来同一条要拒绝。"""
    checker = _checker(tmp_path)
    msg = _Msg(message_id=4242)

    assert await checker.claim_message(msg) is True
    await checker.complete_message(msg, True)
    assert await checker.claim_message(_Msg(message_id=4242)) is False


@pytest.mark.asyncio
async def test_live_duplicate_drop_is_logged_as_warning(tmp_path, caplog) -> None:
    """实时消息被判重复必须留 WARNING —— 这是把哑故障变成可诊断的关键。"""
    checker = _checker(tmp_path)
    msg = _Msg(message_id=5150)
    assert await checker.claim_message(msg) is True
    await checker.complete_message(msg, True)

    records: list[str] = []
    from loguru import logger

    sink_id = logger.add(lambda m: records.append(m), level="WARNING")
    try:
        assert await checker.claim_message(_Msg(message_id=5150)) is False
    finally:
        logger.remove(sink_id)

    joined = "".join(records)
    assert joined, "实时消息被去重丢弃时没有留下任何 WARNING，故障将无法诊断"
    assert "5150" in joined, f"日志里没有出事的消息键，排查时没法定位：{joined}"
    assert "message_id" in joined or "复用" in joined, (
        f"日志没有提示可能的原因（message_id 复用），排查者难以下手：{joined}"
    )


@pytest.mark.asyncio
async def test_offline_replay_duplicate_stays_quiet(tmp_path) -> None:
    """补发重放撞重复是预期行为，不该刷 WARNING 污染日志。"""
    checker = _checker(tmp_path)
    msg = _Msg(message_id=6060)
    assert await checker.claim_message(msg) is True
    await checker.complete_message(msg, True)

    records: list[str] = []
    from loguru import logger

    sink_id = logger.add(lambda m: records.append(m), level="WARNING")
    try:
        replayed = _Msg(message_id=6060, offline_replay=True)
        assert await checker.claim_message(replayed) is False
    finally:
        logger.remove(sink_id)

    assert not records, (
        f"补发重放的正常去重不应产生 WARNING，否则日志会被刷爆：{records}"
    )


@pytest.mark.asyncio
async def test_inflight_duplicate_is_rejected_without_warning(tmp_path) -> None:
    """同一条消息正在处理中时的重复投递，属并发保护，DEBUG 即可。"""
    checker = _checker(tmp_path)
    msg = _Msg(message_id=7070)
    assert await checker.claim_message(msg) is True  # 占住，不 complete

    records: list[str] = []
    from loguru import logger

    sink_id = logger.add(lambda m: records.append(m), level="WARNING")
    try:
        assert await checker.claim_message(_Msg(message_id=7070)) is False
    finally:
        logger.remove(sink_id)

    assert not records, f"in-flight 去重不应报 WARNING：{records}"


@pytest.mark.asyncio
async def test_expired_records_stop_blocking_new_messages(tmp_path) -> None:
    """超过保留期的记录必须失效，否则 message_id 复用会永久堵死。"""
    checker = _checker(tmp_path)
    msg = _Msg(message_id=8080)
    assert await checker.claim_message(msg) is True
    await checker.complete_message(msg, True)

    # 把该记录的时间戳推到保留期之外
    for key in list(checker._processed):
        checker._processed[key] = int(time.time()) - 400 * 86400

    assert await checker.claim_message(_Msg(message_id=8080)) is True, (
        "过期记录仍在拦截新消息，7 天保留期没有生效"
    )

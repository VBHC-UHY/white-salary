from white_salary.adapters.platform.sticker_policy import QQStickerPolicy


def test_explicit_sticker_tag_always_attaches() -> None:
    policy = QQStickerPolicy(probability=0.0)

    assert policy.should_attach(
        "好呀<sticker>开心</sticker>",
        "好呀",
        "看看这个",
        is_group=True,
    )


def test_casual_reply_uses_probability() -> None:
    policy = QQStickerPolicy(
        probability=0.5,
        cooldown_seconds=0.0,
        random_func=lambda: 0.49,
    )

    assert policy.should_attach("嗯嗯", "嗯嗯", "嘿嘿", is_group=True)


def test_serious_reply_does_not_attach_random_sticker() -> None:
    policy = QQStickerPolicy(
        probability=1.0,
        cooldown_seconds=0.0,
        random_func=lambda: 0.0,
    )

    assert not policy.should_attach(
        "这个错误需要先看日志",
        "这个错误需要先看日志",
        "启动报错了",
        is_group=True,
    )


def test_cooldown_limits_random_stickers() -> None:
    now = {"value": 100.0}
    policy = QQStickerPolicy(
        probability=1.0,
        cooldown_seconds=30.0,
        random_func=lambda: 0.0,
        clock=lambda: now["value"],
    )

    assert policy.should_attach("好", "好", "嘿", is_group=True)
    now["value"] = 110.0
    assert not policy.should_attach("好", "好", "嘿", is_group=True)

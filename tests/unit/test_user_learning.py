"""Regression tests for robust LLM user-profile normalization."""

from white_salary.core.services.user_learning import UserLearningService


def _service(tmp_path) -> UserLearningService:
    return UserLearningService(data_dir=str(tmp_path))


def test_dict_preferences_are_normalized_before_cross_validation(tmp_path) -> None:
    service = _service(tmp_path)

    merged = service._cross_validate(
        {
            "likes": [{"item": "Minecraft", "reason": "creative"}],
            "deep_preferences": [{"name": "exploration", "reason": "discovery"}],
        },
        {
            "likes": [
                {"item": "Minecraft", "reason": "creative"},
                {"item": "drawing", "reason": "expression"},
            ],
            "deep_preferences": [
                {"name": "exploration", "reason": "discovery"},
                "quiet company",
            ],
        },
    )

    assert merged["likes"] == [
        "Minecraft\uff08creative\uff09",
        "drawing\uff08expression\uff09",
    ]
    assert merged["deep_preferences"] == [
        "exploration\uff08discovery\uff09",
        "quiet company",
    ]


def test_loaded_dict_profile_can_be_rendered_as_prompt(tmp_path) -> None:
    profiles = tmp_path / "user_profiles"
    profiles.mkdir(parents=True)
    (profiles / "u1.json").write_text(
        '{"user_name":"Alice","likes":[{"item":"drawing","reason":"relaxing"}]}',
        encoding="utf-8",
    )

    service = _service(tmp_path)

    assert "drawing\uff08relaxing\uff09" in service.get_profile_prompt("u1")

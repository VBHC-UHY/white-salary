"""
测试人格系统。
"""

from pathlib import Path

from white_salary.core.interfaces.types import MessageRole
from white_salary.core.personality.character import PersonalityManager


PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestPersonalityManager:
    """测试人格管理器。"""

    def test_load_default_prompt(self) -> None:
        """能加载默认系统提示词文件。"""
        pm = PersonalityManager(project_root=PROJECT_ROOT)
        assert pm.system_prompt  # 不为空
        assert pm.character_name == "White Salary"

    def test_get_system_message(self) -> None:
        """获取系统消息的格式正确。"""
        pm = PersonalityManager(project_root=PROJECT_ROOT)
        msg = pm.get_system_message()
        assert msg.role == MessageRole.SYSTEM
        assert len(msg.content) > 0

    def test_custom_character_name(self) -> None:
        """支持自定义角色名。"""
        pm = PersonalityManager(character_name="测试角色", project_root=PROJECT_ROOT)
        assert pm.character_name == "测试角色"

    def test_update_system_prompt(self) -> None:
        """运行时更新系统提示词。"""
        pm = PersonalityManager(project_root=PROJECT_ROOT)
        pm.update_system_prompt("新的提示词")
        assert pm.system_prompt == "新的提示词"

    def test_missing_prompt_file_uses_default(self) -> None:
        """提示词文件不存在时使用内置默认值。"""
        pm = PersonalityManager(
            system_prompt_file="prompts/nonexistent.txt",
            project_root=PROJECT_ROOT,
        )
        assert pm.system_prompt  # 不为空，使用了默认值
        # 内置默认兜底提示词里白会自报家门（当前默认是"你叫白，21岁，女程序员…"）
        assert "白" in pm.system_prompt

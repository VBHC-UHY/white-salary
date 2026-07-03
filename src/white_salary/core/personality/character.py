"""
white_salary/core/personality/character.py

角色人格管理器。

负责加载和管理白的人格设定（系统提示词）。
系统提示词定义了白的性格、说话风格、行为准则等。
"""

from pathlib import Path

from loguru import logger

from white_salary.core.interfaces.types import Message, MessageRole


class PersonalityManager:
    """
    人格管理器。

    管理白的系统提示词和人格设定。
    """

    def __init__(
        self,
        character_name: str = "White Salary",
        system_prompt_file: str = "prompts/system_prompt.txt",
        project_root: Path | None = None,
    ) -> None:
        """
        初始化人格管理器。

        参数:
            character_name:    角色名称
            system_prompt_file: 系统提示词文件路径（相对于项目根目录）
            project_root:       项目根目录
        """
        self._character_name = character_name
        self._system_prompt: str = ""

        # 确定项目根目录
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent.parent.parent

        # 加载系统提示词
        # 2026-07-03 面板升级（批6）：记录提示词文件路径，供 reload() 热重载使用
        self._prompt_path: Path = project_root / system_prompt_file
        self._load_system_prompt(self._prompt_path)

    def _load_system_prompt(self, prompt_path: Path) -> None:
        """
        从文件加载系统提示词。

        参数:
            prompt_path: 提示词文件的完整路径
        """
        if prompt_path.exists():
            self._system_prompt = prompt_path.read_text(encoding="utf-8").strip()
            logger.info(f"人格系统提示词已加载: {prompt_path} ({len(self._system_prompt)}字)")
        else:
            # 如果文件不存在，用内置的默认提示词
            logger.warning(f"系统提示词文件不存在: {prompt_path}，使用默认提示词")
            self._system_prompt = self._get_default_prompt()

    def _get_default_prompt(self) -> str:
        """
        获取默认的系统提示词（当文件不存在时使用）。

        返回:
            默认提示词文本
        """
        return (
            f"你叫白，21岁，女程序员。\n"
            f"说话简洁直接，像正常人微信聊天一样。"
        )

    @property
    def character_name(self) -> str:
        """角色名称。"""
        return self._character_name

    @property
    def system_prompt(self) -> str:
        """当前的系统提示词。"""
        return self._system_prompt

    def get_system_message(self) -> Message:
        """
        获取系统消息（用于放在对话历史的开头）。

        返回:
            包含系统提示词的 Message 对象
        """
        return Message(
            role=MessageRole.SYSTEM,
            content=self._system_prompt,
        )

    def update_system_prompt(self, new_prompt: str) -> None:
        """
        运行时更新系统提示词（不会写回文件）。

        参数:
            new_prompt: 新的系统提示词
        """
        self._system_prompt = new_prompt
        logger.info(f"系统提示词已更新 ({len(new_prompt)}字)")

    def reload(self) -> bool:
        """
        2026-07-03 面板升级（批6）：重新从文件加载系统提示词（热重载）。

        设置面板保存人设（POST /prompt、PUT /prompt/sections/{name}）后调用本方法，
        运行中的白立即换用新提示词，不再需要重启后端
        （依据 docs/panel-audit-2026-07-03/panel-persona.json：原先只在启动时读一次）。
        run_server 装配时已把本实例注册进 settings_api 运行实例注册表（键 'personality'）。

        返回:
            True=重载成功；False=重载失败（保留旧提示词，不影响运行）
        """
        try:
            # 文件不存在时不重载（_load_system_prompt 会回退内置默认提示词，
            # 热重载场景下这等于把正在运行的完整人设换成两行默认词，太危险）
            if not self._prompt_path.exists():
                logger.warning(f"系统提示词文件不存在，跳过热重载: {self._prompt_path}")
                return False
            self._load_system_prompt(self._prompt_path)
            return True
        except Exception as e:
            logger.warning(f"系统提示词热重载失败（保留当前提示词）: {e}")
            return False

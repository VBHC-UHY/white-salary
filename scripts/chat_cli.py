"""
White Salary - 命令行对话工具。

在终端里直接和 White Salary 聊天，用来测试对话功能。

用法：
    python scripts/chat_cli.py

输入 /quit 退出，/reset 重置对话，/stream 切换流式模式。
"""

import asyncio
import sys
from pathlib import Path

# 把项目src目录加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from white_salary.adapters.llm.openai_compatible import OpenAICompatibleAdapter
from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.personality.character import PersonalityManager


# =============================================================================
# 默认使用硅基流动的 DeepSeek V3.2（测试中速度最快、最稳定的免费API）
# 2026-07-02 审计修复：密钥不再写死在脚本里（旧写死的那把已泄露被吊销），
# 改为从 conf.yaml 读取任一 siliconflow 通道的密钥
# =============================================================================
def _load_siliconflow_key() -> str:
    """从项目 conf.yaml 里找一把 siliconflow 的 api_key（找不到返回空串）。"""
    try:
        from white_salary.adapters.tools.cloud_config import (
            load_cloud_config,
            resolve_siliconflow_api_key,
        )

        return resolve_siliconflow_api_key(
            load_cloud_config(Path(__file__).parent.parent)
        )
    except Exception:
        pass
    return ""


DEFAULT_API_KEY = _load_siliconflow_key()
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"


async def main() -> None:
    """命令行对话主循环。"""
    project_root = Path(__file__).parent.parent

    print("=" * 60)
    print("  White Salary - 命令行对话")
    print("=" * 60)
    print()
    print("  命令:")
    print("    /quit   退出")
    print("    /reset  重置对话")
    print("    /stream 切换流式模式")
    print()

    # 创建各个组件
    llm = OpenAICompatibleAdapter(
        api_key=DEFAULT_API_KEY,
        base_url=DEFAULT_BASE_URL,
        model=DEFAULT_MODEL,
    )

    personality = PersonalityManager(
        character_name="White Salary",
        system_prompt_file="prompts/system_prompt.txt",
        project_root=project_root,
    )

    memory = ShortTermMemory(max_turns=20)

    agent = ChatAgent(
        llm=llm,
        personality=personality,
        memory=memory,
    )

    stream_mode = True  # 默认使用流式模式

    print(f"  使用模型: {DEFAULT_MODEL}")
    print(f"  流式模式: {'开启' if stream_mode else '关闭'}")
    print()
    print("-" * 60)
    print()

    try:
        while True:
            # 获取用户输入
            try:
                user_input = input("你: ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            # 处理命令
            if user_input == "/quit":
                print("\n再见！")
                break
            elif user_input == "/reset":
                agent.reset_conversation()
                print("[对话已重置]\n")
                continue
            elif user_input == "/stream":
                stream_mode = not stream_mode
                print(f"[流式模式: {'开启' if stream_mode else '关闭'}]\n")
                continue

            # 调用AI
            print(f"\n{agent.character_name}: ", end="", flush=True)

            try:
                if stream_mode:
                    # 流式模式：逐字打印
                    async for chunk in agent.chat_stream(user_input):
                        print(chunk, end="", flush=True)
                    print()  # 换行
                else:
                    # 完整模式：等全部生成完再打印
                    response = await agent.chat(user_input)
                    print(response)

            except Exception as e:
                print(f"\n[错误] {e}")

            print()  # 空行分隔

    except KeyboardInterrupt:
        print("\n\n再见！")
    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())

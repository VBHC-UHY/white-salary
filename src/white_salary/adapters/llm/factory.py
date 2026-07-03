"""
white_salary/adapters/llm/factory.py

LLM工厂 - 根据配置自动创建对应的LLM适配器。

工厂模式的好处：
  - 上层代码不需要知道具体用的是哪个LLM
  - 换LLM只需要改配置文件，代码不用动
  - 新增LLM提供商只需要在这里加一个分支

用法：
    from white_salary.adapters.llm.factory import create_llm
    llm = create_llm(config.llm)
    response = await llm.chat_completion(messages)
"""

from loguru import logger

from white_salary.core.exceptions import ConfigError
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.infrastructure.config.models import LLMConfig


# =============================================================================
# 预置的API提供商信息
# 从 WhiteSalary-v2 1.9 的 providers.ini 提取的可用API
# =============================================================================

PRESET_PROVIDERS: dict[str, dict[str, str]] = {
    # futureppo - Claude代理（主力通道，Sonnet 4.6）
    "futureppo": {
        "base_url": "https://91vip.futureppo.top/v1",
        "default_model": "claude-sonnet-4-6",
    },
    # DeepSeek官方
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    # 硅基流动（免费/低价，速度最快）
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
    },
    # 英伟达免费
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "deepseek-ai/deepseek-v3.1",
    },
    # OpenRouter（聚合多个模型）
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-chat",
    },
    # dmxapi（Claude/GPT代理）
    "dmxapi": {
        "base_url": "https://www.dmxapi.cn/v1",
        "default_model": "gpt-4o",
    },
    # Kimi/Moonshot
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2.5",
    },
    # OpenAI官方
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    # Claude官方（通过Anthropic的OpenAI兼容接口）
    "claude": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
    },
    # Ollama本地
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
    },
}


def create_llm(config: LLMConfig) -> LLMInterface:
    """
    根据配置创建LLM适配器实例。

    参数:
        config: LLM配置对象

    返回:
        LLMInterface 的具体实现

    异常:
        ConfigError: 配置不完整时抛出
    """
    # 延迟导入，避免在没安装openai库时报错
    from white_salary.adapters.llm.openai_compatible import OpenAICompatibleAdapter

    provider = config.provider.lower()

    # 确定 base_url
    if config.base_url:
        # 用户手动指定了地址，优先用
        base_url = config.base_url
    elif provider in PRESET_PROVIDERS:
        # 使用预置的地址
        base_url = PRESET_PROVIDERS[provider]["base_url"]
    else:
        raise ConfigError(
            f"未知的LLM提供商: {provider}，"
            f"支持的提供商: {', '.join(PRESET_PROVIDERS.keys())}。"
            f"或者在配置中手动指定 base_url。",
            details={"provider": provider},
        )

    # 确定模型名称
    model = config.model
    if not model and provider in PRESET_PROVIDERS:
        model = PRESET_PROVIDERS[provider]["default_model"]

    # 确定API密钥
    api_key = config.api_key
    if not api_key:
        raise ConfigError(
            f"LLM API密钥未配置！请在 conf.yaml 中设置 llm.api_key，"
            f"或者设置环境变量。",
            details={"provider": provider},
        )

    logger.info(f"创建LLM适配器: provider={provider}, model={model}, base_url={base_url}")

    # 目前所有提供商都兼容OpenAI格式，所以统一用一个适配器
    return OpenAICompatibleAdapter(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

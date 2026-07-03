"""
LLM适配器模块。

提供与各种大语言模型API的对接能力。

用法：
    from white_salary.adapters.llm import create_llm
    llm = create_llm(config.llm)
"""

from white_salary.adapters.llm.factory import create_llm

__all__ = ["create_llm"]

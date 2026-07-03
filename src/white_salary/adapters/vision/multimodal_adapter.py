"""
white_salary/adapters/vision/multimodal_adapter.py

多模态视觉适配器 — 使用多模态LLM理解图片/截屏。

功能：
  - 接收base64图片 → 发给多模态LLM → 返回图片描述
  - 支持截屏理解（看用户屏幕上显示的内容）
  - 支持用户发送的图片理解

使用 llm_vision 通道（独立的多模态模型，如 GPT-4o / GLM-4.1V）
"""

import base64
import aiohttp
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.types import AudioData


class MultimodalVisionAdapter:

    @staticmethod
    def _detect_image_format(base64_data: str) -> str:
        """从base64数据检测图片格式。"""
        import base64
        try:
            header = base64.b64decode(base64_data[:16])
            if header[:8] == b'\x89PNG\r\n\x1a\n':
                return "png"
            elif header[:2] == b'\xff\xd8':
                return "jpeg"
            elif header[:4] == b'GIF8':
                return "gif"
            elif header[:4] == b'RIFF':
                return "webp"
        except Exception:
            pass
        return "png"  # 默认PNG
    """
    多模态视觉适配器。

    通过 OpenAI-compatible API 发送图片给多模态LLM。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def describe_image(
        self,
        image_base64: str,
        prompt: str = "描述这张图片的内容",
        max_tokens: int = 500,
    ) -> str:
        """
        用多模态LLM描述图片内容。

        Args:
            image_base64: base64编码的图片数据
            prompt: 提问（如"这张图片里有什么"）
            max_tokens: 最大回复长度

        Returns:
            图片描述文本
        """
        if not self._api_key:
            return "[视觉系统未配置API Key]"

        if not image_base64 or len(image_base64) < 100:
            return "[图片数据为空或太小]"

        try:
            # 构建多模态消息（OpenAI Vision API格式）
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{self._detect_image_format(image_base64)};base64,{image_base64}",
                            },
                        },
                    ],
                }
            ]

            payload = {
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"]
                        logger.debug(f"[Vision] Image described: {content[:50]}...")
                        return content
                    elif resp.status == 429 or resp.status == 500:
                        body = await resp.text()
                        if "rate_limit" in body or "limit_error" in body:
                            logger.warning(f"[Vision] Rate limited, skipping")
                            return "[视觉模型限流，请稍后再试]"
                        logger.warning(f"[Vision] API error {resp.status}: {body[:200]}")
                        return f"[视觉识别失败: HTTP {resp.status}]"
                    else:
                        body = await resp.text()
                        logger.warning(f"[Vision] API error {resp.status}: {body[:200]}")
                        return f"[视觉识别失败: HTTP {resp.status}]"

        except Exception as e:
            err_msg = str(e) or type(e).__name__
            logger.warning(f"[Vision] Failed ({type(e).__name__}): {err_msg}")
            return f"[视觉识别错误: {err_msg}]"

    async def is_available(self) -> bool:
        """检查视觉系统是否可用。"""
        return bool(self._api_key and self._model)

    def get_status(self) -> dict:
        """获取视觉系统状态（供控制面板显示）。"""
        return {
            "enabled": bool(self._api_key and self._model),
            "model": self._model,
            "base_url": self._base_url,
            "has_key": bool(self._api_key),
        }

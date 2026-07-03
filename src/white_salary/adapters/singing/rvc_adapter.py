"""
white_salary/adapters/singing/rvc_adapter.py

RVC声音转换适配器 — 将任何歌声转换为白的声音。

使用 rvc-python 库进行推理。

流程：
  1. 输入原始歌曲
  2. （可选）人声/伴奏分离
  3. RVC推理：将人声转换为目标声音
  4. （可选）混合伴奏
  5. 输出转换后的歌曲
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.types import AudioData


class RVCAdapter:
    """
    RVC声音转换适配器。

    使用rvc-python进行声音转换。
    """

    def __init__(
        self,
        model_path: str = "",
        device: str = "cuda:0",
        f0_method: str = "rmvpe",
        transpose: int = 0,
    ) -> None:
        self._model_path = model_path
        self._device = device
        self._f0_method = f0_method
        self._transpose = transpose
        self._rvc = None
        self._loaded = False

        if model_path and Path(model_path).exists():
            self._load_model()

    def _load_model(self) -> None:
        """加载RVC模型。"""
        try:
            from rvc_python.infer import RVCInference

            self._rvc = RVCInference(device=self._device)
            self._rvc.load_model(self._model_path)
            self._loaded = True
            logger.info(f"[RVC] 模型加载成功: {self._model_path}")
        except Exception as e:
            logger.error(f"[RVC] 模型加载失败: {e}")

    async def convert(
        self,
        input_path: str,
        output_path: str = "",
        transpose: int = 0,
    ) -> str:
        """
        转换音频文件的声音。

        Args:
            input_path: 输入音频文件路径
            output_path: 输出路径（空=自动生成）
            transpose: 音调偏移（半音，正=升调，负=降调）

        Returns:
            输出文件路径
        """
        if not self._loaded:
            raise RuntimeError("RVC模型未加载")

        if not output_path:
            output_path = str(Path(input_path).with_suffix(".rvc.wav"))

        try:
            pitch = transpose or self._transpose

            # rvc-python的推理接口
            self._rvc.infer_file(
                input_path=input_path,
                output_path=output_path,
                f0_method=self._f0_method,
                f0_up_key=pitch,
            )

            logger.info(f"[RVC] 转换完成: {input_path} → {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"[RVC] 转换失败: {e}")
            raise

    async def convert_vocals(self, audio: AudioData) -> AudioData:
        """
        将音频数据中的人声转换为模型声音。

        Args:
            audio: 输入音频

        Returns:
            转换后的音频
        """
        if not self._loaded:
            logger.warning("[RVC] 模型未加载，返回原始音频")
            return audio

        try:
            # 写入临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio.samples)
                input_path = f.name

            output_path = input_path + ".rvc.wav"

            # 转换
            await self.convert(input_path, output_path)

            # 读取结果
            with open(output_path, "rb") as f:
                result_bytes = f.read()

            # 清理临时文件
            os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

            return AudioData(
                samples=result_bytes,
                sample_rate=audio.sample_rate,
                dtype="wav",
            )

        except Exception as e:
            logger.error(f"[RVC] 音频转换失败: {e}")
            return audio

    async def is_available(self) -> bool:
        """检查RVC是否可用。"""
        return self._loaded

    def list_models(self, models_dir: str = "models/singing") -> list[dict]:
        """
        列出可用的RVC模型。

        Returns:
            模型信息列表
        """
        models = []
        models_path = Path(models_dir)
        if models_path.exists():
            for f in models_path.glob("*.pth"):
                models.append({
                    "name": f.stem,
                    "path": str(f),
                    "size_mb": round(f.stat().st_size / 1024 / 1024, 1),
                })
        return models

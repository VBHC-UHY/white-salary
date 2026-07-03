"""
white_salary/adapters/asr/siliconflow_adapter.py

硅基流动云端语音识别适配器。

使用硅基流动的 SenseVoice 模型进行语音转文字。
优点：不需要本地GPU，速度快，识别准确。
"""

import aiohttp
from loguru import logger

from white_salary.core.exceptions import ASRError
from white_salary.core.interfaces.asr import ASRInterface, TranscriptionResult
from white_salary.core.interfaces.types import AudioData

# 2026-07-02 审计修复（批2）：按真实容器格式上传，不再写死 audio.wav。
# key=AudioData.dtype（容器格式），value=(上传文件名, Content-Type)
_UPLOAD_FORMAT_MAP: dict[str, tuple[str, str]] = {
    "wav": ("audio.wav", "audio/wav"),
    "webm": ("audio.webm", "audio/webm"),
    "ogg": ("audio.ogg", "audio/ogg"),
    "mp3": ("audio.mp3", "audio/mpeg"),
}


class SiliconFlowASRAdapter(ASRInterface):
    """
    硅基流动云端ASR适配器。

    通过硅基流动API将音频转为文字。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "FunAudioLLM/SenseVoiceSmall",
        base_url: str = "https://api.siliconflow.cn/v1",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def transcribe(self, audio: AudioData) -> TranscriptionResult:
        """
        将音频发送到硅基流动API进行识别。

        Args:
            audio: 音频数据（samples为字节流，dtype声明容器格式：wav/webm/ogg/mp3）

        Returns:
            TranscriptionResult 包含识别出的文字
        """
        if not audio.samples or len(audio.samples) == 0:
            return TranscriptionResult(text="", language="unknown", confidence=0.0)

        try:
            # 2026-07-02 审计修复（批2）：此前硬写 audio.wav / audio/wav，
            # 与实际字节流（前端是WebM/Opus）不符导致服务端解码500。
            # 现在按 AudioData.dtype 声明的真实容器格式上传，未知格式回退wav。
            _fmt = (audio.dtype or "wav").lower()
            _filename, _content_type = _UPLOAD_FORMAT_MAP.get(
                _fmt, ("audio.wav", "audio/wav")
            )

            # 硅基流动的ASR API接受multipart/form-data格式
            form = aiohttp.FormData()
            form.add_field(
                "file",
                audio.samples,
                filename=_filename,
                content_type=_content_type,
            )
            form.add_field("model", self._model)
            # 强制指定中文，防止把"嗯"识别成日语"うん"
            form.add_field("language", "zh")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("text", "").strip()

                        # Filter out non-Chinese results (SenseVoice sometimes misdetects language)
                        if text and not self._has_chinese(text):
                            logger.debug(f"[ASR] Filtered non-Chinese: {text[:30]}")
                            return TranscriptionResult(text="", language="unknown", confidence=0.0)

                        # 清掉混入的日语/韩语字符（ASR偶尔把语气词识别成日语）
                        text = self._clean_foreign_chars(text)

                        logger.debug(f"[ASR] Recognized: {text[:50]}...")
                        return TranscriptionResult(
                            text=text,
                            language="zh",
                            confidence=0.9,
                        )
                    else:
                        body = await resp.text()
                        raise ASRError(
                            f"ASR failed (HTTP {resp.status}): {body[:200]}"
                        )

        except aiohttp.ClientError as e:
            raise ASRError(f"ASR connection error (check API key and network): {e}") from e
        except Exception as e:
            raise ASRError(f"ASR unexpected error: {e}") from e

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """检查文本中是否包含中文字符（至少1个）。"""
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    @staticmethod
    def _clean_foreign_chars(text: str) -> str:
        """清掉ASR误识别的日语/韩语字符，保留中文和标点。"""
        import re
        # 去掉日语假名（平假名+片假名）
        text = re.sub(r'[\u3040-\u309f\u30a0-\u30ff]+[。．]?', '', text)
        # 去掉韩语
        text = re.sub(r'[\uac00-\ud7af\u1100-\u11ff]+', '', text)
        # 清理多余空格和开头的标点
        text = re.sub(r'^\s*[。，、！？,.!?\s]+', '', text)
        return text.strip()

    async def is_available(self) -> bool:
        """检查API Key是否有效。"""
        return bool(self._api_key)

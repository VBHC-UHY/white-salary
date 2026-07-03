"""
适配器层（Adapters）

这一层负责对接各种外部技术和服务。
每个适配器都实现了 core/interfaces/ 中定义的抽象接口。

好处：换引擎（比如从OpenAI换成Claude）只需要写新的适配器，
核心代码完全不用改。

子模块：
  - llm/      LLM适配器（OpenAI、Claude、Ollama等）
  - asr/      语音识别适配器（Whisper、FunASR等）
  - tts/      语音合成适配器（Edge TTS、Azure、GPTSoVITS等）
  - vad/      语音活动检测适配器（Silero等）
  - vision/   视觉系统适配器
  - singing/  唱歌模块适配器（RVC等）
  - game/     游戏AI适配器
  - avatar/   虚拟形象适配器（Live2D等）
  - tools/    外部工具适配器（浏览器、代码执行等）
  - storage/  存储适配器（JSON、向量数据库等）
"""

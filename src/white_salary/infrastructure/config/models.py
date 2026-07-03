"""
white_salary/infrastructure/config/models.py

配置数据模型（Pydantic）。

这个文件用 Pydantic 定义了所有配置项的结构和验证规则。
Pydantic 的好处是：
  1. 自动校验——如果配置项的值不对（比如端口号写成了字母），会立刻报错
  2. 类型安全——IDE能自动补全，写错字段名会提示
  3. 默认值——没配置的项会自动用默认值

对应的YAML配置文件结构见 conf.default.yaml。
"""

from pydantic import BaseModel, Field


# =============================================================================
# 各个子配置模型
# =============================================================================

class SystemConfig(BaseModel):
    """
    系统基础配置。

    控制项目名称、版本、调试模式等全局设置。
    """
    name: str = Field(default="White Salary", description="项目名称")
    version: str = Field(default="0.1.3", description="版本号")
    debug: bool = Field(default=False, description="是否开启调试模式")
    log_level: str = Field(default="INFO", description="日志级别: DEBUG/INFO/WARNING/ERROR")


class ServerConfig(BaseModel):
    """
    服务器配置。

    控制Web服务的监听地址、端口和跨域设置。
    """
    host: str = Field(default="localhost", description="监听地址")
    port: int = Field(default=12400, ge=1, le=65535, description="端口号（1-65535）")
    cors_enabled: bool = Field(default=True, description="是否允许跨域请求")
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="允许跨域的来源地址列表",
    )


class LLMConfig(BaseModel):
    """
    LLM（大语言模型）配置。

    控制使用哪个LLM引擎、API密钥、模型参数等。
    """
    provider: str = Field(default="openai", description="LLM引擎: openai/claude/ollama/deepseek")
    api_key: str = Field(default="", description="API密钥")
    model: str = Field(default="gpt-4o", description="模型名称")
    base_url: str = Field(default="", description="API地址（自建服务用）")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="创造性程度(0-2)")
    max_tokens: int = Field(default=2048, ge=1, description="回复最大长度")


class ASRConfig(BaseModel):
    """
    ASR（语音识别）配置。

    2026-07-03 审计修复（批5）：原 provider(whisper)/model_size/language 三个字段
    全仓库零消费（真实行为硬编码在 run_server.py 的 SiliconFlow SenseVoice），
    属"配了没用"的死配置；现重写为真实生效字段，默认值 = 原 run_server.py
    硬编码值，行为不变（依据 docs/audit-2026-07-02/config-audit.json）。
    """
    provider: str = Field(default="siliconflow", description="ASR引擎（当前仅支持 siliconflow）")
    api_key: str = Field(
        default="",
        description="API密钥（留空=自动从已配置的角色LLM中扫一把SiliconFlow密钥，沿用旧逻辑）",
    )
    model: str = Field(default="FunAudioLLM/SenseVoiceSmall", description="语音识别模型名称")


class TTSConfig(BaseModel):
    """
    TTS（语音合成）配置。

    2026-07-03 审计修复（批5）：原 provider/voice/rate/pitch 四个字段全仓库零消费
    （真实行为硬编码在 run_server.py：本地 GPT-SoVITS 优先、SiliconFlow 兜底），
    属"配了没用"的死配置；现重写为真实生效字段，默认值 = 原 run_server.py
    硬编码值，行为不变，但用户在控制面板改 TTS 从此真实生效
    （依据 docs/audit-2026-07-02/config-audit.json）。
    """
    local_api_url: str = Field(
        default="http://127.0.0.1:9880",
        description="本地 GPT-SoVITS API 地址（主TTS，启动时探测该端口决定是否走本地）",
    )
    ref_audio: str = Field(
        default="assets/tts/ref_default.wav",
        description="声音克隆参考音频路径（相对项目根目录；也支持绝对路径）",
    )
    ref_text: str = Field(
        default="你怎么不会想让我去试辣子鸡丁吧",
        description="参考音频对应的文字内容",
    )
    fallback_provider: str = Field(
        default="siliconflow",
        description="本地TTS不可用时的云端兜底引擎（当前仅支持 siliconflow）",
    )
    fallback_api_key: str = Field(
        default="",
        description="兜底引擎API密钥（留空=自动从已配置的角色LLM中扫一把SiliconFlow密钥，沿用旧逻辑）",
    )
    fallback_model: str = Field(
        default="FunAudioLLM/CosyVoice2-0.5B",
        description="兜底TTS模型名称",
    )
    fallback_voice: str = Field(
        default="FunAudioLLM/CosyVoice2-0.5B:anna",
        description="兜底TTS音色ID（自定义克隆音色或预置音色）",
    )
    # 2026-07-03 面板升级（批6）：新增基准语速字段——run_server 创建 GPTSoVITSAdapter
    # 时传入（原构造函数默认1.0，本字段默认值相同，行为不变）；合成时会再乘上
    # 情绪追踪器的 speed_factor（emotion_tracker.get_tts_modifiers），让"表情动作"
    # 页的情绪调速表成真（依据 docs/panel-audit-2026-07-03/panel-voice.json）
    speed: float = Field(
        default=1.0, ge=0.25, le=4.0,
        description="基准语速（1.0=正常；仅本地GPT-SoVITS支持，云端兜底不支持）",
    )


class VADConfig(BaseModel):
    """
    VAD（语音活动检测）配置。
    """
    provider: str = Field(default="silero", description="VAD引擎: silero")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="检测阈值(0-1)")


class MemoryConfig(BaseModel):
    """
    记忆系统配置。
    """
    short_term_max_turns: int = Field(default=20, ge=1, description="短期记忆保留的最大对话轮数")
    long_term_provider: str = Field(default="none", description="长期记忆引擎: chroma/none")
    long_term_top_k: int = Field(default=5, ge=1, description="长期记忆检索返回的条数")


class EmotionConfig(BaseModel):
    """
    情感系统配置。
    """
    enabled: bool = Field(default=True, description="是否启用情感系统")
    sensitivity: float = Field(default=0.6, ge=0.0, le=1.0, description="情绪灵敏度(0-1)")


class PersonalityConfig(BaseModel):
    """
    人格配置。
    """
    system_prompt_file: str = Field(
        default="prompts/system_prompt.txt",
        description="系统提示词文件路径",
    )
    character_name: str = Field(default="White Salary", description="角色名称")


class FilterConfig(BaseModel):
    """
    内容过滤配置。
    """
    enabled: bool = Field(default=True, description="是否启用内容过滤")
    rules_file: str = Field(default="prompts/filter_rules.yaml", description="过滤规则文件路径")


class AvatarConfig(BaseModel):
    """
    虚拟形象配置。
    """
    provider: str = Field(default="none", description="形象引擎: live2d/none")
    model_path: str = Field(default="live2d_models/default", description="模型路径")


class SingingConfig(BaseModel):
    """
    唱歌模块配置。
    """
    enabled: bool = Field(default=False, description="是否启用唱歌功能")
    provider: str = Field(default="rvc", description="唱歌引擎: rvc")
    model_path: str = Field(default="models/singing/default.pth", description="声音模型路径")


class RoleLLMConfig(BaseModel):
    """
    分角色 LLM 配置。

    2026-07-03 审计修复（批5）：conf.yaml 里 llm_tool/llm_memory/llm_emotion/
    llm_vision/llm_postprocess/llm_detect/llm_background 七个角色通道此前未在
    AppConfig 定义，被 Pydantic 默认 extra='ignore' 静默丢弃，run_server 只能用
    yaml.safe_load 旁路裸读 conf.yaml 形成双轨配置；现补齐模型定义，七个通道
    统一走 load_config() 的深合并+校验（依据 docs/audit-2026-07-02/config-audit.json）。

    四个字段全部默认空字符串：api_key 或 model 为空即视为"该角色未配置"，
    run_server 不会为其创建适配器（与旧旁路行为一致）。
    """
    provider: str = Field(default="", description="提供商名（base_url为空时按此名查PRESET_PROVIDERS兜底）")
    api_key: str = Field(default="", description="API密钥（空=该角色未配置）")
    model: str = Field(default="", description="模型名称（空=该角色未配置）")
    base_url: str = Field(default="", description="API地址（空=按provider查预设表兜底）")


class QQConfig(BaseModel):
    """
    QQ 集成配置（NapCat OneBot v11）。

    2026-07-03 审计修复（批5）：此前 qq 节未在 AppConfig 定义（被 Pydantic 静默
    丢弃），run_server 用 yaml 旁路读取；现补齐（依据 config-audit.json）。
    默认值与 run_server 旧旁路的 .get() 默认值逐一相同，行为不变。
    """
    enabled: bool = Field(default=False, description="是否启用QQ集成")
    ws_url: str = Field(default="ws://127.0.0.1:3001", description="NapCat正向WebSocket地址")
    bot_name: str = Field(default="白", description="机器人在群聊中的名字（@或提到时回复）")
    token: str = Field(default="", description="NapCat鉴权token")
    family_qq: list[int | str] = Field(
        default_factory=list,
        description="家人QQ号列表（第一个号同时作为主人的统一user_id）",
    )
    owner_name: str = Field(
        default="",
        description="对主人的称呼（可选；空=按用户画像user_name回退，再没有就交给模型自然称呼）",
    )


class AutoChatConfig(BaseModel):
    """
    主动聊天配置（conf.yaml 的 auto_chat 节）。

    2026-07-03 审计修复（批5）：此前该节未在 AppConfig 定义被静默丢弃，
    websocket_handler 用 yaml 旁路读取；现补齐模型定义（依据 config-audit.json）。
    注意：core/auto_chat.py 里另有一个同名 dataclass（运行时配置对象），
    本类只负责 YAML 解析校验，字段与 conf.default.yaml 的 auto_chat 节对齐。
    """
    enabled: bool = Field(default=True, description="是否启用主动聊天")
    morning_greeting: bool = Field(default=True, description="是否启用早安问候")
    night_greeting: bool = Field(default=True, description="是否启用晚安问候")
    care_reminder: bool = Field(default=True, description="是否启用关心提醒")
    random_chat: bool = Field(default=True, description="是否启用随机闲聊")
    daily_limit: int = Field(default=3, ge=0, description="每日主动聊天次数上限")


class BilibiliConfig(BaseModel):
    """
    B站直播弹幕互动配置（conf.yaml 的 bilibili 节）。

    2026-07-03 功能大项（批11二波）：BilibiliLiveAdapter 早已实现 connect()/
    on_danmaku 回调逻辑，但全项目无人装配它（死架子）。现补齐配置模型 + run_server
    后台线程装配，让白能进驻B站直播间读弹幕、（登录后）发弹幕回复。

    默认 enabled=false 且 room_id=0：新功能默认关闭，不影响现有用户；只有用户
    显式填 room_id 并开 enabled 才会启动监听线程。依赖可选库 bilibili-api-python，
    未安装时装配段的 try/except 会优雅跳过（logger.warning 后照常启动其它形态）。

    发弹幕需要登录凭证（config/bili.ini，由设置面板的B站扫码/浏览器读取写入）；
    没登录只读弹幕不发送。reply_danmaku 控制"是否真的发弹幕回复"（默认 false=
    只监听不发，最安全）；trigger_keywords 控制"只回复含哪些关键词的弹幕"（默认
    空=只回复 @机器人/提到机器人名字的弹幕，避免刷屏被B站风控封禁）。
    """
    enabled: bool = Field(default=False, description="是否启用B站直播弹幕监听")
    room_id: int = Field(
        default=0, ge=0,
        description="B站直播间号（在直播间URL live.bilibili.com/后面那串数字）；0=未配置不启动",
    )
    reply_danmaku: bool = Field(
        default=False,
        description="是否发弹幕回复（默认false=只监听不发；需先在面板登录B站才能真的发送）",
    )
    trigger_keywords: list[str] = Field(
        default_factory=list,
        description="触发关键词列表（弹幕含任一关键词才回复；空=只回复@机器人或提到机器人名字的弹幕，防刷屏）",
    )


class ExternalToolsConfig(BaseModel):
    """
    外部本地工具路径配置（conf.yaml 的 external_tools 节）。

    2026-07-03 外部依赖优化（批8）：ComfyUI / CosyVoice / GPT-SoVITS / Wav2Lip /
    ffmpeg 等"本地进阶功能"的安装路径此前散落在各 adapter 里硬编码，换机器要改源码。
    现收拢为可配置节，解析顺序统一为：
        环境变量(保留各处历史 WS_*) → 本节配置

    重要（用户方向：云端为主、本地进阶可选）：这些字段**只有用本地进阶功能才需要**，
    只用云端（填 API key 就能用）的用户可以全部留空。默认值全为空字符串="未配置"；
    对应本地增强功能会给出明确提示或自动降级，不再回退到作者机器的固定路径。
    """
    comfyui_bat: str = Field(
        default="",
        description="ComfyUI 启动脚本(.bat)路径；空=不自动启动本地 ComfyUI",
    )
    comfyui_input: str = Field(
        default="",
        description="ComfyUI 的 input 目录（图生视频时复制输入图进去）；空=不使用本地 ComfyUI input",
    )
    gpt_sovits_dir: str = Field(
        default="",
        description="GPT-SoVITS 安装目录（本地高质量TTS/声音克隆）；空=不启动本地 GPT-SoVITS",
    )
    cosyvoice_bat: str = Field(
        default="",
        description="CosyVoice 启动脚本(.bat)路径（本地无过滤TTS）；空=不自动启动本地 CosyVoice",
    )
    wav2lip_dir: str = Field(
        default="",
        description="Wav2Lip 安装目录（视频口型同步）；空=不启用 Wav2Lip",
    )
    ffmpeg_path: str = Field(
        default="",
        description="ffmpeg.exe 完整路径（视频/音频转码）；空=只查系统 PATH",
    )


class FeaturesConfig(BaseModel):
    """
    功能开关配置（conf.yaml 的 features 节）。

    2026-07-03 审计修复（批5）：补齐模型定义防止被 Pydantic 静默丢弃。
    2026-07-03 面板升级（批6）：五个开关全部接上真实消费方（依据
    docs/panel-audit-2026-07-03/panel-chatcfg.json）：
      - topic_tracker:        websocket_handler / qq_handler 创建 TopicTracker 处
      - rest_system:          qq_handler 创建 RestSystem 处
      - user_learning:        run_server 装配 UserLearningService 处（False 传 None）
      - memory_consolidation: run_server 每日整理调度线程（手动端点保留）
      - content_filter:       ChatAgent 与 websocket_handler 的 ContentFilter 构造处
    默认值全部为 True = 原硬编码行为，只有用户显式关掉才变化。
    """
    topic_tracker: bool = Field(default=True, description="话题追踪（防重复话题提示）")
    rest_system: bool = Field(default=True, description="作息系统（QQ端休息时不回消息）")
    user_learning: bool = Field(default=True, description="用户学习（自动学习用户画像）")
    memory_consolidation: bool = Field(default=True, description="记忆自动整理（每日凌晨调度）")
    content_filter: bool = Field(default=True, description="内容过滤（False=只记录不过滤）")


# =============================================================================
# 总配置模型（包含所有子配置）
# =============================================================================

class AppConfig(BaseModel):
    """
    应用总配置。

    这个类把所有子配置组合在一起，对应整个 conf.yaml 文件的结构。
    通过 AppConfig.system.debug 这样的方式访问配置项。
    """
    system: SystemConfig = Field(default_factory=SystemConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    emotion: EmotionConfig = Field(default_factory=EmotionConfig)
    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    filter: FilterConfig = Field(default_factory=FilterConfig)
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    singing: SingingConfig = Field(default_factory=SingingConfig)

    # 2026-07-03 审计修复（批5）：补齐此前被 Pydantic 静默丢弃的 10 节配置
    # （7个角色LLM + qq + auto_chat + features），loader 的深合并从此对这些节
    # 真实生效，run_server 等消费方不再需要 yaml 旁路裸读 conf.yaml
    # （依据 docs/audit-2026-07-02/config-audit.json）。
    llm_tool: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="工具判断模型（Function Calling决策）"
    )
    llm_memory: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="记忆分析模型（提取值得记住的信息）"
    )
    llm_emotion: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="情感分析模型（理解情绪、调整语气）"
    )
    llm_vision: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="视觉理解模型（看图/看屏幕）"
    )
    llm_postprocess: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="后处理模型（杂活、配文、快速辅助任务）"
    )
    llm_detect: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="检测防护模型（安全检测、话题追踪）"
    )
    llm_background: RoleLLMConfig = Field(
        default_factory=RoleLLMConfig, description="后台任务模型（不紧急的后台任务、自动聊天）"
    )
    qq: QQConfig = Field(default_factory=QQConfig)
    auto_chat: AutoChatConfig = Field(default_factory=AutoChatConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)

    # 2026-07-03 功能大项（批11二波）：B站直播弹幕互动配置节——装配早已实现但
    # 无人调用的 BilibiliLiveAdapter（死架子）。默认 enabled=false=关闭，不影响
    # 现有用户；依赖可选库 bilibili-api-python，未装时 run_server 装配段优雅跳过。
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)

    # 2026-07-03 外部依赖优化（批8）：外部本地工具路径配置节——把散落在各 adapter
    # 的硬编码安装路径(ComfyUI/CosyVoice/GPT-SoVITS/Wav2Lip/ffmpeg)收拢为可配置项。
    # 默认全空=未配置；只用云端的用户可完全忽略本节（依据批8任务：
    # 外部工具路径配置化 + 云端降级健壮性）。
    external_tools: ExternalToolsConfig = Field(default_factory=ExternalToolsConfig)

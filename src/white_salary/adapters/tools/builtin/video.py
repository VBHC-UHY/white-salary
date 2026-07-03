"""视频工具 — AnimateDiff动画/SVD图生视频/口型同步/视频下载。"""
from ._helpers import tool, P, S, I


@tool("download_video", "下载在线视频", P(url=S("视频链接", True)))
async def download_video(url: str = "") -> str:
    return "视频下载功能暂时不可用"


@tool("get_video_info", "获取视频详细信息", P(url=S("视频链接", True)))
async def get_video_info(url: str = "") -> str:
    if "bilibili" in url or "b23.tv" in url:
        from white_salary.adapters.tools.bilibili_tool import bilibili_video_info
        return await bilibili_video_info(url)
    return "目前只支持B站视频信息查询"


@tool("send_file", "发送文件给指定目标", P(file_path=S("文件路径", True), target=S("目标")))
async def send_file(file_path: str = "", target: str = "") -> str:
    return "文件发送功能暂时不可用"


@tool("make_video", "用AI制作视频/动画——当用户说「做个视频」「做个有声视频」「做个XX秒的视频」时调用",
      P(prompt=S("视频/动画描述", True), duration=S("视频时长秒数，默认5"),
        voiceover=S("配音文本（空=无声）"), voice=S("配音音色，如中文女/中文男")))
async def make_video(prompt: str = "", duration: str = "5", voiceover: str = "", voice: str = "中文女") -> str:
    if not prompt:
        return "请描述想要的视频"
    try:
        import yaml
        from pathlib import Path
        # 2026-07-03 审计修复（批5）：conf.yaml 改为从模块位置推导项目根的绝对路径，
        # 不再依赖 CWD（此前从其它工作目录启动会静默拿空配置，
        # 依据 docs/audit-2026-07-02/config-audit.json）
        _project_root = Path(__file__).resolve().parents[5]
        conf = yaml.safe_load((_project_root / "conf.yaml").read_text(encoding="utf-8")) or {}
        sf_key = conf.get("llm_vision", {}).get("api_key", "")

        dur = int(duration) if str(duration).isdigit() else 5

        if dur <= 5:
            # 5秒以内：单段生成
            from white_salary.adapters.tools.video_gen import generate_video_from_text
            path = await generate_video_from_text(prompt=prompt, api_key=sf_key)
        else:
            # 超过5秒：多段拼接
            from white_salary.adapters.tools.video_gen import generate_long_video
            path = await generate_long_video(prompt=prompt, api_key=sf_key, duration=dur)

        if path:
            # 如果有配音需求，加配音
            if voiceover and voiceover.strip():
                from white_salary.adapters.tools.video_gen import add_voiceover
                voiced_path = await add_voiceover(video_path=path, text=voiceover, voice=voice, api_key=sf_key)
                if voiced_path:
                    return f"有声视频已生成，保存在{voiced_path}"
            return f"视频已生成，保存在{path}"

        # 云端失败，尝试本地ComfyUI AnimateDiff
        from white_salary.adapters.tools.comfyui_client import (
            ensure_comfyui_running, generate_video_animatediff,
        )
        if await ensure_comfyui_running(timeout=60):
            path = await generate_video_animatediff(prompt=prompt)
            if path:
                if voiceover and voiceover.strip():
                    from white_salary.adapters.tools.video_gen import add_voiceover
                    voiced_path = await add_voiceover(video_path=path, text=voiceover, voice=voice, api_key=sf_key)
                    if voiced_path:
                        return f"有声视频已生成，保存在{voiced_path}"
                return f"视频已生成，保存在{path}"

        # 2026-07-03 外部依赖优化（批8）：云端(硅基流动 Wan2.2)与本地(ComfyUI
        # AnimateDiff)都失败，给明确中文提示指向配置文档，不静默含糊
        return (
            "视频生成失败：云端需要配置硅基流动 API key（llm_vision 节），"
            "本地需要安装并启动 ComfyUI(+AnimateDiff 模型)。"
            "本地工具路径可在 conf.yaml 的 external_tools 节配置，详见 docs/EXTERNAL_SERVICES.md"
        )
    except Exception as e:
        from loguru import logger
        logger.debug(f"[Video] 视频生成失败: {e}")
        return "视频生成功能出错了"


@tool("generate_video", "图片变视频——把一张图片变成动态视频。当用户说「把图片变成视频」「图片动起来」时调用",
      P(image_path=S("输入图片路径", True), prompt=S("运动描述（可选）"), mode=S("cloud云端快/local本地慢但无过滤")))
async def generate_video(image_path: str = "", prompt: str = "", mode: str = "cloud") -> str:
    if not image_path:
        return "请提供图片路径"
    try:
        # 云端优先（快，约60秒，但有安全过滤）
        if mode != "local":
            import yaml
            from pathlib import Path
            # 2026-07-03 审计修复（批5）：conf.yaml 改为项目根绝对路径，不依赖 CWD
            _project_root = Path(__file__).resolve().parents[5]
            conf = yaml.safe_load((_project_root / "conf.yaml").read_text(encoding="utf-8")) or {}
            sf_key = conf.get("llm_vision", {}).get("api_key", "")
            if sf_key:
                from white_salary.adapters.tools.video_gen import generate_video_from_image
                path = await generate_video_from_image(
                    image_url=image_path, prompt=prompt or "", api_key=sf_key,
                )
                if path:
                    return f"视频已生成，保存在{path}"

        # 云端失败或指定本地 → 本地Wan2.2 NSFW（慢但无过滤）
        from white_salary.adapters.tools.comfyui_client import (
            ensure_comfyui_running, generate_video_wan22, generate_video_svd,
        )
        if await ensure_comfyui_running(timeout=60):
            import shutil
            from pathlib import Path
            # 2026-07-03 外部依赖优化（批8）：ComfyUI input 目录改走统一解析
            # （环境变量 WS_COMFYUI_INPUT → conf.yaml external_tools.comfyui_input → 内置默认）
            from white_salary.adapters.tools.external_paths import get_comfyui_input
            try:
                comfyui_input = get_comfyui_input()
            except FileNotFoundError:
                return (
                    "图生视频需要配置 ComfyUI input 目录。请在 conf.yaml 的 "
                    "external_tools.comfyui_input 填写路径，或设置 WS_COMFYUI_INPUT。"
                )
            comfyui_input.mkdir(parents=True, exist_ok=True)
            src = Path(image_path)
            if not src.exists():
                return "图片文件不存在"
            dest = comfyui_input / src.name
            shutil.copy2(str(src), str(dest))

            path = await generate_video_wan22(
                input_image=src.name,
                prompt=prompt or "gentle motion, wind blowing hair",
            )
            if path:
                return f"视频已生成，保存在{path}"

            # 再降级到SVD
            path = await generate_video_svd(input_image=src.name)
            if path:
                return f"视频已生成，保存在{path}"

        # 2026-07-03 外部依赖优化（批8）：全部链路失败给明确中文提示（云端优先、
        # 本地兜底都没成），指向配置文档，不静默返回含糊的"失败了"
        return (
            "图生视频失败：云端需要配置硅基流动 API key（llm_vision 节），"
            "本地需要安装并启动 ComfyUI(+Wan2.2/SVD 模型)。"
            "本地工具路径可在 conf.yaml 的 external_tools 节配置，详见 docs/EXTERNAL_SERVICES.md"
        )
    except Exception as e:
        from loguru import logger
        logger.debug(f"[Video] 图生视频失败: {e}")
        return "视频生成功能出错了"


@tool("local_generate_video", "用本地模型生成视频", P(prompt=S("描述", True)))
async def local_generate_video(prompt: str = "") -> str:
    return await make_video(prompt=prompt)


@tool("local_generate_sfx", "用本地模型生成音效", P(description=S("音效描述", True)))
async def local_generate_sfx(description: str = "") -> str:
    return "本地音效生成功能暂时不可用"


@tool("local_lip_sync", "视频口型同步——用Wav2Lip让视频人物口型对上音频",
      P(audio_path=S("音频文件路径"), video_path=S("视频文件路径")))
async def local_lip_sync(audio_path: str = "", video_path: str = "") -> str:
    if not audio_path or not video_path:
        return "需要提供音频和视频文件路径"
    try:
        import subprocess
        from pathlib import Path

        # 2026-07-03 外部依赖优化（批8）：Wav2Lip 目录改走统一解析
        # （环境变量 WS_WAV2LIP_DIR → conf.yaml external_tools.wav2lip_dir → 内置默认）
        from white_salary.adapters.tools.external_paths import get_wav2lip_dir
        try:
            wav2lip_dir = get_wav2lip_dir()
        except FileNotFoundError:
            return (
                "口型同步需要配置 Wav2Lip 安装目录。请在 conf.yaml 的 "
                "external_tools.wav2lip_dir 填写路径，或设置 WS_WAV2LIP_DIR。"
            )
        inference_py = wav2lip_dir / "inference.py"
        if not inference_py.exists():
            return (
                f"口型同步需要本地安装 Wav2Lip（当前查找目录 {wav2lip_dir} 下无 inference.py）。"
                "可在 conf.yaml 的 external_tools.wav2lip_dir 配置安装路径，详见 docs/EXTERNAL_SERVICES.md"
            )

        output_path = f"data/videos/lipsync_{int(__import__('time').time())}.mp4"
        Path("data/videos").mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                "python", str(inference_py),
                "--checkpoint_path", str(wav2lip_dir / "checkpoints" / "wav2lip_gan.pth"),
                "--face", video_path,
                "--audio", audio_path,
                "--outfile", output_path,
            ],
            cwd=str(wav2lip_dir),
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode == 0 and Path(output_path).exists():
            return f"口型同步完成，保存在{output_path}"
        return "口型同步失败了"
    except Exception as e:
        from loguru import logger
        logger.debug(f"[Video] Wav2Lip失败: {e}")
        return "口型同步功能出错了"


@tool("local_full_video_pipeline", "完整视频流水线（生成动画+配音+口型同步）",
      P(prompt=S("视频描述", True)))
async def local_full_video_pipeline(prompt: str = "") -> str:
    return "完整视频流水线功能暂时不可用"


@tool("local_generate_voice", "本地语音生成（GPT-SoVITS）",
      P(text=S("文本", True), voice=S("音色")))
async def local_generate_voice(text: str = "", voice: str = "default") -> str:
    return "本地语音生成功能暂时不可用"


# 2026-07-02 审计修复（批2）：下架5个固定文案空壳——download_video/send_file/
# local_generate_sfx/local_full_video_pipeline/local_generate_voice 全部只返回
# 「暂时不可用」，注册进LLM工具列表会误导模型以为具备能力
# （依据 docs/audit-2026-07-02/tools-media.json）。函数体保留；
# local_generate_voice 以后可直接接 cosyvoice_client.generate_speech。
TOOLS = [fn._tool_def for fn in [
    get_video_info, make_video, generate_video,
    local_generate_video, local_lip_sync,
]]

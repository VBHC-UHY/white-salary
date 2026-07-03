"""多媒体工具 — 图片生成/表情包/画图/唱歌/音乐/截屏/描述图片。"""
from ._helpers import tool, P, S, NONE_PARAMS


@tool("generate_image", "生成AI图片/画图/画画/自拍/自画像/发照片/发个照片/看看你长什么样——根据描述生成图片。当用户说「画个XX」「生成图片」「画一张」「发自拍」「发个照片」「看看你」时调用",
      P(prompt=S("图片描述（越详细越好）", True), send_qq=S("是否发到QQ(true/false)")))
async def generate_image(prompt: str = "", send_qq: str = "false") -> str:
    if not prompt:
        return "请描述想要的图片"
    from white_salary.adapters.tools.image_gen import generate_image as _gen
    try:
        from white_salary.adapters.tools.cloud_config import resolve_image_generation_keys

        sf_key, dmx_key = resolve_image_generation_keys()
    except Exception:
        sf_key, dmx_key = "", ""

    path = await _gen(prompt=prompt, siliconflow_key=sf_key, dmxapi_key=dmx_key)
    if not path:
        # 2026-07-03 外部依赖优化（批8）：三级降级(ComfyUI本地→DMXAPI→硅基流动)全失败时
        # 给明确中文提示，指向配置文档，不静默返回含糊的"失败了"
        return (
            "生图失败：需要配置本地 ComfyUI（进阶）或云端 DMXAPI / 硅基流动 API key。"
            "如果主 LLM 用的是硅基流动，生图会自动复用这把 key；"
            "否则请在控制面板或 conf.yaml 的 llm_vision/DMXAPI 通道补 key，"
            "本地 ComfyUI 路径在 conf.yaml 的 external_tools 节，详见 docs/EXTERNAL_SERVICES.md"
        )

    # 如果需要发到QQ（根据上下文判断发群还是发私聊）
    if send_qq in ("true", "True", True):
        try:
            from white_salary.adapters.tools.builtin.qq_api import _call, get_msg_context, _safe_int
            from pathlib import Path as _Path
            file_url = f"file:///{_Path(path).resolve()}" if not path.startswith("http") else path
            ctx = get_msg_context()
            if ctx.get("is_group") and ctx.get("group_id"):
                # 群聊：发到群里
                await _call("send_group_msg", {
                    "group_id": _safe_int(ctx["group_id"], "群号"),
                    "message": [{"type": "image", "data": {"file": file_url}}],
                })
            elif ctx.get("user_id"):
                # 私聊：发给用户
                await _call("send_private_msg", {
                    "user_id": _safe_int(ctx["user_id"], "QQ号"),
                    "message": [{"type": "image", "data": {"file": file_url}}],
                })
        except Exception:
            pass

    return f"图片已生成，保存在{path}" if not path.startswith("http") else "图片已生成"


@tool("draw", "画图（支持指定画风）——用AI画一张图", P(prompt=S("画面描述", True), style=S("风格如anime/realistic")))
async def draw(prompt: str = "", style: str = "anime") -> str:
    return await generate_image(prompt=f"{style} style, {prompt}" if style else prompt)


# 2026-07-03 工具实现（批9）：describe_image 接真视觉链路的辅助函数。
# 拆成小函数便于单测分别打桩（注册表命中/现场构造/图片加载三条路径独立可测）。

def _project_root_path():
    """返回项目根目录绝对路径（从模块位置推导，不依赖 CWD）。"""
    from pathlib import Path
    return Path(__file__).resolve().parents[5]


def _get_vision_adapter():
    """
    2026-07-03 工具实现（批9）：取视觉适配器——两条路都要能工作。

    1. 优先从 settings_api 运行实例注册表取 'vision'（run_server 若注册过，
       直接复用运行中的实例，配置与控制面板一致）；
    2. 取不到则按 conf.yaml 的 llm_vision 节现场构造 MultimodalVisionAdapter。

    返回:
        (adapter, 错误提示)：成功时错误提示为空串；失败时 adapter 为 None，
        错误提示是给用户看的中文指引。
    """
    # 路径1：运行实例注册表（run_server 是否注册不归本批管，注册了就用）
    try:
        from white_salary.infrastructure.server.settings_api import get_runtime_instance
        instance = get_runtime_instance("vision")
        if instance is not None:
            return instance, ""
    except Exception:
        pass  # 注册表模块导入失败不致命，走现场构造

    # 路径2：按合并后的 llm_vision 配置现场构造；llm_vision 未填 key 时，
    # 允许复用主 llm/其它角色里已经配置的 SiliconFlow key。
    try:
        from white_salary.adapters.tools.cloud_config import (
            load_cloud_config,
            resolve_vision_channel,
        )

        config = load_cloud_config(_project_root_path())
        vision_conf = resolve_vision_channel(config)
        api_key = vision_conf.api_key
        base_url = vision_conf.base_url
        model = vision_conf.model
        if not (api_key and base_url and model):
            raise ValueError("llm_vision 配置不完整（缺 api_key/base_url/model）")
        from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter
        return MultimodalVisionAdapter(api_key=api_key, base_url=base_url, model=model), ""
    except Exception as e:
        return None, (
            f"看图失败：视觉模型未就绪（{e}）。"
            "请在 conf.yaml 的 llm_vision 节配置 api_key/base_url/model，"
            "或让主 llm 通道使用硅基流动 key（会自动复用到看图）。"
            "详见 docs/EXTERNAL_SERVICES.md"
        )


async def _load_image_base64(image_path: str) -> tuple[str, str]:
    """
    2026-07-03 工具实现（批9）：把本地路径或URL的图片读成base64。

    返回:
        (base64字符串, 错误提示)：成功时错误提示为空串。
    """
    import base64 as _b64
    from pathlib import Path

    if image_path.startswith("http://") or image_path.startswith("https://"):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(image_path, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return "", f"看图失败：图片下载失败（HTTP {resp.status}）"
                    data = await resp.read()
            return _b64.b64encode(data).decode("ascii"), ""
        except Exception as e:
            return "", f"看图失败：图片下载出错（{e}）"

    p = Path(image_path)
    if not p.exists() or not p.is_file():
        return "", f"看图失败：本地图片不存在：{image_path}"
    try:
        return _b64.b64encode(p.read_bytes()).decode("ascii"), ""
    except Exception as e:
        return "", f"看图失败：图片读取出错（{e}）"


# 2026-07-03 工具实现（批9）：describe_image 重写为真调视觉模型——
# 原空壳固定返回「需要配合视觉系统使用」（批2下架），现在优先复用运行实例
# 注册表里的 'vision' 实例，取不到就按 conf.yaml llm_vision 现场构造，
# 两条路都不通才返回中文配置指引。加回 TOOLS。
@tool("describe_image", "看图/识别图片——给一张图片（本地路径或URL），用视觉模型描述图里的内容。当用户发来图片问「这是什么」「看看这张图」「图里写了什么」时调用",
      P(image_path=S("图片的本地路径或URL", True)))
async def describe_image(image_path: str = "") -> str:
    if not image_path:
        return "请提供图片的本地路径或URL"

    adapter, err = _get_vision_adapter()
    if adapter is None:
        return err

    image_b64, load_err = await _load_image_base64(image_path)
    if load_err:
        return load_err

    result = await adapter.describe_image(image_b64)
    return result or "看图失败：视觉模型没有返回内容"


@tool("generate_sticker", "生成自定义表情包——用AI生成表情包图片",
      P(emotion=S("情绪", True), style=S("风格")))
async def generate_sticker(emotion: str = "", style: str = "anime") -> str:
    # 用图片生成来做表情包
    sticker_prompt = f"chibi anime girl sticker, {emotion} expression, {style} style, simple background, emoji style"
    return await generate_image(prompt=sticker_prompt)


@tool("screenshot", "截取用户屏幕——截屏并用视觉系统分析内容")
async def screenshot() -> str:
    from white_salary.adapters.vision.screenshot import capture_screenshot
    img = await capture_screenshot()
    if not img:
        return "截屏失败了"

    adapter, err = _get_vision_adapter()
    if adapter is None:
        return f"截屏成功（{len(img)//1024}KB），但还没法分析画面：{err}"

    try:
        description = await adapter.describe_image(
            img,
            prompt=(
                "请用中文简洁描述这张电脑屏幕截图里当前可见的主要内容。"
                "如果用户是在让你看屏幕或截图，直接说明你看到了什么。"
            ),
            max_tokens=260,
        )
    except TypeError:
        description = await adapter.describe_image(img)
    except Exception as e:
        return f"截屏成功（{len(img)//1024}KB），但视觉分析失败了：{e}"

    if description and description.strip():
        return f"截屏成功，我看到：{description.strip()}"
    return f"截屏成功（{len(img)//1024}KB），但视觉模型没有返回可用描述。"


@tool("sing", "唱歌", P(song_name=S("歌名", True)))
async def sing(song_name: str = "") -> str:
    return "唱歌功能暂时不可用"


@tool("music_gen", "生成AI音乐", P(prompt=S("音乐描述", True), style=S("风格")))
async def music_gen(prompt: str = "", style: str = "pop") -> str:
    return "音乐生成功能暂时不可用"


@tool("edit_image", "修改图片——在已有图片基础上修改。当用户说「把图片改一下」「换个衣服」「修改这张图」时调用",
      P(image_path=S("原图路径或URL", True),
        prompt=S("修改描述（如：换成红色衣服、加上翅膀）", True),
        strength=S("修改强度：light/medium/heavy（默认medium）")))
async def edit_image_tool(image_path: str = "", prompt: str = "", strength: str = "medium") -> str:
    if not image_path or not prompt:
        return "需要提供图片路径和修改描述"
    from pathlib import Path

    # 如果是URL（QQ图片等），先下载到本地
    actual_path = image_path
    if image_path.startswith("http://") or image_path.startswith("https://"):
        try:
            import aiohttp, time as _t
            async with aiohttp.ClientSession() as sess:
                async with sess.get(image_path, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        temp_dir = Path("data/temp")
                        temp_dir.mkdir(parents=True, exist_ok=True)
                        ext = image_path.split(".")[-1][:4] if "." in image_path.split("/")[-1] else "jpg"
                        temp_file = temp_dir / f"edit_{int(_t.time())}.{ext}"
                        temp_file.write_bytes(await resp.read())
                        actual_path = str(temp_file)
        except Exception:
            return "图片下载失败"

    if not Path(actual_path).exists():
        return f"图片不存在: {actual_path}"

    # 强度映射
    denoise_map = {"light": 0.3, "medium": 0.5, "heavy": 0.7}
    denoise = denoise_map.get(strength, 0.5)

    from white_salary.adapters.tools.image_gen import edit_image as _edit
    result = await _edit(actual_path, prompt, denoise)
    if result:
        return f"图片已修改: {result}"
    # 2026-07-03 外部依赖优化（批8）：改图当前只有本地 ComfyUI img2img 一条链路，
    # 失败时给明确中文提示指向配置文档，不静默含糊
    return (
        "图片修改失败：改图(img2img)目前需要本地 ComfyUI。"
        "请安装并启动 ComfyUI，路径可在 conf.yaml 的 external_tools.comfyui_bat 配置，"
        "详见 docs/EXTERNAL_SERVICES.md"
    )


# 2026-07-02 审计修复（批2）：下架空壳工具 sing/music_gen——
# 固定返回「暂时不可用」（RVC链路三重断裂：依赖未装、模型目录为空、适配器无人实例化）。
# 函数体保留，待真实现后再加回 TOOLS。
# 2026-07-03 工具实现（批9）：describe_image 已重写为真调视觉模型
# （注册表 'vision' 实例优先、conf.yaml llm_vision 现场构造兜底），加回 TOOLS。
TOOLS = [fn._tool_def for fn in [
    generate_image, draw, generate_sticker,
    screenshot, edit_image_tool, describe_image,
]]

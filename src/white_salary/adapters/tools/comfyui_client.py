"""
white_salary/adapters/tools/comfyui_client.py

ComfyUI HTTP API 客户端 — 通过API遥控本地ComfyUI生成图片/视频。

原则：只做遥控器，不修改ComfyUI的任何文件/配置/模型。

流程：
  1. 读取workflow模板JSON
  2. 替换模板变量（prompt/模型/尺寸等）
  3. 提交给ComfyUI /prompt API
  4. 轮询 /history/{prompt_id} 等待完成
  5. 下载生成的图片到本地
"""

import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


# ComfyUI默认地址
COMFYUI_URL = "http://127.0.0.1:8188"

# ComfyUI启动脚本路径（新版v0.12.3，40插件+顶级模型）
# 2026-07-03 外部依赖优化（批8）：保留模块级常量（环境变量优先，向后兼容既有
# WS_COMFYUI_BAT 行为与单测），但真正启动/复制文件时改调 external_paths.get_comfyui_bat()
# ——它在环境变量之外又加了一层 conf.yaml external_tools 配置回退（解析顺序：
# 环境变量 → 配置 → 内置默认值），便于换机器时不改源码。
COMFYUI_BAT = Path(os.environ["WS_COMFYUI_BAT"]) if os.environ.get("WS_COMFYUI_BAT") else None

# 防重复启动标记
_starting = False

# Workflow模板目录
WORKFLOW_DIR = Path("config/comfyui_workflows")

# 可用模型（从检测到的模型中选择，优先动漫风格）
DEFAULT_MODEL = "illustriousXLV20_v20Stable.safetensors"

# 默认负面提示词
DEFAULT_NEGATIVE = (
    "worst quality, low quality, blurry, deformed, bad anatomy, "
    "bad hands, missing fingers, extra fingers, watermark, text, "
    "signature, jpeg artifacts, ugly"
)


async def is_comfyui_online() -> bool:
    """检查ComfyUI是否在线（不阻塞，3秒超时）。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COMFYUI_URL}/system_stats",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


async def ensure_comfyui_running(timeout: int = 60) -> bool:
    """
    确保ComfyUI正在运行。如果没运行就自动启动，等待就绪。

    Args:
        timeout: 最多等待秒数（加载模型需要时间）

    Returns:
        True=ComfyUI已就绪，False=启动失败
    """
    global _starting

    # 已经在线，直接返回
    if await is_comfyui_online():
        return True

    # 2026-07-03 外部依赖优化（批8）：启动脚本路径改走统一解析（环境变量→配置→默认），
    # 换机器时改 conf.yaml external_tools.comfyui_bat 即可，无需改源码或设环境变量
    from white_salary.adapters.tools.external_paths import get_comfyui_bat
    try:
        comfyui_bat = get_comfyui_bat()
    except FileNotFoundError as exc:
        logger.debug(f"[ComfyUI] {exc}")
        return False

    # 检查启动脚本是否存在（提前检查，避免无意义等待）
    if not comfyui_bat.exists():
        logger.debug(f"[ComfyUI] 启动脚本不存在: {comfyui_bat}")
        return False

    # 正在启动中（其他请求已触发），等待就绪
    if _starting:
        import asyncio
        start = time.time()
        while time.time() - start < timeout:
            if await is_comfyui_online():
                return True
            await asyncio.sleep(3)
        return False

    # 启动ComfyUI（后台运行，不阻塞）
    try:
        _starting = True
        logger.info("[ComfyUI] 未检测到ComfyUI，正在自动启动...")

        subprocess.Popen(
            str(comfyui_bat),
            cwd=str(comfyui_bat.parent),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                        | getattr(subprocess, 'DETACHED_PROCESS', 0),
        )

        # 等待ComfyUI启动完成
        import asyncio
        start = time.time()
        while time.time() - start < timeout:
            if await is_comfyui_online():
                logger.info(f"[ComfyUI] 启动成功（等待了{int(time.time()-start)}秒）")
                _starting = False
                return True
            await asyncio.sleep(3)

        logger.warning(f"[ComfyUI] 启动超时（{timeout}秒）")
        _starting = False
        return False

    except Exception as e:
        logger.warning(f"[ComfyUI] 启动失败: {e}")
        _starting = False
        return False


async def generate_image(
    prompt: str,
    negative_prompt: str = "",
    model: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 25,
    cfg: float = 7.0,
    seed: int = 0,
    quality: str = "hires",
) -> Optional[str]:
    """
    通过ComfyUI生成图片。

    Args:
        prompt: 正面提示词
        negative_prompt: 负面提示词
        model: checkpoint模型名（空=用默认）
        width: 图片宽度
        height: 图片高度
        steps: 采样步数
        cfg: CFG引导强度
        seed: 随机种子（0=随机）
        quality: 质量模式 "fast"=快速(15秒) / "hires"=精细Hires Fix(40秒)

    Returns:
        生成图片的本地路径，失败返回None
    """
    if not await is_comfyui_online():
        logger.debug("[ComfyUI] 未在线")
        return None

    # 根据质量模式选workflow
    if quality == "pro":
        workflow = _load_workflow("txt2img_pro.json")
        if not workflow:
            logger.info("[ComfyUI] 专业模式workflow不存在，降级为精细模式")
            quality = "hires"
    if quality == "hires":
        workflow = _load_workflow("txt2img_hires.json")
        if not workflow:
            logger.info("[ComfyUI] 精细模式workflow不存在，降级为快速模式")
            workflow = _load_workflow("txt2img.json")
    elif quality not in ("pro",):
        workflow = _load_workflow("txt2img.json")

    if not workflow:
        logger.warning("[ComfyUI] 找不到workflow模板")
        return None

    # 填入参数
    model_name = model or DEFAULT_MODEL
    neg = negative_prompt or DEFAULT_NEGATIVE
    actual_seed = seed if seed > 0 else random.randint(1, 2**32 - 1)

    # 精细模式：半分辨率出草稿，放大到目标分辨率精修
    half_w = max(512, width // 2)
    half_h = max(512, height // 2)

    workflow_str = json.dumps(workflow)
    workflow_str = workflow_str.replace("{{prompt}}", _escape_json_str(prompt))
    workflow_str = workflow_str.replace("{{negative_prompt}}", _escape_json_str(neg))
    workflow_str = workflow_str.replace("{{model}}", model_name)
    # 数字占位符：去引号替换（"{{width}}" → 1024）
    workflow_str = workflow_str.replace('"{{width}}"', str(width))
    workflow_str = workflow_str.replace('"{{height}}"', str(height))
    workflow_str = workflow_str.replace('"{{half_width}}"', str(half_w))
    workflow_str = workflow_str.replace('"{{half_height}}"', str(half_h))
    workflow_json = json.loads(workflow_str)

    # 设置动态参数（seed/steps/cfg）
    # pro模式用节点6(KSampler)、14(脸Detailer)、24(手Detailer)、31(Upscale)
    # hires模式用节点3/10/16
    # fast模式用节点3
    seed_nodes = ["3", "6", "10", "14", "16", "24", "31"]
    for node_id in seed_nodes:
        if node_id in workflow_json and "inputs" in workflow_json[node_id]:
            inputs = workflow_json[node_id]["inputs"]
            if "seed" in inputs:
                inputs["seed"] = actual_seed

    # 主KSampler的steps和cfg（节点3/6=主采样）
    for node_id in ["3", "6"]:
        if node_id in workflow_json and "inputs" in workflow_json[node_id]:
            inputs = workflow_json[node_id]["inputs"]
            if "steps" in inputs:
                inputs["steps"] = steps
            if "cfg" in inputs:
                inputs["cfg"] = cfg
    # 放大重绘固定65步（节点31，不跟主采样联动）
    if "31" in workflow_json and "inputs" in workflow_json["31"]:
        workflow_json["31"]["inputs"]["steps"] = 65

    mode_names = {"pro": "专业(Detailer+Upscale)", "hires": "精细(Hires Fix)", "fast": "快速"}
    mode_name = mode_names.get(quality, "快速")
    logger.info(f"[ComfyUI] {mode_name}模式，模型={model_name}，尺寸={width}x{height}")

    # 提交给ComfyUI
    prompt_id = await _submit_prompt(workflow_json)
    if not prompt_id:
        return None

    # 轮询等待完成（最多120秒）
    result = await _wait_for_result(prompt_id, timeout=120)
    if not result:
        logger.warning(f"[ComfyUI] 生成超时: {prompt_id}")
        return None

    # 下载图片
    image_path = await _download_result(result)
    return image_path


async def edit_image(
    image_path: str,
    prompt: str,
    negative_prompt: str = "",
    model: str = "",
    denoise: float = 0.5,
    steps: int = 25,
    cfg: float = 7.0,
    seed: int = 0,
) -> Optional[str]:
    """
    通过ComfyUI修改图片（img2img）。

    Args:
        image_path: 原图本地路径
        prompt: 修改提示词（描述想要的效果）
        negative_prompt: 负面提示词
        model: checkpoint模型名（空=用默认IllustriousXL v2.0）
        denoise: 修改强度 0.0~1.0（0.3=轻微/0.5=中等/0.7=大改）
        steps: 采样步数
        cfg: CFG引导强度
        seed: 随机种子（0=随机）

    Returns:
        修改后图片的本地路径，失败返回None
    """
    if not await is_comfyui_online():
        logger.debug("[ComfyUI] 未在线")
        return None

    workflow = _load_workflow("img2img.json")
    if not workflow:
        logger.warning("[ComfyUI] img2img workflow不存在")
        return None

    # 把原图复制到ComfyUI的input目录
    src_path = Path(image_path)
    if not src_path.exists():
        logger.warning(f"[ComfyUI] 原图不存在: {image_path}")
        return None

    # 2026-07-03 外部依赖优化（批8）：input 目录改走统一解析——优先用配置里显式的
    # comfyui_input（避免只配了 comfyui_bat 而 input 目录布局不同的情形），
    # 未配置时回退到"启动脚本同级 ComfyUI/input"（与旧行为一致）
    from white_salary.adapters.tools.external_paths import get_comfyui_bat, get_comfyui_input
    try:
        input_dir = get_comfyui_input()
    except FileNotFoundError:
        try:
            input_dir = get_comfyui_bat().parent / "ComfyUI" / "input"
        except FileNotFoundError as exc:
            logger.debug(f"[ComfyUI] input directory is not configured: {exc}")
            return None
    input_dir.mkdir(parents=True, exist_ok=True)
    input_name = f"ws_edit_{int(time.time())}_{src_path.name}"
    dest_path = input_dir / input_name
    import shutil
    shutil.copy2(str(src_path), str(dest_path))

    # 填入参数
    model_name = model or DEFAULT_MODEL
    neg = negative_prompt or DEFAULT_NEGATIVE
    actual_seed = seed if seed > 0 else random.randint(1, 2**32 - 1)
    actual_denoise = max(0.1, min(1.0, denoise))

    workflow_str = json.dumps(workflow)
    workflow_str = workflow_str.replace("{{prompt}}", _escape_json_str(prompt))
    workflow_str = workflow_str.replace("{{negative_prompt}}", _escape_json_str(neg))
    workflow_str = workflow_str.replace("{{model}}", model_name)
    workflow_str = workflow_str.replace("{{input_image}}", input_name)
    workflow_str = workflow_str.replace('"{{denoise}}"', str(actual_denoise))
    workflow_json = json.loads(workflow_str)

    # 设置seed/steps/cfg
    if "3" in workflow_json and "inputs" in workflow_json["3"]:
        inputs = workflow_json["3"]["inputs"]
        inputs["seed"] = actual_seed
        inputs["steps"] = steps
        inputs["cfg"] = cfg

    logger.info(f"[ComfyUI] img2img: denoise={actual_denoise}, 模型={model_name}")

    # 提交给ComfyUI
    prompt_id = await _submit_prompt(workflow_json)
    if not prompt_id:
        return None

    result = await _wait_for_result(prompt_id, timeout=120)
    if not result:
        logger.warning(f"[ComfyUI] img2img超时: {prompt_id}")
        return None

    return await _download_result(result)


def _load_workflow(name: str) -> Optional[dict]:
    """加载workflow模板JSON。"""
    path = WORKFLOW_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[ComfyUI] 加载workflow失败: {e}")
        return None


def _escape_json_str(s: str) -> str:
    """转义JSON字符串中的特殊字符。"""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


async def _submit_prompt(workflow: dict) -> Optional[str]:
    """提交workflow到ComfyUI，返回prompt_id。"""
    try:
        payload = {"prompt": workflow}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{COMFYUI_URL}/prompt",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prompt_id = data.get("prompt_id", "")
                    if prompt_id:
                        logger.info(f"[ComfyUI] 已提交: {prompt_id}")
                        return prompt_id
                else:
                    body = await resp.text()
                    logger.warning(f"[ComfyUI] 提交失败({resp.status}): {body[:200]}")
    except Exception as e:
        logger.warning(f"[ComfyUI] 提交异常: {e}")
    return None


async def _wait_for_result(prompt_id: str, timeout: int = 120) -> Optional[dict]:
    """轮询等待生成完成，返回输出信息。"""
    import asyncio
    start = time.time()

    while time.time() - start < timeout:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{COMFYUI_URL}/history/{prompt_id}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if prompt_id in data:
                            outputs = data[prompt_id].get("outputs", {})
                            if outputs:
                                return outputs
        except Exception:
            pass

        await asyncio.sleep(2)  # 每2秒检查一次

    return None


async def _download_result(outputs: dict) -> Optional[str]:
    """从ComfyUI下载生成的图片。"""
    # 找到SaveImage节点的输出
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        for img_info in images:
            filename = img_info.get("filename", "")
            subfolder = img_info.get("subfolder", "")
            img_type = img_info.get("type", "output")

            if not filename:
                continue

            try:
                params = {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{COMFYUI_URL}/view",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status == 200:
                            img_bytes = await resp.read()
                            # 保存到本地
                            save_dir = Path("data/images")
                            save_dir.mkdir(parents=True, exist_ok=True)
                            ext = filename.rsplit(".", 1)[-1] if "." in filename else "png"
                            local_name = f"comfyui_{int(time.time())}_{random.randint(100,999)}.{ext}"
                            local_path = save_dir / local_name
                            local_path.write_bytes(img_bytes)
                            logger.info(f"[ComfyUI] 图片已保存: {local_path} ({len(img_bytes)//1024}KB)")
                            return str(local_path)
            except Exception as e:
                logger.warning(f"[ComfyUI] 下载图片失败: {e}")

    return None


async def list_models() -> list[str]:
    """获取ComfyUI可用的checkpoint模型列表。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COMFYUI_URL}/object_info/CheckpointLoaderSimple",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = (
                        data.get("CheckpointLoaderSimple", {})
                        .get("input", {})
                        .get("required", {})
                        .get("ckpt_name", [[], {}])[0]
                    )
                    return models if isinstance(models, list) else []
    except Exception:
        pass
    return []


async def generate_video_wan22(
    input_image: str,
    prompt: str = "",
    negative_prompt: str = "",
    model: str = "Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v2.1.safetensors",
    width: int = 832,
    height: int = 480,
    frames: int = 49,
    steps: int = 25,
    cfg: float = 3.0,
    seed: int = 0,
) -> Optional[str]:
    """
    通过ComfyUI + Wan2.2 NSFW生成高质量视频（无安全过滤）。

    4个模型组合：
    - Wan2.2 NSFW主模型（14GB fp8）via WanVideoModelLoader
    - UMT5文本编码器（11GB bf16→fp8量化）via LoadWanVideoT5TextEncoder
    - CLIP Vision H（2.4GB）via CLIPVisionLoader
    - Wan2.1 VAE（243MB）via WanVideoVAELoader

    Returns:
        生成的GIF文件路径
    """
    if not await is_comfyui_online():
        return None

    neg = negative_prompt or "worst quality, blurry, static, no motion, ugly"
    actual_seed = seed if seed > 0 else random.randint(1, 2**32 - 1)
    pos_prompt = prompt or "gentle motion, wind blowing hair, slight body movement"

    # 直接构造workflow（不用模板，避免JSON变量替换问题）
    workflow = {
        # Block Swap：把20/40个transformer block交换到CPU，防止16GB显存OOM
        "0": {"class_type": "WanVideoBlockSwap", "inputs": {
            "blocks_to_swap": 20,
            "offload_img_emb": True,
            "offload_txt_emb": True,
            "use_non_blocking": True,
            "vace_blocks_to_swap": 0,
            "prefetch_blocks": 1,
            "block_swap_debug": False,
        }},
        "1": {"class_type": "WanVideoModelLoader", "inputs": {
            "model": model, "base_precision": "bf16", "quantization": "fp8_e4m3fn",
            "load_device": "main_device", "attention_mode": "sdpa",
            "block_swap_args": ["0", 0],  # 连接BlockSwap
        }},
        "2": {"class_type": "LoadWanVideoT5TextEncoder", "inputs": {
            "model_name": "nsfw_wan_umt5-xxl_bf16.safetensors", "precision": "bf16",
            "load_device": "offload_device", "quantization": "fp8_e4m3fn",
        }},
        "3": {"class_type": "CLIPVisionLoader", "inputs": {
            "clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        }},
        "4": {"class_type": "WanVideoVAELoader", "inputs": {
            "model_name": "wan_2.1_vae.safetensors", "precision": "bf16",
        }},
        "5": {"class_type": "WanVideoTextEncode", "inputs": {
            "positive_prompt": pos_prompt, "negative_prompt": neg,
            "t5": ["2", 0], "force_offload": True,
        }},
        "6": {"class_type": "LoadImage", "inputs": {"image": input_image}},
        "7": {"class_type": "WanVideoClipVisionEncode", "inputs": {
            "clip_vision": ["3", 0], "image_1": ["6", 0],
            "strength_1": 1.0, "strength_2": 0.0, "crop": "center",
            "combine_embeds": "average", "force_offload": True,
        }},
        "8": {"class_type": "WanVideoImageToVideoEncode", "inputs": {
            "width": width, "height": height, "num_frames": frames,
            "noise_aug_strength": 0.0, "start_latent_strength": 1.0,
            "end_latent_strength": 0.0, "force_offload": True,
            "vae": ["4", 0], "clip_embeds": ["7", 0], "start_image": ["6", 0],
        }},
        "9": {"class_type": "WanVideoSampler", "inputs": {
            "model": ["1", 0], "image_embeds": ["8", 0],
            "steps": steps, "cfg": cfg, "shift": 3.0, "seed": actual_seed,
            "force_offload": True, "scheduler": "unipc", "riflex_freq_index": 0,
            "text_embeds": ["5", 0], "denoise_strength": 1.0,
        }},
        "10": {"class_type": "WanVideoPassImagesFromSamples", "inputs": {"samples": ["9", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "WhiteSalary_Wan22", "images": ["10", 0],
        }},
    }

    logger.info(f"[ComfyUI] Wan2.2 NSFW I2V: {frames}帧 {width}x{height}, 模型={model}")

    prompt_id = await _submit_prompt(workflow)
    if not prompt_id:
        return None

    # Wan2.2 14B很慢，给15分钟
    result = await _wait_for_result(prompt_id, timeout=900)
    if not result:
        logger.warning("[ComfyUI] Wan2.2生成超时(15分钟)")
        return None

    return await _download_frames_and_make_gif(result, fps=16, prefix="wan22")


async def generate_video_animatediff(
    prompt: str,
    negative_prompt: str = "",
    model: str = "",
    width: int = 768,
    height: int = 768,
    frames: int = 16,
    fps: int = 8,
    seed: int = 0,
) -> Optional[str]:
    """
    通过ComfyUI + AnimateDiff生成短动画（SDXL质量）。

    用SD1.5模型 + v3 motion module生成动画帧，
    下载帧序列后用Pillow合成GIF。

    Returns:
        生成的GIF文件路径
    """
    if not await is_comfyui_online():
        return None

    workflow = _load_workflow("animatediff.json")
    if not workflow:
        logger.warning("[ComfyUI] 找不到animatediff workflow")
        return None

    # SD1.5 v3 motion module更稳定
    model_name = model or "Meinamix_meinaV11.safetensors"
    motion_model = "v3_sd15_mm.ckpt"
    neg = negative_prompt or DEFAULT_NEGATIVE
    actual_seed = seed if seed > 0 else random.randint(1, 2**32 - 1)

    workflow_str = json.dumps(workflow)
    workflow_str = workflow_str.replace("{{prompt}}", _escape_json_str(prompt))
    workflow_str = workflow_str.replace("{{negative_prompt}}", _escape_json_str(neg))
    workflow_str = workflow_str.replace("{{model}}", model_name)
    workflow_str = workflow_str.replace("{{motion_model}}", motion_model)
    workflow_str = workflow_str.replace("{{width}}", str(width))
    workflow_str = workflow_str.replace("{{height}}", str(height))
    workflow_str = workflow_str.replace("{{frames}}", str(frames))
    workflow_json = json.loads(workflow_str)

    if "3" in workflow_json:
        workflow_json["3"]["inputs"]["seed"] = actual_seed

    logger.info(f"[ComfyUI] AnimateDiff SDXL: {frames}帧 {width}x{height}, 模型={model_name}")

    prompt_id = await _submit_prompt(workflow_json)
    if not prompt_id:
        return None

    # SDXL+AnimateDiff比较慢，给300秒
    result = await _wait_for_result(prompt_id, timeout=300)
    if not result:
        logger.warning("[ComfyUI] AnimateDiff生成超时")
        return None

    # 下载所有帧 → 合成GIF
    return await _download_frames_and_make_gif(result, fps=fps, prefix="animatediff")


async def generate_video_svd(
    input_image: str,
    width: int = 768,
    height: int = 512,
    frames: int = 14,
    seed: int = 0,
) -> Optional[str]:
    """
    通过ComfyUI + SVD将图片变成视频。

    768x512是16GB显存的安全分辨率。
    motion_bucket_id=180 + augmentation_level=0.05 确保有明显运动。

    Returns:
        生成的GIF文件路径
    """
    if not await is_comfyui_online():
        return None

    workflow = _load_workflow("svd.json")
    if not workflow:
        logger.warning("[ComfyUI] 找不到svd workflow")
        return None

    actual_seed = seed if seed > 0 else random.randint(1, 2**32 - 1)

    workflow_str = json.dumps(workflow)
    workflow_str = workflow_str.replace("{{input_image}}", _escape_json_str(input_image))
    workflow_str = workflow_str.replace("{{width}}", str(width))
    workflow_str = workflow_str.replace("{{height}}", str(height))
    workflow_str = workflow_str.replace("{{frames}}", str(frames))
    workflow_json = json.loads(workflow_str)

    if "4" in workflow_json:
        workflow_json["4"]["inputs"]["seed"] = actual_seed

    logger.info(f"[ComfyUI] SVD图生视频: {frames}帧 {width}x{height}, 图片={input_image}")

    prompt_id = await _submit_prompt(workflow_json)
    if not prompt_id:
        return None

    result = await _wait_for_result(prompt_id, timeout=300)
    if not result:
        logger.warning("[ComfyUI] SVD生成超时")
        return None

    return await _download_frames_and_make_gif(result, fps=6, prefix="svd")


async def _download_frames_and_make_gif(
    outputs: dict, fps: int = 8, prefix: str = "video"
) -> Optional[str]:
    """下载ComfyUI生成的帧序列，用Pillow合成GIF。"""
    # 收集所有帧的文件信息
    frame_infos = []
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        for img in images:
            if img.get("filename"):
                frame_infos.append(img)

    if not frame_infos:
        logger.warning("[ComfyUI] 没有找到输出帧")
        return None

    # 按文件名排序（确保帧顺序正确）
    frame_infos.sort(key=lambda x: x.get("filename", ""))
    logger.info(f"[ComfyUI] 下载 {len(frame_infos)} 帧...")

    # 下载所有帧
    frames_data = []
    async with aiohttp.ClientSession() as session:
        for fi in frame_infos:
            try:
                params = {
                    "filename": fi["filename"],
                    "subfolder": fi.get("subfolder", ""),
                    "type": fi.get("type", "output"),
                }
                async with session.get(
                    f"{COMFYUI_URL}/view", params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        frames_data.append(await resp.read())
            except Exception as e:
                logger.warning(f"[ComfyUI] 下载帧失败: {e}")

    if len(frames_data) < 2:
        logger.warning(f"[ComfyUI] 帧数不够: {len(frames_data)}")
        # 如果只有1帧，当图片保存
        if frames_data:
            save_dir = Path("data/videos")
            save_dir.mkdir(parents=True, exist_ok=True)
            p = save_dir / f"{prefix}_{int(time.time())}.png"
            p.write_bytes(frames_data[0])
            return str(p)
        return None

    # 用Pillow合成GIF
    try:
        from PIL import Image
        import io

        pil_frames = []
        for fd in frames_data:
            img = Image.open(io.BytesIO(fd))
            # 转RGB（GIF不支持RGBA）
            if img.mode != "RGB":
                img = img.convert("RGB")
            pil_frames.append(img)

        save_dir = Path("data/videos")
        save_dir.mkdir(parents=True, exist_ok=True)
        gif_path = save_dir / f"{prefix}_{int(time.time())}_{random.randint(100,999)}.gif"

        # duration = 每帧毫秒数
        duration = max(50, 1000 // fps)
        pil_frames[0].save(
            str(gif_path),
            save_all=True,
            append_images=pil_frames[1:],
            duration=duration,
            loop=0,  # 无限循环
            optimize=True,
        )

        total_size = gif_path.stat().st_size
        logger.info(f"[ComfyUI] GIF合成完成: {gif_path} ({len(pil_frames)}帧, {total_size//1024}KB)")
        return str(gif_path)

    except ImportError:
        logger.warning("[ComfyUI] 缺少Pillow库，无法合成GIF")
        # 降级：保存第一帧为图片
        save_dir = Path("data/videos")
        save_dir.mkdir(parents=True, exist_ok=True)
        p = save_dir / f"{prefix}_{int(time.time())}.png"
        p.write_bytes(frames_data[0])
        return str(p)
    except Exception as e:
        logger.warning(f"[ComfyUI] GIF合成失败: {e}")
        return None

"""
white_salary/adapters/tools/image_gen.py

图片生成工具 — 3级fallback：ComfyUI(本地) → DMXAPI → SiliconFlow。

借鉴v2的tools/image_gen.py（1800行），但大幅简化：
  - v2有复杂的身体暴露检测和NSFW模式，我们不做
  - v2的3级fallback架构很好，保留
  - v2的权限系统（只有主人能用），保留
  - v2的外观提示词很详细，我们用简化版

功能：
  - 根据用户描述生成图片
  - ComfyUI本地生成（最高质量）
  - DMXAPI云端生成（备选）
  - SiliconFlow FLUX生成（兜底）
  - 返回图片URL或Base64
"""

import json
import time
import base64
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


# 默认配置
COMFYUI_URL = "http://127.0.0.1:8188"
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/images/generations"
DMXAPI_URL = "https://www.dmxapi.cn/v1/images/generations"

# 图片保存目录
IMAGE_DIR = Path("data/images")

def _load_style_config() -> dict:
    """从config/image_style.json加载形象配置。"""
    cfg_path = Path("config/image_style.json")
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _get_appearance() -> str:
    """获取白的完整外观提示词。"""
    cfg = _load_style_config()
    parts = [
        cfg.get("base_appearance", "1girl, silver white long hair, silver-gray eyes, beautiful face"),
        cfg.get("outfit_default", "futuristic sci-fi outfit"),
        cfg.get("quality_tags", "high quality, masterpiece, best quality"),
    ]
    return ", ".join(parts)

def _get_negative() -> str:
    """获取负面提示词。"""
    cfg = _load_style_config()
    return cfg.get("negative_prompt",
        "worst quality, low quality, blurry, deformed, bad anatomy, "
        "bad hands, missing fingers, extra fingers, watermark, text")

def _is_self_portrait(prompt: str) -> bool:
    """判断是否是自画像请求。"""
    cfg = _load_style_config()
    keywords = cfg.get("trigger_keywords", ["自拍", "自画像", "白的", "你的照片"])
    return any(kw in prompt for kw in keywords)


def _save_image(data: bytes, ext: str = "png") -> str:
    """保存图片到本地，返回文件路径。"""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time())}_{id(data) % 10000}.{ext}"
    path = IMAGE_DIR / filename
    path.write_bytes(data)
    logger.info(f"[ImageGen] 图片已保存: {path}")
    return str(path)


async def generate_image(
    prompt: str,
    siliconflow_key: str = "",
    dmxapi_key: str = "",
    size: str = "1024x1024",
    is_self_portrait: bool = False,
) -> Optional[str]:
    """
    生成图片，按优先级尝试3个供应商。

    Args:
        prompt: 用户描述
        siliconflow_key: SiliconFlow API key
        dmxapi_key: DMXAPI key
        size: 图片尺寸
        is_self_portrait: 是否是自画像（加入白的外观提示）

    Returns:
        图片URL，或None（全部失败）
    """
    # 构建最终prompt + 自画像自动切模型
    is_portrait = is_self_portrait or _is_self_portrait(prompt)
    if is_portrait:
        full_prompt = f"{_get_appearance()}, {prompt}"
    else:
        full_prompt = prompt

    # 1. 尝试ComfyUI本地
    result = await _try_comfyui(full_prompt, size, is_portrait)
    if result:
        return await _download_and_save(result)

    # 2. 尝试DMXAPI
    if dmxapi_key:
        result = await _try_dmxapi(full_prompt, dmxapi_key, size)
        if result:
            return await _download_and_save(result)

    # 3. 兜底SiliconFlow
    if siliconflow_key:
        result = await _try_siliconflow(full_prompt, siliconflow_key, size)
        if result:
            return await _download_and_save(result)

    logger.warning("[ImageGen] 所有供应商都失败了")
    return None


async def edit_image(
    image_path: str,
    prompt: str,
    denoise: float = 0.5,
) -> Optional[str]:
    """
    修改图片（img2img）。

    Args:
        image_path: 原图路径
        prompt: 修改提示词
        denoise: 修改强度（0.3=轻微, 0.5=中等, 0.7=大改）

    Returns:
        修改后图片路径，失败返回None
    """
    # ComfyUI本地
    try:
        from white_salary.adapters.tools.comfyui_client import edit_image as _comfyui_edit, ensure_comfyui_running
        await ensure_comfyui_running()
        result = await _comfyui_edit(
            image_path=image_path,
            prompt=prompt,
            denoise=denoise,
        )
        if result:
            return result
    except Exception as e:
        logger.debug(f"[ImageGen] ComfyUI img2img失败: {e}")

    logger.warning("[ImageGen] 图片修改失败")
    return None


async def _download_and_save(url_or_b64: str) -> Optional[str]:
    """下载图片URL或解码Base64，保存到本地并返回路径。"""
    try:
        if url_or_b64.startswith("data:image"):
            # Base64格式: data:image/png;base64,xxxxx
            b64_data = url_or_b64.split(",", 1)[1]
            img_bytes = base64.b64decode(b64_data)
            return _save_image(img_bytes)
        elif url_or_b64.startswith("http"):
            # URL格式：下载图片
            async with aiohttp.ClientSession() as session:
                async with session.get(url_or_b64, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        ext = "png"
                        ct = resp.headers.get("Content-Type", "")
                        if "jpeg" in ct or "jpg" in ct:
                            ext = "jpg"
                        elif "webp" in ct:
                            ext = "webp"
                        return _save_image(img_bytes, ext)
        else:
            # 可能是纯Base64
            img_bytes = base64.b64decode(url_or_b64)
            return _save_image(img_bytes)
    except Exception as e:
        logger.warning(f"[ImageGen] 保存图片失败: {e}")
    # 保存失败就返回原URL
    return url_or_b64


async def _try_comfyui(prompt: str, size: str, is_portrait: bool = False) -> Optional[str]:
    """尝试ComfyUI本地生成（通过API遥控，不修改ComfyUI文件）。"""
    try:
        from white_salary.adapters.tools.comfyui_client import generate_image as _comfyui_gen, ensure_comfyui_running

        # 自动检测+启动ComfyUI（没运行就启动，最多等60秒）
        if not await ensure_comfyui_running(timeout=60):
            return None

        # 解析尺寸
        try:
            w, h = size.split("x")
            width, height = int(w), int(h)
        except Exception:
            width, height = 1024, 1024

        # 从配置读取模型（自画像用Illustrious-XL动漫最强，其他用NoobAI-XL综合最强）
        cfg = _load_style_config()
        comfyui_cfg = cfg.get("providers", {}).get("comfyui", {})
        if is_portrait and comfyui_cfg.get("self_portrait_model"):
            model = comfyui_cfg["self_portrait_model"]
            logger.info(f"[ImageGen] 自画像模式，使用 {model}")
        else:
            model = comfyui_cfg.get("model", "")

        # 从配置读取质量模式（默认精细）
        quality = cfg.get("default_quality", "hires")

        result = await _comfyui_gen(
            prompt=prompt,
            negative_prompt=_get_negative(),
            model=model,
            width=width,
            height=height,
            quality=quality,
        )
        if result:
            logger.info(f"[ImageGen] ComfyUI本地生成成功: {result}")
            return result

    except ImportError:
        logger.debug("[ImageGen] comfyui_client未安装")
    except Exception as e:
        logger.debug(f"[ImageGen] ComfyUI异常: {e}")
    return None


async def _try_dmxapi(prompt: str, api_key: str, size: str) -> Optional[str]:
    """尝试DMXAPI生成。"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "dall-e-3",
                "prompt": prompt,
                "size": size,
                "n": 1,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            async with session.post(
                DMXAPI_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    item = data.get("data", [{}])[0]
                    # DMXAPI可能返回url或b64_json
                    url = item.get("url", "")
                    b64 = item.get("b64_json", "")
                    if url:
                        logger.info("[ImageGen] DMXAPI生成成功(url)")
                        return url
                    elif b64:
                        logger.info("[ImageGen] DMXAPI生成成功(base64)")
                        return f"data:image/png;base64,{b64}"
                else:
                    body = await resp.text()
                    logger.debug(f"[ImageGen] DMXAPI失败({resp.status}): {body[:100]}")

    except Exception as e:
        logger.debug(f"[ImageGen] DMXAPI异常: {e}")
    return None


async def _try_siliconflow(prompt: str, api_key: str, size: str) -> Optional[str]:
    """尝试SiliconFlow FLUX生成。"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "Qwen/Qwen-Image",
                "prompt": prompt,
                "image_size": size,
                "num_inference_steps": 20,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            async with session.post(
                SILICONFLOW_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    images = data.get("images", [])
                    if images:
                        url = images[0].get("url", "")
                        if url:
                            logger.info("[ImageGen] SiliconFlow生成成功")
                            return url
                else:
                    body = await resp.text()
                    logger.debug(f"[ImageGen] SiliconFlow失败({resp.status}): {body[:100]}")

    except Exception as e:
        logger.debug(f"[ImageGen] SiliconFlow异常: {e}")
    return None

"""
white_salary/adapters/tools/video_gen.py

视频生成工具 — 云端Wan2.2（最好） + 本地SVD（备选）。

云端：硅基流动 Wan-AI/Wan2.2-T2V-A14B（文字→视频）/ Wan2.2-I2V-A14B（图片→视频）
本地：ComfyUI + svd_xt（图片→视频）

流程（云端）：
  1. 提交生成请求到 /v1/video/submit
  2. 轮询 /v1/video/status 等待完成
  3. 下载MP4视频到本地
"""

import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


# 视频保存目录
VIDEO_DIR = Path("data/videos")


async def generate_video_from_text(
    prompt: str,
    api_key: str = "",
    size: str = "1280x720",
    negative_prompt: str = "",
) -> Optional[str]:
    """
    文字→视频（云端Wan2.2）。

    Args:
        prompt: 视频描述
        api_key: SiliconFlow API Key
        size: 视频尺寸

    Returns:
        视频文件本地路径
    """
    if not api_key:
        logger.warning("[VideoGen] 缺少SiliconFlow API Key")
        return None

    try:
        payload = {
            "model": "Wan-AI/Wan2.2-T2V-A14B",
            "prompt": prompt,
            "image_size": size,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        # 提交任务
        request_id = await _submit_video(api_key, payload)
        if not request_id:
            return None

        # 轮询等结果（最多5分钟）
        video_url = await _poll_video_result(api_key, request_id, timeout=300)
        if not video_url:
            return None

        # 下载保存
        return await _download_video(video_url, prefix="t2v")

    except Exception as e:
        logger.warning(f"[VideoGen] 文字生视频失败: {e}")
        return None


async def generate_video_from_image(
    image_url: str,
    prompt: str = "",
    api_key: str = "",
) -> Optional[str]:
    """
    图片→视频（云端Wan2.2 I2V）。

    Args:
        image_url: 输入图片URL
        prompt: 运动描述（可选）
        api_key: SiliconFlow API Key

    Returns:
        视频文件本地路径
    """
    if not api_key:
        logger.warning("[VideoGen] 缺少SiliconFlow API Key")
        return None

    try:
        payload = {
            "model": "Wan-AI/Wan2.2-I2V-A14B",
            "image": image_url,
        }
        if prompt:
            payload["prompt"] = prompt

        request_id = await _submit_video(api_key, payload)
        if not request_id:
            return None

        video_url = await _poll_video_result(api_key, request_id, timeout=300)
        if not video_url:
            return None

        return await _download_video(video_url, prefix="i2v")

    except Exception as e:
        logger.warning(f"[VideoGen] 图生视频失败: {e}")
        return None


async def _submit_video(api_key: str, payload: dict) -> Optional[str]:
    """提交视频生成任务，返回requestId。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.siliconflow.cn/v1/video/submit",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rid = data.get("requestId", "")
                    if rid:
                        logger.info(f"[VideoGen] 任务已提交: {rid}")
                        return rid
                body = await resp.text()
                logger.warning(f"[VideoGen] 提交失败({resp.status}): {body[:150]}")
    except Exception as e:
        logger.warning(f"[VideoGen] 提交异常: {e}")
    return None


async def _poll_video_result(
    api_key: str, request_id: str, timeout: int = 300
) -> Optional[str]:
    """轮询视频生成结果。"""
    import asyncio
    start = time.time()

    while time.time() - start < timeout:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.siliconflow.cn/v1/video/status",
                    json={"requestId": request_id},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("status", "")

                        if status == "Succeed":
                            videos = data.get("results", {}).get("videos", [])
                            if videos:
                                url = videos[0].get("url", "")
                                if url:
                                    elapsed = int(time.time() - start)
                                    logger.info(f"[VideoGen] 生成成功（{elapsed}秒）")
                                    return url
                        elif status == "Failed":
                            reason = data.get("reason", "未知原因")
                            logger.warning(f"[VideoGen] 生成失败: {reason}")
                            return None
                        # InQueue / Processing → 继续等
        except Exception:
            pass

        await asyncio.sleep(5)

    logger.warning(f"[VideoGen] 生成超时（{timeout}秒）")
    return None


async def _download_video(url: str, prefix: str = "video") -> Optional[str]:
    """下载视频到本地。"""
    try:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    video_bytes = await resp.read()
                    # 判断格式
                    ext = "mp4"
                    ct = resp.headers.get("Content-Type", "")
                    if "webm" in ct:
                        ext = "webm"

                    filename = f"{prefix}_{int(time.time())}.{ext}"
                    path = VIDEO_DIR / filename
                    path.write_bytes(video_bytes)
                    logger.info(f"[VideoGen] 视频已保存: {path} ({len(video_bytes)//1024}KB)")
                    return str(path)
    except Exception as e:
        logger.warning(f"[VideoGen] 下载视频失败: {e}")
    return None


def _find_ffmpeg() -> Optional[str]:
    """
    找到可用的ffmpeg。

    2026-07-03 外部依赖优化（批8）：改调 external_paths.find_ffmpeg——统一解析顺序为
    环境变量 WS_FFMPEG_PATH → conf.yaml external_tools.ffmpeg_path → 内置候选(新→旧) → PATH。
    prefer_path_first=False 保留本模块历史顺序（先内置候选再 PATH），行为不变。
    """
    from white_salary.adapters.tools.external_paths import find_ffmpeg
    return find_ffmpeg(prefer_path_first=False)


def _extract_last_frame(video_path: str, output_path: str) -> bool:
    """用ffmpeg提取视频最后一帧为PNG。"""
    import subprocess
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("[VideoGen] 找不到ffmpeg")
        return False
    try:
        # 先取总帧数，再提取最后一帧
        result = subprocess.run(
            [ffmpeg, "-sseof", "-0.1", "-i", video_path,
             "-frames:v", "1", "-y", output_path],
            capture_output=True, text=True, timeout=30,
        )
        return Path(output_path).exists() and Path(output_path).stat().st_size > 0
    except Exception as e:
        logger.warning(f"[VideoGen] 提取最后一帧失败: {e}")
        return False


def _concat_videos(video_paths: list[str], output_path: str) -> bool:
    """用ffmpeg把多段MP4拼成一个。"""
    import subprocess
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return False

    # 写concat列表文件
    list_path = VIDEO_DIR / "_concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for vp in video_paths:
            # ffmpeg concat需要用正斜杠
            f.write(f"file '{Path(vp).as_posix()}'\n")

    try:
        result = subprocess.run(
            [ffmpeg, "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", "-y", output_path],
            capture_output=True, text=True, timeout=60,
        )
        list_path.unlink(missing_ok=True)
        return Path(output_path).exists() and Path(output_path).stat().st_size > 0
    except Exception as e:
        logger.warning(f"[VideoGen] 拼接失败: {e}")
        list_path.unlink(missing_ok=True)
        return False


async def generate_long_video(
    prompt: str = "",
    api_key: str = "",
    image_path: str = "",
    duration: int = 10,
    size: str = "1280x720",
) -> Optional[str]:
    """
    生成长视频（多段5秒拼接）。

    原理：
      第1段：输入图片 → 5秒视频 → 提取末帧
      第2段：末帧 → 5秒视频 → 提取末帧
      ...
      最后：ffmpeg拼成一个完整视频

    Args:
        prompt: 视频描述
        api_key: SiliconFlow API Key
        image_path: 起始图片路径（可选，没有就用T2V第一段）
        duration: 目标时长（秒），会向上取整到5的倍数
        size: 视频尺寸

    Returns:
        拼接后的视频文件路径
    """
    import base64

    segments = max(1, (duration + 4) // 5)  # 向上取整到5秒
    logger.info(f"[VideoGen] 长视频: {duration}秒 = {segments}段 x 5秒")

    segment_paths = []

    for i in range(segments):
        logger.info(f"[VideoGen] 生成第{i+1}/{segments}段...")

        if i == 0 and image_path:
            # 第一段用输入图片（I2V）
            img_path = Path(image_path)
            if img_path.exists():
                img_b64 = base64.b64encode(img_path.read_bytes()).decode()
                payload = {
                    "model": "Wan-AI/Wan2.2-I2V-A14B",
                    "image": f"data:image/png;base64,{img_b64}",
                    "prompt": prompt,
                    "image_size": size,
                }
            else:
                # 图片不存在，用T2V
                payload = {
                    "model": "Wan-AI/Wan2.2-T2V-A14B",
                    "prompt": prompt,
                    "image_size": size,
                }
        elif i == 0:
            # 没有输入图片，第一段用T2V
            payload = {
                "model": "Wan-AI/Wan2.2-T2V-A14B",
                "prompt": prompt,
                "image_size": size,
            }
        else:
            # 后续段：用上一段的末帧做I2V
            last_frame_path = str(VIDEO_DIR / f"_last_frame_{i}.png")
            if not _extract_last_frame(segment_paths[-1], last_frame_path):
                logger.warning(f"[VideoGen] 第{i+1}段：提取末帧失败，停止")
                break

            img_b64 = base64.b64encode(Path(last_frame_path).read_bytes()).decode()
            payload = {
                "model": "Wan-AI/Wan2.2-I2V-A14B",
                "image": f"data:image/png;base64,{img_b64}",
                "prompt": prompt,
                "image_size": size,
            }
            # 清理临时帧
            Path(last_frame_path).unlink(missing_ok=True)

        # 提交并等待
        request_id = await _submit_video(api_key, payload)
        if not request_id:
            logger.warning(f"[VideoGen] 第{i+1}段提交失败")
            break

        video_url = await _poll_video_result(api_key, request_id, timeout=300)
        if not video_url:
            logger.warning(f"[VideoGen] 第{i+1}段生成失败")
            break

        seg_path = await _download_video(video_url, prefix=f"seg{i+1}")
        if not seg_path:
            break

        segment_paths.append(seg_path)
        logger.info(f"[VideoGen] 第{i+1}段完成: {seg_path}")

    if not segment_paths:
        return None

    if len(segment_paths) == 1:
        # 只有一段，直接返回
        return segment_paths[0]

    # 多段拼接
    output_path = str(VIDEO_DIR / f"long_{int(time.time())}.mp4")
    if _concat_videos(segment_paths, output_path):
        # 清理段文件
        for sp in segment_paths:
            Path(sp).unlink(missing_ok=True)
        logger.info(f"[VideoGen] 长视频拼接完成: {output_path} ({len(segment_paths)}段)")
        return output_path
    else:
        # 拼接失败，返回第一段
        logger.warning("[VideoGen] 拼接失败，返回第一段")
        return segment_paths[0]


async def add_voiceover(
    video_path: str,
    text: str,
    voice: str = "anna",
    api_key: str = "",
) -> Optional[str]:
    """
    给视频加配音（云端CosyVoice2优先 → 本地备选）+ ffmpeg合成。

    Args:
        video_path: 无声视频路径
        text: 配音文本
        voice: 云端音色名（anna/bella/claire/diana）
        api_key: SiliconFlow API Key

    Returns:
        有声视频路径
    """
    import subprocess

    audio_path = None

    # 1. 云端CosyVoice2（快，有安全过滤）
    if api_key:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "FunAudioLLM/CosyVoice2-0.5B",
                    "input": text,
                    "voice": f"FunAudioLLM/CosyVoice2-0.5B:{voice}",
                }
                async with session.post(
                    "https://api.siliconflow.cn/v1/audio/speech",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        audio_bytes = await resp.read()
                        save_dir = Path("data/audio")
                        save_dir.mkdir(parents=True, exist_ok=True)
                        audio_path = str(save_dir / f"voiceover_{int(time.time())}.mp3")
                        Path(audio_path).write_bytes(audio_bytes)
                        logger.info(f"[VideoGen] 云端配音生成: {audio_path} ({len(audio_bytes)//1024}KB)")
                    else:
                        body = await resp.text()
                        logger.warning(f"[VideoGen] 云端配音失败({resp.status}): {body[:100]}")
        except Exception as e:
            logger.warning(f"[VideoGen] 云端配音异常: {e}")

    # 2. 本地CosyVoice2备选（无过滤，需要单独部署）
    if not audio_path:
        try:
            from white_salary.adapters.tools.cosyvoice_client import generate_speech
            audio_path = await generate_speech(text=text, voice=voice)
        except Exception:
            pass

    if not audio_path:
        logger.warning("[VideoGen] 配音生成失败（云端+本地都不可用）")
        return None

    # ffmpeg合成
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("[VideoGen] 找不到ffmpeg")
        return None

    output_path = str(VIDEO_DIR / f"voiced_{int(time.time())}.mp4")
    try:
        result = subprocess.run(
            [ffmpeg,
             "-i", video_path,      # 视频
             "-i", audio_path,       # 音频
             "-c:v", "copy",         # 视频流不重新编码
             "-c:a", "aac",          # 音频转AAC
             "-shortest",            # 以较短的为准
             "-y", output_path],
            capture_output=True, text=True, timeout=30,
        )
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            logger.info(f"[VideoGen] 配音合成完成: {output_path}")
            return output_path
    except Exception as e:
        logger.warning(f"[VideoGen] 配音合成失败: {e}")

    return None

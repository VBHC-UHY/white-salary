"""
API 可用性测试脚本。

逐一测试所有API提供商，判断哪些能用、哪些不能用。
每个API发送一个简单的"你好"请求，看能不能正常返回。

用法：
    python scripts/test_apis.py
"""

import asyncio
import json
import os
import time
import sys
from dataclasses import dataclass
from pathlib import Path

# 需要安装: pip install openai
try:
    from openai import AsyncOpenAI
except ImportError:
    print("请先安装 openai 库: pip install openai")
    sys.exit(1)


# =============================================================================
# 所有API提供商的配置
# 2026-07-03 开源准备（批7）：密钥不再写死在脚本里，改为从 scripts/providers.json
# （已被 .gitignore 忽略，不会进仓库）或同名大写环境变量读取。仓库内只保留
# scripts/providers.example.json 模板（key 留空）。这样 clone 后照模板填自己的
# key 即可，行为与旧脚本完全一致；老用户复制一份 example 填上 key 就能跑。
# =============================================================================

# 项目/脚本目录
_SCRIPT_DIR = Path(__file__).parent
# 密钥文件（gitignore，用户私有）与模板文件（进仓库，key 留空）
_PROVIDERS_FILE = _SCRIPT_DIR / "providers.json"
_PROVIDERS_EXAMPLE = _SCRIPT_DIR / "providers.example.json"


@dataclass
class ProviderConfig:
    """一个API提供商的配置。"""
    name: str           # 提供商名称
    api_key: str        # API密钥
    base_url: str       # API地址
    model: str          # 要测试的模型
    description: str    # 说明


def _load_providers() -> list[ProviderConfig]:
    """
    加载所有待测提供商配置。

    读取顺序：优先 scripts/providers.json 里每个条目的 api_key；
    若某条目 api_key 为空，则回退到同名大写环境变量（例如 name="deepseek"
    对应环境变量 PROVIDER_DEEPSEEK 或 DEEPSEEK_API_KEY）。

    找不到 providers.json 时给出友好指引并退出，避免拿空 key 去请求。
    """
    if not _PROVIDERS_FILE.exists():
        # 2026-07-03 开源准备（批7）：缺密钥文件时不裸跑，给出清晰的配置指引
        print("=" * 70)
        print("  未找到密钥文件: scripts/providers.json")
        print("=" * 70)
        print()
        print("  这个脚本需要真实的 API 密钥才能测试各家接口是否可用。")
        print("  密钥文件已被 .gitignore 忽略，不会进入 git 仓库，请按下面步骤配置：")
        print()
        print("    1. 复制模板:")
        print("         copy scripts\\providers.example.json scripts\\providers.json")
        print("       （Linux/Mac 用 cp）")
        print("    2. 编辑 scripts/providers.json，把你有的 api_key 填进去；")
        print("       没有的通道 api_key 留空即可（会自动跳过）。")
        print()
        print("  也可以改用环境变量：把某通道 api_key 留空，设置对应的大写环境变量，")
        print("  例如 DEEPSEEK_API_KEY / PROVIDER_DEEPSEEK。")
        print()
        sys.exit(1)

    try:
        raw = json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # 不裸吞异常：文件读不了/格式错都明确报出来
        print(f"读取 scripts/providers.json 失败: {e}")
        sys.exit(1)

    items = raw.get("providers", raw) if isinstance(raw, dict) else raw
    providers: list[ProviderConfig] = []
    for entry in items:
        name = str(entry.get("name", "")).strip()
        # api_key 优先取文件里的，空则回退到环境变量
        api_key = str(entry.get("api_key", "") or "").strip()
        if not api_key and name:
            env_name = name.upper()
            api_key = (
                os.environ.get(f"PROVIDER_{env_name}")
                or os.environ.get(f"{env_name}_API_KEY")
                or ""
            ).strip()
        providers.append(
            ProviderConfig(
                name=name,
                api_key=api_key,
                base_url=str(entry.get("base_url", "")).strip(),
                model=str(entry.get("model", "")).strip(),
                description=str(entry.get("description", name)).strip(),
            )
        )
    return providers


# 提供商列表在 main() 里按需加载（避免 import 时就退出，方便被测试引用）
PROVIDERS: list[ProviderConfig] = []


async def test_single_provider(provider: ProviderConfig) -> dict:
    """
    测试单个API提供商是否可用。

    发送一个简单的"你好"请求，检查能否正常返回。

    参数:
        provider: 提供商配置

    返回:
        测试结果字典
    """
    result = {
        "name": provider.name,
        "description": provider.description,
        "model": provider.model,
        "success": False,
        "response": "",
        "latency_ms": 0,
        "error": "",
    }

    # 2026-07-03 开源准备（批7）：没填 key 的通道直接跳过，不拿空 key 去请求
    if not provider.api_key:
        result["error"] = "未配置 api_key（跳过）"
        return result

    client = AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=30.0,
    )

    start_time = time.time()

    try:
        response = await client.chat.completions.create(
            model=provider.model,
            messages=[
                {"role": "user", "content": "你好，请用一句话回复我。"}
            ],
            max_tokens=100,
            temperature=0.7,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        reply = response.choices[0].message.content or ""

        result["success"] = True
        result["response"] = reply[:100]  # 只取前100字
        result["latency_ms"] = elapsed_ms

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = elapsed_ms
        result["error"] = str(e)[:200]

    finally:
        await client.close()

    return result


async def test_tts_siliconflow() -> dict:
    """
    测试硅基流动的TTS（语音合成）服务。
    """
    result = {
        "name": "siliconflow_tts_service",
        "description": "硅基流动 TTS - CosyVoice2",
        "success": False,
        "response": "",
        "latency_ms": 0,
        "error": "",
    }

    try:
        import aiohttp

        start_time = time.time()

        # 2026-07-03 开源准备（批7）：TTS 用的 key 也从密钥文件/环境变量取，不再写死。
        # 优先取 providers.json 里名为 siliconflow_tts 的通道，其次 siliconflow，
        # 最后回退环境变量 SILICONFLOW_TTS_API_KEY / SILICONFLOW_API_KEY。
        api_key = ""
        for p in PROVIDERS:
            if p.name in ("siliconflow_tts", "siliconflow") and p.api_key:
                api_key = p.api_key
                break
        if not api_key:
            api_key = (
                os.environ.get("SILICONFLOW_TTS_API_KEY")
                or os.environ.get("SILICONFLOW_API_KEY")
                or ""
            ).strip()
        if not api_key:
            result["error"] = "未配置硅基流动 key（跳过 TTS 测试）"
            return result
        url = "https://api.siliconflow.cn/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "FunAudioLLM/CosyVoice2-0.5B",
            "input": "你好，我是White Salary。",
            "voice": "speech:neuro-sama:d4avcj5sssvc73ed80g0:famfowwbzwtbbtobwsyk",
            "response_format": "mp3",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                elapsed_ms = int((time.time() - start_time) * 1000)
                if resp.status == 200:
                    audio_data = await resp.read()
                    result["success"] = True
                    result["response"] = f"音频生成成功，大小: {len(audio_data)} bytes"
                    result["latency_ms"] = elapsed_ms
                else:
                    body = await resp.text()
                    result["error"] = f"HTTP {resp.status}: {body[:200]}"
                    result["latency_ms"] = elapsed_ms

    except ImportError:
        result["error"] = "aiohttp未安装，跳过TTS测试"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


async def test_stt_siliconflow() -> dict:
    """
    测试硅基流动的STT（语音识别）服务。
    只检查API端点是否可达（不发送真实音频）。
    """
    result = {
        "name": "siliconflow_stt_service",
        "description": "硅基流动 STT - SenseVoiceSmall",
        "success": False,
        "response": "需要真实音频才能完整测试，这里只测API可达性",
        "latency_ms": 0,
        "error": "",
    }

    # STT需要真实音频文件，这里标记为待测试
    result["response"] = "STT需要音频输入，将在集成测试中验证"
    return result


async def main() -> None:
    """运行所有API测试。"""
    print("=" * 70)
    print("  White Salary - API 可用性测试")
    print("=" * 70)
    print()

    # 2026-07-03 开源准备（批7）：运行时从 providers.json/环境变量加载密钥
    global PROVIDERS
    PROVIDERS = _load_providers()

    # 测试所有LLM API
    results = []
    for provider in PROVIDERS:
        print(f"测试中: {provider.description} ...", end=" ", flush=True)
        result = await test_single_provider(provider)
        if result["success"]:
            print(f"OK ({result['latency_ms']}ms)")
        else:
            print(f"FAIL")
        results.append(result)

    # 测试TTS
    print(f"测试中: 硅基流动 TTS ...", end=" ", flush=True)
    tts_result = await test_tts_siliconflow()
    if tts_result["success"]:
        print(f"OK ({tts_result['latency_ms']}ms)")
    else:
        print(f"FAIL")
    results.append(tts_result)

    # STT跳过
    stt_result = await test_stt_siliconflow()
    results.append(stt_result)

    # 打印详细结果
    print()
    print("=" * 70)
    print("  测试结果汇总")
    print("=" * 70)
    print()

    success_list = []
    fail_list = []

    for r in results:
        status = "OK" if r["success"] else "FAIL"
        name = r.get("description") or r["name"]

        if r["success"]:
            success_list.append(r)
            print(f"  [OK]   {name}")
            print(f"         延迟: {r['latency_ms']}ms")
            if r.get("response"):
                print(f"         回复: {r['response'][:60]}")
        else:
            fail_list.append(r)
            print(f"  [FAIL] {name}")
            if r.get("error"):
                print(f"         错误: {r['error'][:80]}")
        print()

    print("=" * 70)
    print(f"  总计: {len(results)} 个API")
    print(f"  可用: {len(success_list)} 个")
    print(f"  不可用: {len(fail_list)} 个")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

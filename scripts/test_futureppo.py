"""Test futureppo key(s) to find the issue.

2026-07-03 开源准备（批7）：密钥不再写死在脚本里，改为从 scripts/providers.json
（已被 .gitignore 忽略）里名为 futureppo 的通道，或环境变量
FUTUREPPO_API_KEY / PROVIDER_FUTUREPPO 读取。仓库内只保留 providers.example.json 模板。
"""
import json
import os
import sys
from pathlib import Path

import requests

URL = "https://91vip.futureppo.top/v1/chat/completions"
MODEL = "claude-sonnet-4-6"

_PROVIDERS_FILE = Path(__file__).parent / "providers.json"


def _load_futureppo_key() -> str:
    """从 providers.json 取 futureppo 通道的 key；空则回退环境变量。"""
    key = ""
    if _PROVIDERS_FILE.exists():
        try:
            raw = json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
            items = raw.get("providers", raw) if isinstance(raw, dict) else raw
            for entry in items:
                if str(entry.get("name", "")).strip() == "futureppo":
                    key = str(entry.get("api_key", "") or "").strip()
                    break
        except (OSError, json.JSONDecodeError) as e:
            # 不裸吞异常：读文件/解析失败时明确提示，再走环境变量兜底
            print(f"读取 scripts/providers.json 失败: {e}")
    if not key:
        key = (
            os.environ.get("FUTUREPPO_API_KEY")
            or os.environ.get("PROVIDER_FUTUREPPO")
            or ""
        ).strip()
    return key


def main() -> None:
    key = _load_futureppo_key()
    if not key:
        print("未配置 futureppo 密钥。请在 scripts/providers.json 里填 futureppo 通道的")
        print("api_key（先 copy scripts/providers.example.json scripts/providers.json），")
        print("或设置环境变量 FUTUREPPO_API_KEY。")
        sys.exit(1)

    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            timeout=15,
        )
        print(f"futureppo Key: HTTP {r.status_code}")
        if r.status_code == 200:
            print(f"  OK: {r.json()['choices'][0]['message']['content'][:50]}")
        else:
            print(f"  Error: {r.text[:150]}")
    except Exception as e:
        print(f"futureppo Key: ERROR {e}")


if __name__ == "__main__":
    main()

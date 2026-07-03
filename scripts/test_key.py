"""Test all LLM provider keys."""
import requests, yaml, sys

with open("conf.yaml", "r", encoding="utf-8") as f:
    conf = yaml.safe_load(f)

roles = {
    "llm": "Main Chat",
    "llm_tool": "Tool",
    "llm_memory": "Memory",
    "llm_emotion": "Emotion",
    "llm_vision": "Vision",
    "llm_postprocess": "Postprocess",
    "llm_detect": "Detect",
    "llm_background": "Background",
}

print("=== Testing All LLM Keys ===\n")
all_ok = True

for role_key, role_name in roles.items():
    c = conf.get(role_key, {})
    key = c.get("api_key", "")
    url = c.get("base_url", "")
    model = c.get("model", "")

    if not key or not url or not model:
        print(f"  SKIP  {role_name:12s} - not configured")
        continue

    try:
        r = requests.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            timeout=15,
        )
        if r.status_code == 200:
            print(f"  OK    {role_name:12s} ({c.get('provider', '?')} / {model})")
        else:
            print(f"  FAIL  {role_name:12s} HTTP {r.status_code}: {r.text[:80]}")
            all_ok = False
    except Exception as e:
        print(f"  ERR   {role_name:12s} {str(e)[:60]}")
        all_ok = False

print(f"\n{'ALL KEYS OK!' if all_ok else 'SOME KEYS FAILED!'}")

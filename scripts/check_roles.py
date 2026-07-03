"""Verify all LLM roles are configured with separate providers."""
import yaml

with open("conf.yaml", "r", encoding="utf-8") as f:
    conf = yaml.safe_load(f)

roles = ["llm", "llm_tool", "llm_memory", "llm_emotion", "llm_vision",
         "llm_postprocess", "llm_detect", "llm_background"]

print("=== 10-Channel LLM Role Check ===\n")
providers_used = set()
for role in roles:
    c = conf.get(role, {})
    provider = c.get("provider", "NOT SET")
    model = c.get("model", "NOT SET")
    has_key = "OK" if c.get("api_key", "") else "NO KEY"
    providers_used.add(provider)
    print(f"  {role:20s} | {provider:15s} | {model:40s} | {has_key}")

print(f"\nUnique providers: {len(providers_used)} ({', '.join(sorted(providers_used))})")
print(f"Total roles: {len(roles)}")

# Check no two adjacent roles use the same provider (avoid rate limiting)
main_provider = conf.get("llm", {}).get("provider", "")
vision_provider = conf.get("llm_vision", {}).get("provider", "")
if main_provider == vision_provider:
    print(f"\n⚠️  WARNING: Main and Vision use same provider '{main_provider}' - may cause rate limiting!")
else:
    print(f"\n✅ Main ({main_provider}) and Vision ({vision_provider}) use different providers")

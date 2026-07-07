"""单文件测试 DeepSeek 连接。运行:
    uv run python .test_deepseek.py
不会入 db,只看 LLM 通不通 + 回什么。
"""

import os
import sys

# 手动从 .env 读取 key(避免对其他模块的污染)
def _load_dotenv(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if k and k not in os.environ:
                os.environ[k] = v

_load_dotenv(".env")

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "?"

print(f"DEEPSEEK_API_KEY 发现: {masked} (长度 {len(api_key)})")
print()

# 强制用 deepseek(而不是 mock)
os.environ["HEALTHOS_LLM"] = "deepseek"

try:
    from healthos.llm.client import chat, LLMRequest
except ImportError as e:
    print(f"✗ import error: {e}")
    sys.exit(1)

req = LLMRequest(
    system="你是 HealthOS 的测试接口。用一句话回复\"pong\"。",
    user="ping",
    max_tokens=50,
    thinking_disabled=True,
)

print("→ 发起 ping...")
try:
    resp = chat(req)
except Exception as e:
    print(f"✗ LLM call failed: {type(e).__name__}: {e}")
    sys.exit(2)

print(f"✓ LLM 返回:")
print(f"  text: {resp.text!r}")
print(f"  tool_calls: {resp.tool_calls}")
print(f"  raw keys: {list(resp.raw.keys()) if resp.raw else 'n/a'}")
print()
if resp.text and len(resp.text.strip()) > 0:
    print("✓ HealthOS — DeepSeek connected OK")
else:
    print("⚠ connected but empty response — model name may be wrong")

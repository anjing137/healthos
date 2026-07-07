"""LLM client 抽象 + 实现。

Provider:
- "mock" — 不发 HTTP 请求,返回 deterministic stub(本地测/没 key 时用)
- "deepseek" — deepseek-v4-pro,OpenAI-compatible base_url = https://api.deepseek.com

设计:
- 客户端只发 chat completions + tools
- 不依赖 OpenAI SDK(避免 pip 多装一包);用 urllib,跨平台最小依赖
- key 不在代码里,只通过 env var DEEPSEEK_API_KEY 读
- 一段 LLM → tool_calls 的回退路径,无 function call 时回 JSON.parse(类似 Anthropic prompt-based)
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEEPSEEK_BASE = "https://api.deepseek.com"


def _load_dotenv(path: Path | None = None) -> None:
    """最小 .env loader:把 key=value 填到 os.environ(不覆盖已存在)。

    - 不依赖 python-dotenv
    - 自动找项目根的 .env 或 CWD/.env
    - 不 print / 不 log key 内容
    """
    if path is None:
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
    if path is None or not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


# 模块导入即自动 load;后续 _make_client() 会读 os.environ[key]
_load_dotenv()


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMRequest:
    system: str
    user: str
    tools: list[dict] = field(default_factory=list)
    model: str = "deepseek-v4-pro"
    max_tokens: int = 1024
    thinking_disabled: bool = True


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Optional[dict] = None


class LLMError(RuntimeError):
    pass


# ─── Provider interface ─────────────────────────────────────────


def _make_client(provider: str | None = None):
    """根据 env var 选 provider。优先级:DEEPSEEK_API_KEY 存在 → deepseek,否则 mock。"""
    provider = provider or _auto_pick_provider()
    if provider == "deepseek":
        from .deepseek import DeepSeekClient  # late import 避免循环
        return DeepSeekClient()
    elif provider == "mock":
        from .mock import MockClient
        return MockClient()
    raise LLMError(f"unknown llm provider: {provider}")


def _auto_pick_provider() -> str:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    return "mock"


# ─── Public API ────────────────────────────────────────────────


def chat(req: LLMRequest, provider: Optional[str] = None) -> LLMResponse:
    return _make_client(provider).chat(req)


def chat_json(req: LLMRequest, provider: Optional[str] = None) -> dict:
    """调一次,要求 LLM 返 JSON。新手友好(不需要 tool 定义)。"""
    # 把 system 末尾加 JSON 提示
    req.system = (req.system + "\n\nReturn strict JSON only. No prose.").strip()
    resp = chat(req, provider=provider)
    txt = resp.text.strip()
    # 去掉 markdown 包装
    if txt.startswith("```"):
        # first newline 后到 末尾前 ``` 前
        first_nl = txt.find("\n")
        if first_nl >= 0:
            txt = txt[first_nl + 1 :]
        if txt.endswith("```"):
            txt = txt[:-3]
        txt = txt.strip()
    return json.loads(txt)

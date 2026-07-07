"""DeepSeek client — 用 urllib 单文件实现,不引入额外依赖。

Base URL: https://api.deepseek.com
Auth:     Authorization: Bearer $DEEPSEEK_API_KEY
Model:    默认 deepseek-v4-pro(可改)

Function calling / tool use:
- messages 里加 tools 字段(OpenAI-format)
- response.choices[0].message 可能有 tool_calls 数组
- tool_calls[i].function.name + arguments(JSON string)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .client import LLMError, LLMRequest, LLMResponse, ToolCall


DEEPSEEK_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


def _request_json(url: str, headers: dict, body: dict, timeout: float = 30.0) -> dict:
    """发 POST request, expecting JSON 响应。失败 raise LLMError。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:500]
        raise LLMError(f"HTTP {e.code}: {body_text}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"network: {e}") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"non-JSON response: {e}") from e


class DeepSeekClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY env var not set")
        self.api_key = api_key

    def chat(self, req: LLMRequest) -> LLMResponse:
        # 构造 OpenAI-compatible messages
        # 支持两种调用方式:
        #   1. simple(system+user)— 缺省,messages=[system, user]
        #   2. multi-turn(req.messages 是 list[dict])— agent.py 在 tool 路径下注入
        if getattr(req, "messages", None):
            messages = list(req.messages)
            if req.system and not any(m.get("role") == "system" for m in messages[:1]):
                messages.insert(0, {"role": "system", "content": req.system})
        else:
            messages = []
            if req.system:
                messages.append({"role": "system", "content": req.system})
            messages.append({"role": "user", "content": req.user})

        body: dict[str, Any] = {
            "model": req.model or self.model,
            "max_tokens": req.max_tokens,
            "messages": messages,
            "stream": False,
        }
        if req.tools:
            body["tools"] = req.tools
        if req.thinking_disabled:
            # DeepSeek V4 模型支持 disabling thinking via parameter
            # 具体参数名以官方文档为准;先放 thinking={"type": "disabled"}
            body["thinking"] = {"type": "disabled"}

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = _request_json(url, headers, body)

        # 解析响应
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            txt = msg.get("content", "") or ""
            raw_tcs = msg.get("tool_calls", []) or []
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {data}") from e

        tcs: list[ToolCall] = []
        for tc in raw_tcs:
            try:
                fn = tc["function"]
                args_raw = fn.get("arguments", "{}") or "{}"
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            tcs.append(ToolCall(name=fn["name"], arguments=args))

        return LLMResponse(text=txt, tool_calls=tcs, raw=data)

"""Mock LLM client — 用于本地测试,真 key 没填时它兜底。"""
from __future__ import annotations

from .client import LLMRequest, LLMResponse


class MockClient:
    """返回一个最小可预测的响应:把 system+user 拼回 echo,工具调用返回 []。

    真实测试场景下,我们要让 mock 至少能说明"对话发生过"。
    测试 7 天趋势这种 case 时,可以注入 return_text 覆盖默认。
    """

    last_response_text: str | None = None  # 单实例共享,test hook

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    def chat(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        # 用 system 提示关键字决定输出(用于测试)
        if MockClient.last_response_text is not None:
            txt = MockClient.last_response_text
        else:
            # 默认:返回 "ok" + system 头一行(测试能识别这是 mock 的)
            head = req.system.split("\n", 1)[0]
            txt = f"[MOCK] ack system='{head[:40]}' user_len={len(req.user)}"
        return LLMResponse(text=txt, tool_calls=[], raw={"mock": True})

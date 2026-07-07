"""读 PROFILE.md 并拼成给 LLM 的 prompt 片段。

设计:
- Markdown + YAML frontmatter 是 source of truth(可 git,可手工编辑)
- 读整个文件拼到 CHAT_SYSTEM 末尾
- 文件不存在时返回空字符串(向后兼容)
- 读失败时也不抛 — chat 仍能跑,只是少了一层画像
"""

from __future__ import annotations

from pathlib import Path

_PROFILE_PATH = Path(__file__).resolve().parent / "PROFILE.md"
# 公开别名,便于测试 monkeypatch + 文档引用
PROFILE_PATH = _PROFILE_PATH


def load_profile_text(path: Path | None = None) -> str:
    """读 PROFILE.md 全文(frontmatter + 段落),失败返空串。"""
    p = path or _PROFILE_PATH
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def profile_block_for_prompt(path: Path | None = None) -> str:
    """返回拼到 CHAT_SYSTEM 末尾的 `<user_profile>...</user_profile>` 块。

    空 profile → 返空串,不污染 prompt。
    """
    text = load_profile_text(path)
    if not text.strip():
        return ""
    return (
        "\n\n<user_profile>\n"
        "以下是 HealthOS 用户的长时不变量 — 你每次对话都应参考,但不要复述整块:\n\n"
        f"{text.strip()}\n"
        "</user_profile>\n"
        "\n注意:profile 里的数字是常驻参考,如果你要回答的是某天具体数字,用 read_today 拿实时数据为准。"
    )
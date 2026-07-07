"""REPL 启动时打印的 ASCII 抬头。

手写,不加任何依赖。设计:简单、双行、单色调。
"""

from __future__ import annotations


BANNER = """\
  __        __ ___  ___  ___     ___  ___
 /\\ \\      / /|_ _|/ _ \\/ _ \\   / __|/ _ \\
|  \\ \\ /\\ / /  | | | | | | | |  |__ \\ | | |
| |\\ \\ V  V /   | | | | | | | |  __| | | | |
| | \\ |\\ | |   | | | |_| | |_| | |__/| |_| |
| |  \\ | | ||| |___|\\___/ \\___/  \\___|\\___/
 \\_|  \\_| |___|

  Personal Health Agent  ·  Recorder-first  ·  Local SQLite
"""


WELCOME_BANNER = """\
================================================
  HealthOS REPL · Personal Health Agent
================================================

  Your data stays on this machine.
  Type Chinese freely; section headers (早餐 午餐 ... 运动 睡眠 膝盖)
  auto-route to record. Questions (?) auto-route to chat.
""".rstrip()

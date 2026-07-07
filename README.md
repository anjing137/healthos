# HealthOS — 命令行使用手册

个人健康数据记录 + 减脂追踪 + AI 对话系统(DeepSeek V4)。

## 安装

```bash
# uv 自 0.4 起会自动加载项目根 .env(无需手动 export)
cp .env.example .env
# 编辑 .env,把 DEEPSEEK_API_KEY=xxx 填上
```

接入真 DeepSeek:

```
DEEPSEEK_API_KEY=sk-xxx
HEALTHOS_LLM=auto          # auto = 有 key 自动启用,无 key 回 mock
DEEPSEEK_MODEL=deepseek-v4-pro
```

不填 `HEALTHOS_LLM`,自动 auto:有 key → DeepSeek,无 → mock(本地 mock echo)。

## 真 LLM 连接测试

**一次性测试**,不入 db:

```bash
uv run python .test_deepseek.py
```

期望输出末行 `✓ HealthOS — DeepSeek connected OK`。如果 fail,常见原因:
- 网络/Tailscale 拦截 Tailnet 段(参考 memory `tailscale-shadowrocket-conflict.md`)
- API key 错或过期(去 https://platform.deepseek.com/api-keys 重生)
- 模型名错(默认 `deepseek-v4-pro`,可换)

测试成功后再用真 LLM 命令。

## 命令一览

### 数字记录(走 Python parse,无 LLM)

| 命令 | 作用 |
|---|---|
| `healthos record "<日记>" --date YYYY-MM-DD` | 录入一日。早午晚 + 训练/睡眠/膝盖 段头清楚 |
| `healthos learn "<回答>" --date ...` | 回答 system 提示的 open_question |
| `healthos today --date ...` | 当日统计(早/午/晚 + 摄入合计 + 待回答) |
| `healthos deficit --date ...` | 当日减脂报告(BMR / TDEE / 缺口 / 估算掉秤) |

数据例子:

```bash
uv run healthos record "
早餐：一碗豆浆，一个包子
午餐：一碗米饭配冬瓜炒肉的菜
晚餐：日本豆腐，小酥肉炖白菜，花生米，莲藕，毛血旺，还喝了白酒
" --date 2026-07-06

uv run healthos learn "3两白酒 / 日本豆腐 100g / 莲藕 50g / 花生米 30g / 小酥肉约50g 白菜70g" --date 2026-07-06

uv run healthos today --date 2026-07-06
uv run healthos deficit --date 2026-07-06
```

### LLM 对话与总结(走 DeepSeek V4)

| 命令 | 作用 |
|---|---|
| `healthos chat "<msg>"` | 跟 LLM 自由对话,原文不入 db |
| `healthos commit "<summary>" --date ...` | 写当日总结,LLM 不参与 parse,走 record(lenient=True) |
| `healthos verify --date ...` | LLM 核查当日数据,把可疑点写入 verify_pending |
| `healthos verify-show --date ...` | 列待答核查项 |
| `healthos verify-answer <id> "<text>"` | 回答 verify 中的某个问题 |

例子:

```bash
uv run healthos chat "今天吃得多了,我该怎么办"
uv run healthos commit "今天吃得很简单,早餐一杯豆浆,午餐鸡胸肉 150g" --date 2026-07-10
uv run healthos verify --date 2026-07-06
uv run healthos verify-show
uv run healthos verify-answer 1 "午餐 957 因为我吃了大米 + 冬瓜 + 肉 300g"
```

## 数据流图

```
                      ┌─────────────────────┐
   chat <msg>  ──────►│  LLM (DeepSeek V4) │  对话原文只留内存
                      │   tools:           │
                      │   read_today()     │  只读 SQLite
                      │   get_recent_trend  │
                      │   get_open_questions│
                      └─────────┬───────────┘
                                │
                                │  user 写总结
                                ▼
   commit <summary> ─────► record(lenient=True)
                          → 写正表(meal/workout/sleep/knee/weight)
                          → 写 chat_log(speaker='user_summary')
                                │
                                │  user 触发 verify
                                ▼
   verify ─────► LLM 拉数据 + 找疑点 → 写 verify_pending
                                  │
                                  │  user 答
                                  ▼
   verify-answer N "..." ─► verify_pending.status='resolved'
                            → 写 chat_log(speaker='verify_answer')
```

## LLM 边界(硬约束)

LLM 永远不会:

- ❌ 直接写 SQLite(只有 Python 函数能写)
- ❌ 给出"建议怎么吃/怎么练"(record_only:true 仍然成立)
- ❌ 把 LLM 自身的判断入"建议"表(它可能在 chat_log 里以 `verify_answer` 出现,但不会影响任何数字表)

LLM 只会:

- ✅ 读 tool 数据(read_today/get_recent_trend/get_open_questions)
- ✅ 回答"数据是什么样",不做判断
- ✅ 找出可疑事实,**问 user** 来确认

## 不在 db 的设计

| 数据 | 入 db? | 为什么 |
|---|---|---|
| meal/workout/sleep/knee/weight 行 | ✓ | 你自己考量的真实情况 |
| `record` warnings(unknown 食物) | ✓ | 后续 learn 时方便回查 |
| open_question resolved_grams + answer_text | ✓ | 审计 / 你改默认后能拉日志 |
| `commit` 的 user summary | ✓ | `chat_log.speaker='user_summary'` |
| `verify-answer` 答的文本 | ✓ | `chat_log.speaker='verify_answer'` |
| `chat` 的对话原文 | ❌ | 你说 — 避免污染 db / 节省空间 |
| LLM 临时思考/thinking 块 | ❌ | 默认关闭 |
| LLM 给的建议(纯自然语言) | ❌ | 不写库;只在 verify 时写 question |

## 一些历史

- 项目根:`/Volumes/program/healthos`(外接盘)
- 真实数据:`data/healthos.db`(gitignored)
- InBody(7/2):已入库,体重 100.6 / BMR 1890 / 蛋白目标 130g
- 体重 7/7:100.9 kg 已写入 weight 表

## 排错

| 现象 | 可能原因 | 解决 |
|---|---|---|
| `verify` 报 "LLM failed: Expecting value" | 用了 mock client(LLM 没真接通) | 设 `HEALTHOS_LLM=deepseek` 或填 key |
| `chat` 返回 `[MOCK] ack ...` | 未填 key 或 key 没生效 | 检查 .env + `cp .env.example .env` |
| `deficit` 显示 0 kcal / 蛋白 | 当日没录饮食;或 open_question 全 closed 真值,需 → `today` 触发真值重算 | `healthos record ...` + `healthos today` |
| `commit` 后系统不知 commit 文本里的数字 | 整段被当一件,parser 无法拆 | 等 D7 引入 LLM-input fallback;或用段头标 (早餐：...) |

## 后续路线

- D6 — Markdown 报告落到 `data/exports/YYYY-MM-DD.md`
- D7 — week 汇总 + 真读 `config/health_rules.yaml` + 多个 unit tests
- 长期 — Coach Adam 规则版(基于 if/else,不上 LLM 决策)

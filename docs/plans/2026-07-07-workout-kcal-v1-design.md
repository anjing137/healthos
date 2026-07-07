# v1 — Workout KCal 估算设计

> 状态: 设计冻结，进入实现
> 日期: 2026-07-07
> 范围: workout 表的 MET 估算 + 强度修正；不开任何外部依赖

## 1. 背景与目标

当前 `/today` 显示的 `+471` 缺口只算了"吃进来 vs TDEE"，
没有扣掉任何运动消耗。`workout` 表只存 `duration_min`，
没有 `sport` / `intensity` / `kcal_burned` 字段。

**v1 目标**：把"运动消耗"纳入 deficit 报告，
估算精度放第二、可审计放第一。

**显式不追求的目标**：

- ±5% 精度（不开 Apple Health 不可达）
- 子运动细分（5v5 vs 投篮不分，篮球康复动作不分）
- 心率 / ZONE / RPE

## 2. 范围与非范围

### IN

- 12 项运动的 MET 字典（ACSM Compendium of Physical Activities 2011）
- 输入识别：中文级别词「轻 / 中 / 高」标强度
- 公式：`kcal = MET × weight_kg × hours`
- 未知运动走 open_question
- 老数据保留：sport/intensity/kcal_burned 全 NULL，deficit 把它们当 0
- 单条手动校准：`healthos fix-workout <id> --kcal=X`

### OUT

- Apple Health / 外部数据源
- 子运动细分
- 心率 / ZONE / RPE
- 自适应体重回溯
- 多运动同日合并

## 3. MET 字典

来源：Ainsworth et al., *Compendium of Physical Activities* (2011)。
`healthos/nutrition/activities.py` 提供 `MET_TABLE` + 中文→key 映射。

| key | 中文别名 | light | moderate | high |
|---|---|---|---|---|
| walking_slow | 散步、走走、遛弯 | 2.5 | 3.0 | 3.5 |
| walking_brisk | 快走 | 4.0 | 4.5 | 5.5 |
| jogging | 慢跑、轻松跑 | 6.0 | 7.0 | 8.5 |
| running | 跑步、跑步 | 8.0 | 9.8 | 11.5 |
| basketball | 打篮球、篮球 | 5.0 | 6.5 | 8.0 |
| jump_rope | 跳绳 | 8.0 | 12.0 | 13.0 |
| elliptical | 椭圆机 | 4.5 | 5.0 | 6.5 |
| swimming_freestyle | 游泳、自由泳 | 5.0 | 6.0 | 9.0 |
| rowing | 划船机、划船 | 4.5 | 7.0 | 8.5 |
| weight_training_general | 撸铁、器械、力量训练、举铁 | 3.5 | 5.0 | 6.0 |
| yoga_stretching | 瑜伽、拉伸 | 2.0 | 3.0 | 4.0 |
| knee_rehab¹ | 膝盖训练、康复训练、静蹲、臀桥、死虫、直腿抬高、侧卧抬腿 | 2.5 | 3.5 | 4.5 |

¹ `knee_rehab` 不是 ACSM 标准条目，靠墙静蹲 3.5 + 拉伸 2.5 加权估算，
confidence 上限锁 0.7（其它 ACSM 项目上限 0.95）。

## 4. 数据 schema — migration v006

`healthos/db/migrations/v006_workout_kcal.sql`：
幂等（init() 多跑无害）。

```sql
ALTER TABLE workout ADD COLUMN sport TEXT;
ALTER TABLE workout ADD COLUMN intensity TEXT;
ALTER TABLE workout ADD COLUMN kcal_burned REAL;
ALTER TABLE workout ADD COLUMN kcal_method TEXT;
ALTER TABLE workout ADD COLUMN confidence REAL;
```

老数据全部保留为 NULL。

## 5. 估算器 — `healthos/record/workout_kcal.py`

```python
def estimate_kcal(sport, intensity, minutes, weight_kg=None) -> tuple[float, float]:
    """返回 (kcal, confidence)。"""
```

**confidence 规则**：

| 情况 | confidence |
|---|---|
| ACSM 运动 + 已知强度 + 已知体重 | 0.85 |
| ACSM 运动 + 未指定强度（默认 moderate） | 0.70 |
| ACSM 运动 + 未知体重（fallback 70） | 0.65 |
| 未知运动 + 手填 kcal | 1.00 |
| `knee_rehab`（合成） | 上限 0.70 |

**未知 sport** → `raise UnknownSport(sport)`，
让 caller 写 open_question + workout 行 `kcal_method='pending'`。

## 6. 输入识别 — `_extract_workout_meta`

不动 `parser.py`，做后置 hook。
位置：`healthos/record/write.py:_write_workout` 前。

```python
def _extract_workout_meta(raw_text) -> WorkoutMeta:
    # sport: 12 项正则之一
    # intensity: 轻/中/高 + 同义词（轻松/中等/高强/热血/拼命）
    # 缺失时返回 intensity='moderate' 默认
```

## 7. 写入流程

`record/write.py` 的 workout 分支：

```
parser 解析 sec.name="workout"
  ↓
_extract_workout_meta(sec.raw) → sport/intensity/minutes
  ↓
if sport is None:
    _record_open_question(...)
    INSERT workout(sport=NULL, intensity=intensity, kcal_burned=NULL,
                   kcal_method='pending', confidence=NULL, ...)
else:
    kcal, conf = estimate_kcal(sport, intensity, minutes, weight_kg=...)
    INSERT workout(sport=sport, intensity=intensity, kcal_burned=kcal,
                   kcal_method='MET', confidence=conf, ...)
```

## 8. `/today` 输出

deficit 公式改一行：

```
intake_kcal = SUM(meal.kcals)
workout_kcal = SUM(workout.kcal_burned)
deficit = intake_kcal - (tdee + workout_kcal)
```

输出新增一行：

```
运动消耗: ~X kcal (N 条已估 / M 条待补)
```

未补的运动：sport 未识别 → 报"X 条 sport 未知"。

## 9. 单条手动校准

新 CLI：`healthos fix-workout <id> --kcal=580`

```sql
UPDATE workout
SET kcal_burned = ?, kcal_method = 'manual', confidence = 1.0
WHERE id = ?
```

满足 CLAUDE.md 第 5 条：不批量改 `resolved_grams=0`。

## 10. 测试

`tests/test_workout_kcal.py`（10 个 case）：

1. 篮球 50min moderate / 100kg → kcal ≈ 542, conf 0.85
2. 跑步 60min high / 100kg → kcal ≈ 1150, conf 0.85
3. 强度缺失 → conf 0.70
4. weight_kg=None → 用 70, conf 0.65
5. sport='frisbee' → 抛 UnknownSport
6. knee_rehab → conf ≤ 0.7
7. 写库后新字段被正确填
8. unknown sport 写 open_question
9. 老 workout 行 sport 仍 NULL
10. deficit 计算排除 NULL kcal_burned

`uv run pytest` 必须 36 + 10 全绿。

## 11. 回滚

| 情况 | 操作 |
|---|---|
| 测试挂了 | revert .py 文件，db 不用动 |
| `/today` 多余一行 | 删一行 print |
| 整块不要 | 删 activities.py / workout_kcal.py |
| 数据库想回滚 | ALTER 列保留，但没人填——不算错 |

CLAUDE.md 第 3 条满足：只加 migration，没手动改表。

## 12. 验证门

- [ ] 36 老测试 + 10 新测试全绿
- [ ] 真实日记：`/today` 显示数字合理
- [ ] 未知运动：open_question + 提示
- [ ] DB 里看 workout 表新列填上了

## 13. 不变量

- **绝不静默改写老数据**（CLAUDE.md 第 5 条精神）
- **改 db 只加 migration**（CLAUDE.md 第 3 条）
- **meal.kcals 占位 vs 真值** 原则**不适用**于 workout
  ——workout 没有"占位 vs 真值"分层，估算即写入
- **LLM 不直接写 SQLite**（CLAUDE.md 第 6 条）— record_ai 走同一 hook，自动接力

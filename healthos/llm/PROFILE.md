---
# HealthOS LLM 常驻画像 — 每次启动注入 CHAT_SYSTEM
# 这是 source of truth,LLM 启动时直接读这个文件
# 修改方式:手动编辑 + git commit(可审计、可回滚)
user_id: anjing137
updated: 2026-07-08
weight_kg: 100.9
weight_date: 2026-07-07
bmr_kcal: 1890
bmr_source: inbody-2026-07-02
protein_target_g: 130
activity_factor: 1.55
obsidian_vault: /Volumes/video/obsidian/health/Daily/
---

# 当前身体状态

- 当前体重 **100.9 kg**(2026-07-07 称的)
- InBody(2026-07-02):100.6 kg / BMR=1890 kcal / 体脂 30% / 骨骼肌 40.3 kg
- 蛋白目标 **130 g/d**(InBody 推荐)
- 膝盖经常 3~5/10 紧绷,记入日记时**关注膝盖状态变化**

# 风格偏好

- **中文回答,简短** — 1~3 句话,不写长文
- 数字精确:整数 kcal,克数保留 1 位小数
- 报告路径:`/Volumes/video/obsidian/health/Daily/`
- 时区:Asia/Shanghai(本地)

# 训练习惯

- 主要:散步 / 篮球 / 膝盖康复(不计入强度训练)
- 膝盖康复动作包含:靠墙静蹲 / 臀桥 / 死虫 / 直腿抬高 / 侧卧抬腿(合并记 `knee_rehab`)
- MET 表见 `healthos/nutrition/activities.py`

# 数据现状

- 完整天数:目前 1 天(2026-07-06)— 14 天滑动平均不可用
- 真实数据开始于:2026-07-06
- 减脂期:**是**(BMI ≈ 30)
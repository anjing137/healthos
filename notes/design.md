# HealthOS — 设计笔记

## 为什么是 SQLite + Python 而不是 Notion / 飞书 / 现有健康 App

- 数据是你自己拥有的,不在别人服务器
- 一个 .db 文件可以 git 备份、可以一键 export CSV
- Python 是你熟悉的,Agent 写出来的代码也能读、也能改
- 不依赖任何外部服务的可用性

## 为什么不接 Apple Health / Garmin / 小米 / Obsidian

- 接进来 = 第一版的 schema 必须迁就上游的 schema
- 上游 schema 变了(它们一定变), 我们这边跟着崩
- 第一版的策略:**先把"我**自己打字**"这个最不可控的入口做对,
  再去谈接外部 API 那个相对简单的接口**

## 为什么"先记录员、后教练"

第一版 Agent 的输出 = **"今天记到这;今日累加;近期趋势"**。
所有数字都是事实陈述。**没有**"建议你少吃""你这个平台做错了"之类的话。

理由:
- Agent 建议在数据<14d 时基本是噪音(你看到了也是白看,看多了还会怀疑自己)
- 等数据能看到趋势,再开第二版 Coach Adam
- 那个时候的口径是"我的膝盖 x 14 天紧绷 × 平均 **y** 分;按你设的阈值 **z** 分已经是边界;
  **但建议动作只有你能定**,所以我只说事实"

## 目录约定

```
parser/        输入 → 结构化
nutrition/     食物估大卡 + 单位换算
record/        写库
query/         读库,只读
report/        Markdown 渲染
rules/         规则(读 YAML)
db/            schema + migrations
data/          SQLite 文件
config/        YAML 文件
tests/         pytest
notes/         设计笔记(本目录)
```

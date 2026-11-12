---
name: agent-workflows-ab
description: Run or refine the user's A+B personal agent workflows: a morning calendar-and-watchlist brief and a conversational decision ledger. Use only when those workflows are requested.
metadata:
  short-description: A+B morning brief and decision ledger
---

# A+B 个人工作流

两条工作流分开执行，共用时区、原子状态写入、运行日志和反馈记忆约定。

## A. 晨间双流

- 只有在调度器和日历服务真实连通后，才按用户配置的 `Asia/Shanghai` 时间运行。
- 读取今天与明天的日历，并独立读取配置好的公开监控名单。日历只读，不修改、邀请、取消或挪动事件。
- 日历或监控源读取失败时明确报告不可读，不能伪装成空日程或无变化；一段失败不能吞掉另一段结果。
- 监控源首次成功读取时建立基线并说明，不称为“无变化”。
- 发送前对每项变化重新核对来源和上次快照。送达确认前不推进快照、书签或去重状态。
- 不猜测日历提供商、名单、作息或收件人；缺配置时明确请求补充。

## B. 决策快照

- 用户要求记录判断或预测时，收集 `decision`、`basis`、`expectation` 和 `confidence`（0-100）。缺必填项就追问，不推测把握度。
- 预期必须能用可观察结果核对；含糊时先请用户改成可验证表述。
- 用户未指定时，`revisit_days` 默认 90。记录保存在配置的本地账本中，不发送到外部服务。
- 回访时展示原判断、依据、预期和把握度，不替用户判定实际结果。只有用户提供实际结果后才关闭记录。
- 明确保留部分命中；定义和样本不足时，不下校准或偏差结论。
- 需要执行确定性的新增、到期查询、送达确认、关闭或汇总时，使用 `scripts/decision_ledger.py`；具体命令见参考文档。

## 共用规则

- 每次运行都重读配置，配置与可变状态分开存放。
- 用稳定 ID 与内容指纹去重；状态以临时文件加原子替换保存，运行日志只追加。
- 区分 `ok`、`partial`、`degraded`、`failed`、`skipped`；读取失败不等于成功的空结果。
- 每次运行限制耗时、抓取数和模型调用数；记录截断及部分失败。
- 发送成功但状态提交前崩溃可能造成重复。下游支持时使用幂等键；否则说明是“至少一次”投递，不承诺绝不重复。
- 只从用户明确纠正、明确评价价值、以及已关闭的 B 记录中学习。不能从沉默或一次性要求推断长期偏好。
- 学习记忆与只读配置分开；配置、名单、阈值、收件人和调度变更均先提出建议，用户确认后再改。
- 状态结构见 [state-and-failure.md](references/state-and-failure.md)，反馈学习边界见 [feedback-learning.md](references/feedback-learning.md)。

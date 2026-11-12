# A+B 状态与失败处理

以下只是初始结构，不代表已连接真实服务。除非用户明确指定其他位置，个人日历内容和决策账本都保存在本地。

## B 决策账本

以 `assets/decision_ledger.template.json` 为初始结构。新增、到期查询、送达确认、关闭与汇总由 `scripts/decision_ledger.py` 执行。每条记录：

- `id` 稳定且唯一；`created_at` 是带时区的 ISO 8601 时间，`revisit_date` 是 ISO 日期。
- `decision`、`basis`、`expectation` 必须由用户确认；`confidence` 为 0 到 100 的整数。
- `status` 在回访卡确认送达后从 `pending` 转为 `awaiting_actual`；用户提供实际结果后转为 `closed`。自动提醒前必须另行配置无回复策略。
- 用户报告结果前，`actual` 和 `delta` 保持空值。明确保存实际结果和命中类型。

工具会先校验，再写同目录临时文件并原子替换账本。同一判断使用内容指纹去重；修改预期或把握度会被视为不同记录，避免覆盖历史。工具按单写入者设计；若对话端和定时端可能同时写，部署前需加锁或乐观版本检查。

命令示例（将账本路径换成你选择的本地位置）：

```powershell
python scripts/decision_ledger.py --ledger state/decision_ledger.json add --decision "某项判断" --basis "判断依据" --expectation "2027-02-01 前出现可核验的结果" --confidence 70
python scripts/decision_ledger.py --ledger state/decision_ledger.json due --limit 3
python scripts/decision_ledger.py --ledger state/decision_ledger.json mark-sent d-YYYYMMDD-001
python scripts/decision_ledger.py --ledger state/decision_ledger.json close d-YYYYMMDD-001 --actual "实际结果" --outcome partial --note "部分完成"
python scripts/decision_ledger.py --ledger state/decision_ledger.json profile
```

`mark-sent` 仅应在下游确认回访卡送达后执行。CLI 不会自行推送、创建定时任务或猜测实际结果。

## A 配置与投递

以 `assets/morning_dual.config.template.json` 为占位模板。用户提供后再填真实日历、工作时段和监控源；每次运行受配置预算限制。

逐源记录最近一次成功读取位置和内容哈希。抓取或发送失败时不推进书签。源不支持可靠游标时用完整快照哈希比较，并说明其限制，不假装存在书签。

发送前先保存待投递消息或 outbox 记录；下游确认收到后再标记送达并提交状态。下游不支持幂等键时，崩溃后可能重复发送；不要为了宣称“恰好一次”而丢消息。

## 失败状态

- `ok`：所有配置输入均可读且结果已送达。
- `partial`：一个或多个非关键源不可读；明确列出或计数。
- `degraded`：主要数据源不可用，但仍有部分有效输出。
- `failed`：无法交付可靠结果；指出失败阶段。
- `skipped`：配置规则要求本次不运行；仅在明确跳过时静默。

记录运行时间、状态、耗时、数据源/抓取数、条目数、截断和失败说明。不要记录密钥或不必要的完整私人日历内容。

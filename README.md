# A+B 个人工作流 MVP

[![CI](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml)

一个本地优先、只读、可审计的个人 Agent 工作流原型。A 负责晨间信息汇总，B 负责决策记录与回访。

## 特性

- 本地 `.ics` 日历读取，不执行日历写操作；
- 公开 RSS/Atom 源差分，首次运行建立基线；
- 决策账本的校验、去重、原子写入和到期状态迁移；
- 失败与不可读状态显式输出；
- 无外部依赖的标准库实现；
- GitHub Actions 仅运行测试，不接触个人状态。

这是一个可本地运行的只读原型：

- A：读取可选的 `.ics` 日历文件，并比较公开 RSS/Atom 源的新增条目；
- B：用本地 JSON 账本记录判断、查询到期回访、确认送达、录入实际结果并做有限统计；
- 反馈学习：只记录用户明确纠正、明确偏好和已确认回访结果，不自动改配置。

## 运行 A

```powershell
python scripts/morning_dual.py `
  --config assets/morning_dual.config.json `
  --state state/morning_dual.json `
  --output output/morning/2026-11-12.md
```

`calendar_ics` 默认是 `null`，因此日历段会明确显示“日程不可读”，不会伪装成空日程。将它改成你提供的本地 `.ics` 路径后，脚本只读解析。

首次成功读取监控源只建立基线；后续运行才报告新增条目。状态使用原子替换，运行记录追加到 `runs.jsonl`。

## 运行 B

参见 `references/state-and-failure.md` 中的命令示例。账本工具不会自行发送消息，也不会猜测实际结果。

## 当前边界

此 MVP 不包含账号登录、日历写操作、第三方消息推送或云端数据库。它可以作为 GitHub 仓库上传，但部署到 GitHub Actions 或其他调度器前，仍需配置秘密、定时器、推送通道和并发锁。

## 安全边界

不要把 `state/`、`output/`、`.ics` 文件、访问令牌或私人决策内容提交到仓库。发布前请替换 README 中的 `OWNER/REPOSITORY` 占位符。

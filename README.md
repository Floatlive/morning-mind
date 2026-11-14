# Morning Mind

[![CI](https://github.com/Floatlive/morning-mind/actions/workflows/ci.yml/badge.svg)](https://github.com/Floatlive/morning-mind/actions/workflows/ci.yml)

**Morning Mind** 是一个本地优先的个人 Agent 工作流原型，把每天的信息整理和长期决策复盘放在一个小型、可检查的工具集中。它包含两部分：

- **A · 晨间双流**：读取今明两天的本地日历 `.ics` 文件，并检查公开 RSS/Atom 订阅源相对上次成功读取的新增条目；可只读展示决策回访。
- **B · 决策快照**：把判断、依据、可验证预期和把握度写入本地 JSON 账本；到期后由用户确认回访是否送达，再记录实际结果。

这个仓库目前提供的是**本地可运行的 MVP**，不是托管式服务，也不是连接了真实日历、邮件或消息账户的在线智能体。它不需要 API key；Windows 需要 `tzdata` 提供 IANA 时区数据。

`v0.2.0` 增加今明日程、订阅源发布日期与完整基线、到期决策回访；旧版最多十条的订阅源快照会按重合条目谨慎迁移。简报仍只生成本地文件，没有实时文字推送、自动重试或送达确认。

## 目录

- [设计原则](#设计原则)
- [要求](#要求)
- [快速开始](#快速开始)
- [A：晨间双流](#a晨间双流)
- [B：决策快照](#b决策快照)
- [数据与状态](#数据与状态)
- [测试](#测试)
- [安全与隐私](#安全与隐私)
- [能力矩阵与集成前置条件](#能力矩阵与集成前置条件)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

## 设计原则

- **本地优先**：日历文件、决策账本、抓取快照和简报都由你在本机指定路径保存。
- **默认只读**：A 只读取日历和公开订阅源；不会编辑、邀请、取消日历事件，也不会自动发送消息。
- **失败要可见**：缺少日历配置时明确显示“日程不可读”；抓取失败会记录源名称和错误类型，不把失败伪装成“今天没有变化”。
- **首次读取先建基线**：一个订阅源第一次成功读取只保存快照；从下一次成功读取开始，才比较新增条目。
- **状态变化要显式**：B 的记录只有在确认回访卡已送达后才从 `pending` 进入 `awaiting_actual`；收到用户提供的实际结果后才关闭。
- **不把小样本说成学习结论**：关闭记录少于 10 条时只报告数量；达到 10 条后也只给描述性统计，不声称因果关系或稳定校准。

## 要求

- Python 3.11 或更高版本
- Git（克隆仓库时需要）
- Windows 需安装时区数据：运行 `python -m pip install tzdata`；macOS/Linux 通常使用系统时区数据库。

克隆仓库：

```bash
git clone https://github.com/Floatlive/morning-mind.git
cd morning-mind
```

Windows PowerShell 同样可以使用以上 Git 命令。以下命令也适用于 macOS/Linux；Windows 用户可将路径分隔符写成 `/`，或按 PowerShell 语法调整换行。

## 快速开始

先从公开模板创建本机配置；PowerShell：

```powershell
Copy-Item assets/morning_dual.config.template.json assets/morning_dual.config.json
```

macOS/Linux 可用 `cp`。按需修改本机配置后运行 A（PowerShell 可把下面命令写成单行）：

```bash
python scripts/morning_dual.py \
  --config assets/morning_dual.config.json \
  --state state/morning_dual.json \
  --output output/morning/today.md
```

第一次运行会建立订阅源基线，并在终端输出 JSON 运行摘要；Markdown 简报写入 `output/morning/today.md`。状态、实际配置与输出均被 `.gitignore` 排除。

首次运行会显示“日程不可读：未配置可读的 `.ics` 文件”，因为示例配置中的 `calendar_ics` 默认为 `null`。这是刻意的：没有日历输入时，工具不会假装日历为空。

## A：晨间双流

### 配置

编辑 `assets/morning_dual.config.json`：

```json
{
  "timezone": "Asia/Shanghai",
  "calendar_ics": null,
  "timeout_seconds": 20,
  "watchlist": [
    {
      "name": "OpenAI News",
      "url": "https://openai.com/news/rss.xml"
    }
  ]
}
```

- `timezone`：IANA 时区名称，例如 `Asia/Shanghai` 或 `America/New_York`。未通过 `--date` 指定日期时，它决定“当天”的计算以及状态/运行日志的时间戳。
- `calendar_ics`：可选的本地 `.ics` 文件路径；留空时会明确报告日历不可读。
- `timeout_seconds`：单个订阅源请求的超时秒数。当前示例配置使用 20 秒。
- `watchlist`：需要读取的 RSS/Atom 源列表，每项填写名称和 URL。公开模板包含 OpenAI News、OpenAI Python SDK Releases、Anthropic Python SDK Releases 三个示例。

仓库中的 `assets/morning_dual.config.template.json` 只含公开示例源。首次运行状态保存在 `--state` 指定的 JSON 文件中；后续运行沿用它来区分新增条目。使用不同状态文件会重新建立基线。

### 指定日期并保存输出

```bash
python scripts/morning_dual.py \
  --config assets/morning_dual.config.json \
  --state state/morning_dual.json \
  --output output/morning/2026-11-13.md \
  --date 2026-11-13
```

`--date` 用于选择简报日期，格式为 `YYYY-MM-DD`；省略时使用配置时区的当天。脚本会：

1. 解析今天和明天的日历事件（如果配置了可读的 `.ics` 文件）。支持单次事件，以及每日/每周的基础重复事件。
2. 读取配置中的 RSS/Atom 源，并与上次成功保存的条目快照比较。
3. 生成简报，列出新增条目的来源、发布日期和链接，并可展示到期决策回访。
4. 更新状态 JSON，并在状态目录追加一行 `runs.jsonl` 运行记录。

日历或账本不可读时，简报会标明。日历不可读使运行状态成为 `partial`（监控源全部失败时为 `degraded`）。这不代表脚本已连接日历服务或完成投递。

通过 `--ledger state/decision_ledger.json` 可只读加入到期决策回访。晨报展示最多三条，含判断、依据、可验证预期、把握度、状态和回访截止日期；`pending` 与 `awaiting_actual` 都会列出。账本缺失或损坏会标记不可读，晨报不会修改账本或确认送达。

### 日历与订阅源的边界

`.ics` 解析支持单次事件，以及 `FREQ=DAILY`、`FREQ=WEEKLY` 的基础 `RRULE`，并识别 `INTERVAL`、`COUNT`、`UNTIL` 与 `EXDATE`。事件时间会转到配置时区。不支持月度/年度规则、按工作日筛选、重复实例修改或完整 RFC 5545 兼容性；遇到不支持的规则会报告日历不可读。

项目使用 RSS/Atom 源当前返回的条目做快照比较，并为条目生成基于来源、标题和链接的指纹。它不是可靠的事件流或永久归档：如果源移除了旧条目、重排内容或只保留有限数量的近期条目，快照可能无法代表期间发生的全部更新。抓取或解析失败会被报告；不要把失败解释为“没有新内容”。

## B：决策快照

账本 CLI 使用本地 JSON 文件保存判断。所有命令都以 `--ledger` 指定同一账本路径。

### 新增一条决策

```bash
python scripts/decision_ledger.py --ledger state/decision_ledger.json add \
  --decision "某项判断将在指定日期前得到结果" \
  --basis "支持这一判断的可复核依据" \
  --expectation "2027-02-01 前公开发布版本 2.0，可由更新日志核验" \
  --confidence 70 \
  --domain "产品" \
  --revisit-days 90
```

必填信息：

- `--decision`：要复盘的判断。
- `--basis`：作出判断时的依据。
- `--expectation`：可观察、可核验的预期，建议包含结果和截止日期。过短或明显含糊的预期会被拒绝。
- `--confidence`：0 到 100 的整数。

可选信息：`--domain` 用于分类，默认是“未分类”；`--revisit-days` 设置多少天后到期，默认 90 天。可用 `--today YYYY-MM-DD` 覆盖记录日期，便于测试或补录。

同一内容重复新增会根据内容指纹去重；改变判断、依据、预期、把握度、分类或回访间隔，会形成不同记录。

### 查询和关闭到期记录

```bash
# 列出已到期且未关闭的记录（pending 或 awaiting_actual）
python scripts/decision_ledger.py --ledger state/decision_ledger.json due --limit 3

# 只有在你确认已经收到/送达回访卡后，才执行状态迁移
python scripts/decision_ledger.py --ledger state/decision_ledger.json mark-sent d-20261113-001

# 用户提供实际结果后关闭记录；outcome 可选 hit、partial、miss
python scripts/decision_ledger.py --ledger state/decision_ledger.json close d-20261113-001 \
  --actual "实际发生的、可核验的结果" \
  --outcome partial \
  --note "与原预期相比的说明"

# 查看描述性汇总
python scripts/decision_ledger.py --ledger state/decision_ledger.json profile
```

记录状态流转：

```text
add -> pending -> mark-sent -> awaiting_actual -> close -> closed
```

`due` 是只读查询，不会自动发送提醒。`mark-sent` 只是由使用者确认送达后的本地状态更新，不连接邮件、微信或其他消息通道。`close` 需要人工提供实际结果和 `hit`、`partial`、`miss` 分类；脚本不会自行判断结果。

## 数据与状态

默认输出和本地状态可按下表管理：

| 路径 | 内容 | 是否应提交到 Git |
| --- | --- | --- |
| `assets/morning_dual.config.json` | 订阅源和可选日历路径配置 | 否；只提交模板 |
| `state/morning_dual.json` | 上次成功读取的订阅源快照 | 否 |
| `state/decision_ledger.json` | 私人判断、依据、预期与实际结果 | 否 |
| `output/morning/*.md` | 生成的晨间简报 | 通常否 |
| `state/runs.jsonl` | 每次运行的摘要日志 | 否 |

默认 `.gitignore` 排除了 `state/`、`output/` 和实际配置。账本与状态文件会通过同目录临时文件写入，再以原子替换更新；若账本 JSON 损坏或结构不符合预期，CLI 会停止并保留原文件，不会尝试猜测修复。

工具按**单写入者**场景设计。如果你通过多个并行定时任务或多个进程同时更新同一状态文件，应先加入文件锁或版本检查机制。

更多状态语义和失败处理约定见 [`references/state-and-failure.md`](references/state-and-failure.md)；Agent Skill 行为约定见 [`SKILL.md`](SKILL.md)。

## 测试

仅使用 Python 标准库运行测试：

```bash
python -m unittest discover -s tests -v
```

测试覆盖订阅条目解析、空源与旧快照、今明日程与基础重复、时区转换、决策回访只读联动、到期查询、含糊预期拒绝和损坏账本保护。GitHub Actions 会在推送和 Pull Request 时运行同一测试命令。

## 安全与隐私

- 不要提交真实日历 `.ics`、私人决策账本、生成的简报、令牌或凭据。
- 使用日历导出文件前，请确认你了解其包含的个人信息，并只在可信本地环境保存。
- A 会访问你在配置中填写的公开 RSS/Atom URL；不要把未知或不可信 URL 当作可信服务。
- 当前脚本不会把本地账本、日历或简报上传到远端，也不会向第三方发送消息。
- 仓库公开后，配置文件和提交历史均可能被他人看到；公开前检查文件和历史记录。

发现安全问题时，请参阅 [`SECURITY.md`](SECURITY.md) 的报告说明，不要在公开 Issue 中粘贴敏感数据。

## 能力矩阵与集成前置条件

本项目未集成云日历 OAuth/API、邮件/微信/短信投递、GitHub 自动建任务、云数据库、LLM 推理或自动改配置。这些能力不是遗漏的“开关”：它们分别需要真实账户、最小权限授权、密钥保护、第三方写入规则、数据留存策略或并发设计。

完整逐项清单、当前可用替代方案和启用前置条件见 [`docs/capability-matrix.md`](docs/capability-matrix.md)。仓库中的“工作流”指可独立运行的本地脚本与状态约定，并不表示外部服务已连接。

## 参与贡献

欢迎提交 Issue 或 Pull Request。贡献前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，并运行测试。

## 许可证

本项目使用 MIT License，详见 [`LICENSE`](LICENSE)。

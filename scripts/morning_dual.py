#!/usr/bin/env python3
"""Run the local A segment (ICS calendar) and B segment (public feed diff).

This MVP is intentionally read-only: it writes local snapshots and a Markdown
brief, but never edits calendars or sends network messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from decision_ledger import due_items, load_ledger


try:
    TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def configured_timezone(config: dict):
    name = config.get("timezone", "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        if name == "Asia/Shanghai":
            return TZ
        raise ValueError(f"无效或不可用时区：{name}；请安装 tzdata") from exc


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, timeout: int) -> bytes:
    req = Request(url, headers={"User-Agent": "agent-workflows-ab/0.1 (read-only)"})
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def text(node: ElementTree.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext()) if node is not None else "").strip()


def feed_items(raw: bytes, source: str, limit: int | None = None) -> list[dict]:
    root = ElementTree.fromstring(raw)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    output = []
    for item in items[:limit]:
        title = text(item.find("title")) or text(item.find("{http://www.w3.org/2005/Atom}title"))
        link_node = item.find("link")
        if link_node is None:
            link_node = item.find("{http://www.w3.org/2005/Atom}link")
        link = (link_node.attrib.get("href") if link_node is not None else None) or text(link_node)
        published = text(item.find("pubDate")) or text(item.find("{http://www.w3.org/2005/Atom}updated")) or text(item.find("{http://www.w3.org/2005/Atom}published"))
        if title and link:
            fingerprint = hashlib.sha256(f"{source}|{title}|{link}".encode("utf-8")).hexdigest()
            output.append({"title": title, "link": link, "published": published, "fingerprint": fingerprint, "source": source})
    return output


def unfold_ics(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line.rstrip("\r\n"))
    return result


def parse_ics_start(key: str, value: str, local_tz=TZ) -> tuple[str, str | None]:
    if "VALUE=DATE" in key or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date().isoformat(), None
    if not re.fullmatch(r"\d{8}T\d{6}Z?", value):
        raise ValueError("DTSTART 格式无效")
    moment = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    tzid = next((part.split("=", 1)[1].strip('"') for part in key.split(";")[1:] if part.startswith("TZID=")), None)
    if value.endswith("Z"):
        origin = timezone.utc
    elif tzid is None:
        origin = local_tz
    elif tzid == "UTC":
        origin = timezone.utc
    else:
        try:
            origin = ZoneInfo(tzid)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"无法解析时区 {tzid}") from exc
    local = moment.replace(tzinfo=origin).astimezone(local_tz)
    return local.date().isoformat(), local.strftime("%H:%M")


def event_occurs_on(event: dict, target: date) -> bool:
    start = date.fromisoformat(event["date"])
    if target < start or target in event.get("exdates", set()):
        return False
    rule = event.get("rrule")
    if not rule:
        return target == start
    values = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    if set(values) - {"FREQ", "INTERVAL", "COUNT", "UNTIL"}:
        raise ValueError("包含暂不支持的重复规则")
    interval = int(values.get("INTERVAL", "1"))
    if interval < 1:
        raise ValueError("重复间隔无效")
    elapsed = (target - start).days
    if values.get("FREQ") == "DAILY":
        period = interval
    elif values.get("FREQ") == "WEEKLY":
        period = 7 * interval
    else:
        raise ValueError("包含暂不支持的重复规则")
    if elapsed % period:
        return False
    count = int(values.get("COUNT", "0"))
    if count < 0:
        raise ValueError("重复次数无效")
    until = values.get("UNTIL")
    if until:
        until_day = datetime.strptime(until[:8], "%Y%m%d").date()
        if target > until_day:
            return False
    return count == 0 or elapsed // period < count


def calendar_summary(path: Path | None, target: date, label: str = "今日", local_tz=TZ) -> tuple[str, list[dict]]:
    if path is None or not path.exists():
        return "日程不可读：未配置可读的 .ics 文件", []
    events = []
    active = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"日程不可读：{type(exc).__name__}", []
    if "BEGIN:VCALENDAR" not in lines or "END:VCALENDAR" not in lines:
        return "日程不可读：.ics 格式无效", []
    for line in unfold_ics(lines):
        if line == "BEGIN:VEVENT":
            active = {}
        elif line == "END:VEVENT" and active is not None:
            if "date" not in active:
                return "日程不可读：事件缺少有效 DTSTART", []
            try:
                if event_occurs_on(active, target):
                    events.append(active)
            except ValueError as exc:
                return f"日程不可读：{exc}", []
            active = None
        elif active is not None:
            full_key, _, value = line.partition(":")
            key = full_key.split(";", 1)[0]
            if key == "SUMMARY":
                active["summary"] = value
            elif key == "DTSTART":
                try:
                    active["date"], active["time"] = parse_ics_start(full_key, value, local_tz)
                except ValueError as exc:
                    return f"日程不可读：{exc}", []
            elif key == "RRULE":
                active["rrule"] = value
            elif key == "EXDATE":
                try:
                    active.setdefault("exdates", set()).update(
                        date.fromisoformat(parse_ics_start(full_key, part, local_tz)[0]) for part in value.split(",")
                    )
                except ValueError as exc:
                    return f"日程不可读：{exc}", []
            elif key in ("RDATE", "RECURRENCE-ID"):
                return "日程不可读：包含暂不支持的重复事件", []
    if not events:
        return f"日程可读：{label}没有解析到事件", []
    return f"日程可读：{label}解析到 {len(events)} 个事件", events


def published_day(value: str, local_tz=TZ) -> str:
    if not value:
        return "日期未提供"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return "日期未提供"
    if moment.tzinfo is not None:
        moment = moment.astimezone(local_tz)
    return moment.date().isoformat()


def item_line(item: dict, local_tz=TZ) -> str:
    return f"- {item['source']}：{item['title']}（{published_day(item['published'], local_tz)}；{item['link']}）"


def event_line(event: dict) -> str:
    time_label = f"{event['time']} " if event.get("time") else ""
    return f"- {time_label}{event.get('summary', '未命名事件')}"


def decision_revisits(path: Path | None, today: date) -> tuple[str, list[dict]]:
    if path is None:
        return "未配置", []
    try:
        if not path.is_file():
            raise ValueError("文件不存在或不是普通文件")
        return "可读", due_items(load_ledger(path), today)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "不可读", []


def run(config_path: Path, state_path: Path, output_path: Path, today: date, ledger_path: Path | None = None) -> dict:
    config = load_json(config_path, {})
    local_tz = configured_timezone(config)
    state = load_json(state_path, {"version": 1, "feeds": {}})
    state.setdefault("feeds", {})
    calendar_path = Path(config["calendar_ics"]) if config.get("calendar_ics") else None
    calendar_note, events = calendar_summary(calendar_path, today, local_tz=local_tz)
    tomorrow_note, tomorrow_events = calendar_summary(calendar_path, today + timedelta(days=1), "明日", local_tz=local_tz)
    ledger_status, revisits = decision_revisits(ledger_path, today)
    changed = []
    baselines = []
    baseline_count = 0
    migration_notes = []
    failures = []
    fetch_count = 0
    for feed in config.get("watchlist", []):
        url = feed.get("url")
        name = feed.get("name") or url or "未命名源"
        if not url:
            failures.append(f"{name}: URL 未配置")
            continue
        try:
            fetch_count += 1
            items = feed_items(fetch(url, int(config.get("timeout_seconds", 20))), name)
            if not items:
                raise ValueError("源中没有可解析的条目")
            current_hash = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            previous = state["feeds"].get(name)
            if previous is None:
                baselines.append((name, items[:3]))
                baseline_count += 1
            elif not previous.get("snapshot_complete"):
                old = {item["fingerprint"] for item in previous.get("items", [])}
                first_overlap = next((index for index, item in enumerate(items) if item["fingerprint"] in old), None)
                if first_overlap is None:
                    migration_notes.append(f"{name}：旧基线已扩展；没有重合条目，新增无法判断")
                else:
                    changed.extend(item for item in items[:first_overlap] if item["fingerprint"] not in old)
                    migration_notes.append(f"{name}：旧基线已扩展；较早历史未计入新增")
            elif previous.get("content_hash") != current_hash:
                old = {item["fingerprint"] for item in previous.get("items", [])}
                changed.extend([item for item in items if item["fingerprint"] not in old])
            state["feeds"][name] = {
                "url": url, "content_hash": current_hash, "items": items,
                "last_read": datetime.now(local_tz).isoformat(timespec="seconds"), "snapshot_complete": True,
            }
        except Exception as exc:  # network and malformed-feed failures are reported, not hidden
            failures.append(f"{name}: 不可读（{type(exc).__name__}）")
    calendar_failed = calendar_note.startswith("日程不可读")
    status = "ok" if not failures and not calendar_failed and not migration_notes else (
        "degraded" if failures and len(failures) == len(config.get("watchlist", [])) else "partial"
    )
    if ledger_status == "不可读" and status == "ok":
        status = "partial"
    if changed:
        changed_note = f"检测到 {len(changed)} 条新变化"
    elif failures:
        changed_note = "今日外部变化不可完整判断"
    elif migration_notes:
        changed_note = "旧基线已扩展，较早历史未比较"
    elif baseline_count:
        changed_note = f"已为 {baseline_count} 个源建立基线，后续运行开始对比"
    else:
        changed_note = "今日无变化"
    lines = [f"# 晨间双流 · {today.isoformat()}", "", "## 一、今明日程", "### 今天", f"- {calendar_note}"]
    for event in events:
        lines.append(event_line(event))
    lines += ["", "### 明天", f"- {tomorrow_note}"]
    for event in tomorrow_events:
        lines.append(event_line(event))
    lines += ["", "## 二、监控变化", f"- {changed_note}"]
    for name, items in baselines:
        lines.append(f"- {name}：已建立基线，最近 {len(items)} 条：")
        lines.extend(item_line(item, local_tz) for item in items)
    for item in changed:
        lines.append(item_line(item, local_tz))
    lines.extend(f"- {note}" for note in migration_notes)
    if failures:
        lines.append(f"- {len(failures)} 个监控源不可读，变化不可完整判断：")
        lines.extend(f"- {failure}" for failure in failures)
    calendar_status = "未配置" if calendar_path is None else ("不可读" if calendar_failed else "可读")
    lines += [
        "", "## 三、读取状态",
        f"- 监控源：{len(config.get('watchlist', [])) - len(failures)}/{len(config.get('watchlist', []))} 成功读取",
        f"- 日历：{calendar_status}",
        f"- 决策账本：{ledger_status}",
        f"- 运行状态：{status}",
    ]
    lines += ["", "## 四、决策回访"]
    if ledger_status == "不可读":
        lines.append("- 决策账本不可读；到期回访无法判断")
    elif ledger_status == "未配置":
        lines.append("- 决策账本未配置")
    elif not revisits:
        lines.append("- 当前无到期回访")
    else:
        lines.append(f"- 到期未关闭：{len(revisits)} 条；展示前 {min(3, len(revisits))} 条")
        for item in revisits[:3]:
            lines.append(
                f"- {item['id']}｜回访截止：{item['revisit_date']}｜状态：{item['status']}｜"
                f"分类：{item.get('domain', '未分类')}｜把握度：{item['confidence']}%"
            )
            lines.append(f"  原判断：{item['decision']}；依据：{item['basis']}；可验证预期：{item['expectation']}")
        if len(revisits) > 3:
            lines.append(f"- 另有 {len(revisits) - 3} 条到期记录未展示")
    lines += ["", "> 这是只读 MVP；未配置日历时，A 段会明确标记不可读。"]
    atomic_text(output_path, "\n".join(lines) + "\n")
    atomic_json(state_path, state)
    run_log = state_path.parent / "runs.jsonl"
    with run_log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"run_at": datetime.now(local_tz).isoformat(timespec="seconds"), "status": status, "fetches": fetch_count, "items": len(changed), "failures": failures}, ensure_ascii=False) + "\n")
    return {"status": status, "fetches": fetch_count, "changes": len(changed), "baseline_sources": baseline_count, "failures": failures, "decision_ledger_status": ledger_status, "due_decisions": len(revisits), "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, help="只读决策账本 JSON 路径")
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    try:
        config = load_json(args.config, {})
        result = run(args.config, args.state, args.output, args.date or datetime.now(configured_timezone(config)).date(), args.ledger)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

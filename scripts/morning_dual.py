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
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")


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


def feed_items(raw: bytes, source: str, limit: int = 10) -> list[dict]:
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


def calendar_summary(path: Path | None, target: date) -> tuple[str, list[dict]]:
    if path is None or not path.exists():
        return "日程不可读：未配置可读的 .ics 文件", []
    events = []
    active = None
    for line in unfold_ics(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line == "BEGIN:VEVENT":
            active = {}
        elif line == "END:VEVENT" and active is not None:
            if active.get("date") == target.isoformat():
                events.append(active)
            active = None
        elif active is not None:
            key, _, value = line.partition(":")
            key = key.split(";", 1)[0]
            if key == "SUMMARY":
                active["summary"] = value
            elif key == "DTSTART":
                match = re.search(r"(\d{4})(\d{2})(\d{2})", value)
                if match:
                    active["date"] = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if not events:
        return "日程可读：今日没有解析到事件", []
    return f"日程可读：今日解析到 {len(events)} 个事件", events


def run(config_path: Path, state_path: Path, output_path: Path, today: date) -> dict:
    config = load_json(config_path, {})
    state = load_json(state_path, {"version": 1, "feeds": {}})
    state.setdefault("feeds", {})
    notes = []
    calendar_path = Path(config["calendar_ics"]) if config.get("calendar_ics") else None
    calendar_note, events = calendar_summary(calendar_path, today)
    notes.append(calendar_note)
    changed = []
    baseline_count = 0
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
            current_hash = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            previous = state["feeds"].get(name)
            if previous is None:
                notes.append(f"{name}：已建立基线，明日开始对比")
                baseline_count += 1
            elif previous.get("content_hash") != current_hash:
                old = {item["fingerprint"] for item in previous.get("items", [])}
                changed.extend([item for item in items if item["fingerprint"] not in old])
            state["feeds"][name] = {"url": url, "content_hash": current_hash, "items": items, "last_read": datetime.now(TZ).isoformat(timespec="seconds")}
        except Exception as exc:  # network and malformed-feed failures are reported, not hidden
            failures.append(f"{name}: 不可读（{type(exc).__name__}）")
    status = "ok" if not failures else ("degraded" if len(failures) == len(config.get("watchlist", [])) else "partial")
    if failures:
        notes.append(f"{len(failures)} 个监控源不可读：" + "；".join(failures))
    if baseline_count and not changed and not failures:
        changed_note = f"首次运行：已为 {baseline_count} 个源建立基线，明日开始对比"
    elif not changed and not failures:
        changed_note = "今日无变化"
    elif changed:
        changed_note = f"检测到 {len(changed)} 条新变化"
    else:
        changed_note = "今日外部变化不可完整判断"
    lines = [f"# 晨间双流 · {today.isoformat()}", "", "## 一、今天的时间", f"- {calendar_note}"]
    for event in events[:10]:
        lines.append(f"- {event.get('summary', '未命名事件')}")
    lines += ["", "## 二、外部变化", f"- {changed_note}"]
    for item in changed[:10]:
        lines.append(f"- {item['source']}：{item['title']}（{item['link']}）")
    lines += ["", "> 这是只读 MVP；未配置日历时，A 段会明确标记不可读。"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    atomic_json(state_path, state)
    run_log = output_path.parent / "runs.jsonl"
    with run_log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"run_at": datetime.now(TZ).isoformat(timespec="seconds"), "status": status, "fetches": fetch_count, "items": len(changed), "failures": failures}, ensure_ascii=False) + "\n")
    return {"status": status, "fetches": fetch_count, "changes": len(changed), "baseline_sources": baseline_count, "failures": failures, "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(TZ).date())
    args = parser.parse_args()
    try:
        result = run(args.config, args.state, args.output, args.date)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

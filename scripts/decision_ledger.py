#!/usr/bin/env python3
"""Small local CLI for the A+B decision snapshot ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any


def now_local() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def today_local() -> date:
    return now_local().date()


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"账本不可读，未修改原文件：{exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
        raise ValueError("账本结构无效，预期 version=1 且 items 为数组；原文件未修改")
    return data


def atomic_save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def parse_day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def normalize_expectation(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    vague = ("会更好", "应该能成", "会成功", "有进展", "效果不错", "大概率可以")
    if len(text) < 8 or any(term in text for term in vague):
        raise ValueError("expectation 太含糊；请改成带可观察结果和截止时间的预期")
    return text


def due_items(data: dict[str, Any], on: date) -> list[dict[str, Any]]:
    return sorted(
        (item for item in data["items"] if item.get("status") == "pending" and parse_day(item["revisit_date"], "revisit_date") <= on),
        key=lambda item: (item["revisit_date"], item["id"]),
    )


def add_item(args: argparse.Namespace) -> dict[str, Any]:
    if not 0 <= args.confidence <= 100:
        raise ValueError("confidence 必须在 0 到 100 之间")
    if args.revisit_days < 1:
        raise ValueError("revisit-days 必须大于 0")
    if not args.decision.strip() or not args.basis.strip():
        raise ValueError("decision 和 basis 不能为空")
    expectation = normalize_expectation(args.expectation)
    data = load_ledger(args.ledger)
    fingerprint_data = json.dumps(
        [args.decision.strip(), args.basis.strip(), expectation, args.confidence, args.domain.strip() or "未分类", args.revisit_days],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(fingerprint_data.encode("utf-8")).hexdigest()
    existing = next((item for item in data["items"] if item.get("fingerprint") == fingerprint), None)
    if existing:
        return existing
    created = datetime.combine(args.today, now_local().timetz())
    day_prefix = args.today.strftime("%Y%m%d")
    used = {item.get("id", "") for item in data["items"]}
    number = 1
    while f"d-{day_prefix}-{number:03d}" in used:
        number += 1
    item = {
        "id": f"d-{day_prefix}-{number:03d}",
        "fingerprint": fingerprint,
        "created_at": created.isoformat(timespec="seconds"),
        "decision": args.decision.strip(),
        "basis": args.basis.strip(),
        "expectation": expectation,
        "confidence": args.confidence,
        "domain": args.domain.strip() or "未分类",
        "revisit_days": args.revisit_days,
        "revisit_date": (args.today + timedelta(days=args.revisit_days)).isoformat(),
        "status": "pending",
        "actual": None,
        "delta": None,
        "closed_at": None,
    }
    data["items"].append(item)
    atomic_save(args.ledger, data)
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="本地决策快照账本：增量状态迁移与安全原子写入")
    parser.add_argument("--ledger", type=Path, required=True, help="账本 JSON 文件路径")
    subs = parser.add_subparsers(dest="command", required=True)

    add = subs.add_parser("add", help="新增一条决策")
    add.add_argument("--decision", required=True)
    add.add_argument("--basis", required=True)
    add.add_argument("--expectation", required=True)
    add.add_argument("--confidence", type=int, required=True)
    add.add_argument("--domain", default="未分类")
    add.add_argument("--revisit-days", type=int, default=90)
    add.add_argument("--today", type=lambda s: parse_day(s, "today"), default=today_local())

    due = subs.add_parser("due", help="列出到期记录，不修改账本")
    due.add_argument("--on", type=lambda s: parse_day(s, "on"), default=today_local())
    due.add_argument("--limit", type=int, default=3)

    sent = subs.add_parser("mark-sent", help="确认回访卡已送达后，迁移到 awaiting_actual")
    sent.add_argument("ids", nargs="+", help="记录 ID")
    sent.add_argument("--on", type=lambda s: parse_day(s, "on"), default=today_local())

    close = subs.add_parser("close", help="录入用户提供的实际结果并关闭记录")
    close.add_argument("id")
    close.add_argument("--actual", required=True)
    close.add_argument("--outcome", choices=("hit", "partial", "miss"), required=True)
    close.add_argument("--note", default="")

    profile = subs.add_parser("profile", help="描述性统计；少于 10 条仅显示样本量")
    profile.add_argument("--on", type=lambda s: parse_day(s, "on"), default=today_local())

    args = parser.parse_args()
    try:
        if args.command == "add":
            item = add_item(args)
            print(json.dumps({"status": "ok", "item": item}, ensure_ascii=False, indent=2))
            return 0

        data = load_ledger(args.ledger)
        if args.command == "due":
            if args.limit < 1:
                raise ValueError("limit 必须大于 0")
            items = due_items(data, args.on)[: args.limit]
            print(json.dumps({"status": "ok", "count": len(items), "items": items}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "mark-sent":
            by_id = {item["id"]: item for item in data["items"]}
            if len(set(args.ids)) != len(args.ids):
                raise ValueError("请求中包含重复 ID")
            for item_id in args.ids:
                item = by_id.get(item_id)
                if item is None or item["status"] != "pending" or parse_day(item["revisit_date"], "revisit_date") > args.on:
                    raise ValueError(f"{item_id} 不存在、未到期或状态不是 pending；未修改账本")
            stamp = now_local().isoformat(timespec="seconds")
            for item_id in args.ids:
                by_id[item_id]["status"] = "awaiting_actual"
                by_id[item_id]["last_revisit_sent_at"] = stamp
            atomic_save(args.ledger, data)
            print(json.dumps({"status": "ok", "marked": args.ids}, ensure_ascii=False))
            return 0

        if args.command == "close":
            matches = [item for item in data["items"] if item.get("id") == args.id]
            if not matches:
                raise ValueError(f"找不到记录：{args.id}")
            item = matches[0]
            if item.get("status") != "awaiting_actual":
                raise ValueError("记录必须处于 awaiting_actual 才能关闭；未修改账本")
            item["actual"] = args.actual.strip()
            item["delta"] = {"outcome": args.outcome, "bias": None, "note": args.note.strip()}
            item["status"] = "closed"
            item["closed_at"] = now_local().isoformat(timespec="seconds")
            atomic_save(args.ledger, data)
            print(json.dumps({"status": "ok", "item": item}, ensure_ascii=False, indent=2))
            return 0

        closed = [item for item in data["items"] if item.get("status") == "closed"]
        counts = {label: sum(1 for item in closed if (item.get("delta") or {}).get("outcome") == label) for label in ("hit", "partial", "miss")}
        result: dict[str, Any] = {"status": "ok", "closed_count": len(closed), "outcomes": counts}
        if len(closed) < 10:
            result["message"] = "样本不足 10 条；仅报告数量，不推断偏差或校准。"
        else:
            scored = [item for item in closed if (item.get("delta") or {}).get("outcome") in ("hit", "miss")]
            result["binary_scored_count"] = len(scored)
            result["domains"] = {
                domain: {
                    "count": sum(1 for item in scored if item.get("domain", "未分类") == domain),
                    "hit_rate": round(
                        sum(1 for item in scored if item.get("domain", "未分类") == domain and item["delta"]["outcome"] == "hit")
                        / max(1, sum(1 for item in scored if item.get("domain", "未分类") == domain)),
                        3,
                    ),
                }
                for domain in sorted({item.get("domain", "未分类") for item in scored})
            }
            result["message"] = "描述性统计，不代表因果关系或稳定校准；partial 单独报告。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

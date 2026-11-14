import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "morning_dual.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("morning_dual", SCRIPT)
morning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(morning)


class MorningDualTests(unittest.TestCase):
    def test_brief_shows_recurrence_tomorrow_and_due_decision_without_changing_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar = root / "calendar.ics"
            calendar.write_text(
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261112T090000\n"
                "SUMMARY:Daily meeting\nRRULE:FREQ=DAILY\nEND:VEVENT\nEND:VCALENDAR\n",
                encoding="utf-8",
            )
            ledger_path = root / "ledger.json"
            ledger_path.write_text(json.dumps({"version": 1, "items": [{
                "id": "d-1", "status": "awaiting_actual", "revisit_date": "2026-11-12",
                "decision": "Ship feature", "basis": "Ready", "expectation": "Released by deadline",
                "confidence": 70, "domain": "Product",
            }]}), encoding="utf-8")
            before = ledger_path.read_bytes()
            config = root / "config.json"
            config.write_text(json.dumps({"timezone": "Asia/Shanghai", "calendar_ics": str(calendar), "watchlist": []}), encoding="utf-8")
            output = root / "brief.md"
            result = morning.run(config, root / "state.json", output, date(2026, 11, 14), ledger_path)
            brief = output.read_text(encoding="utf-8")
            self.assertEqual(result["due_decisions"], 1)
            self.assertEqual(brief.count("Daily meeting"), 2)
            self.assertIn("Ship feature", brief)
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_empty_feed_does_not_advance_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            state = root / "state.json"
            output = root / "brief.md"
            config.write_text(json.dumps({"timezone": "Asia/Shanghai", "calendar_ics": None, "watchlist": [{"name": "Feed", "url": "https://example.test/rss"}]}), encoding="utf-8")
            good = b'<rss><channel><item><title>Old</title><link>https://example.test/old</link></item></channel></rss>'
            with patch.object(morning, "fetch", side_effect=[good, b"<rss><channel/></rss>"]):
                morning.run(config, state, output, date(2026, 11, 14))
                before = json.loads(state.read_text(encoding="utf-8"))["feeds"]["Feed"]
                result = morning.run(config, state, output, date(2026, 11, 14))
            self.assertEqual(result["status"], "degraded")
            self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["feeds"]["Feed"], before)
            self.assertNotIn("今日无变化", output.read_text(encoding="utf-8"))

    def test_invalid_ledger_is_reported_without_hiding_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar = root / "calendar.ics"
            calendar.write_text(
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261114\n"
                "SUMMARY:Important meeting\nEND:VEVENT\nEND:VCALENDAR\n",
                encoding="utf-8",
            )
            config = root / "config.json"
            config.write_text(json.dumps({"timezone": "Asia/Shanghai", "calendar_ics": str(calendar), "watchlist": []}), encoding="utf-8")
            ledger_path = root / "ledger.json"
            ledger_path.write_text("{broken", encoding="utf-8")
            before = ledger_path.read_bytes()
            output = root / "brief.md"
            result = morning.run(config, root / "state.json", output, date(2026, 11, 14), ledger_path)
            self.assertEqual(result["status"], "partial")
            self.assertIn("Important meeting", output.read_text(encoding="utf-8"))
            self.assertIn("决策账本不可读", output.read_text(encoding="utf-8"))
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_legacy_snapshot_does_not_label_older_history_as_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(json.dumps({"calendar_ics": None, "watchlist": [
                {"name": "Feed", "url": "https://example.test/rss"}
            ]}), encoding="utf-8")
            state = root / "state.json"
            output = root / "brief.md"
            old_feed = b'<rss><channel><item><title>Recent</title><link>https://example.test/recent</link></item></channel></rss>'
            expanded = (b'<rss><channel><item><title>Recent</title><link>https://example.test/recent</link></item>'
                        b'<item><title>Older</title><link>https://example.test/older</link></item></channel></rss>')
            with patch.object(morning, "fetch", side_effect=[old_feed, expanded]):
                morning.run(config, state, output, date(2026, 11, 14))
                saved = json.loads(state.read_text(encoding="utf-8"))
                saved["feeds"]["Feed"].pop("snapshot_complete")
                state.write_text(json.dumps(saved), encoding="utf-8")
                result = morning.run(config, state, output, date(2026, 11, 15))
            self.assertEqual(result["changes"], 0)
            self.assertIn("旧基线已扩展", output.read_text(encoding="utf-8"))
            self.assertNotIn("Older（", output.read_text(encoding="utf-8"))

    def test_failed_output_replace_does_not_advance_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(json.dumps({"calendar_ics": None, "watchlist": []}), encoding="utf-8")
            state = root / "state.json"
            output = root / "brief.md"
            output.write_text("previous brief", encoding="utf-8")
            with patch.object(morning.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    morning.run(config, state, output, date(2026, 11, 14))
            self.assertEqual(output.read_text(encoding="utf-8"), "previous brief")
            self.assertFalse(state.exists())

    def test_feed_items_and_calendar_missing_are_explicit(self):
        raw = b'<rss><channel><item><title>Release 1</title><link>https://example.test/1</link></item></channel></rss>'
        items = morning.feed_items(raw, "Example")
        self.assertEqual(items[0]["title"], "Release 1")
        note, events = morning.calendar_summary(None, date(2026, 11, 12))
        self.assertIn("不可读", note)
        self.assertEqual(events, [])

    def test_first_run_builds_baseline_and_second_run_has_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            state = root / "state.json"
            output = root / "brief.md"
            config.write_text(json.dumps({"calendar_ics": None, "watchlist": []}), encoding="utf-8")
            first = morning.run(config, state, output, date(2026, 11, 12))
            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["baseline_sources"], 0)
            self.assertIn("不可读", output.read_text(encoding="utf-8"))
            second = morning.run(config, state, output, date(2026, 11, 12))
            self.assertEqual(second["changes"], 0)

    def test_calendar_expands_daily_and_weekly_recurrence(self):
        with tempfile.TemporaryDirectory() as tmp:
            calendar = Path(tmp) / "calendar.ics"
            calendar.write_text(
                """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261109
SUMMARY:每日站会
RRULE:FREQ=DAILY
EXDATE:20261112
END:VEVENT
BEGIN:VEVENT
DTSTART:20261102
SUMMARY:每周复盘
RRULE:FREQ=WEEKLY;INTERVAL=2
END:VEVENT
END:VCALENDAR
""",
                encoding="utf-8",
            )

            note, events = morning.calendar_summary(calendar, date(2026, 11, 16))

            self.assertIn("2 个事件", note)
            self.assertEqual([event["summary"] for event in events], ["每日站会", "每周复盘"])

    def test_calendar_honors_exdate_for_recurring_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            calendar = Path(tmp) / "calendar.ics"
            calendar.write_text(
                """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261109
SUMMARY:每日站会
RRULE:FREQ=DAILY
EXDATE:20261112
END:VEVENT
END:VCALENDAR
""",
                encoding="utf-8",
            )

            note, events = morning.calendar_summary(calendar, date(2026, 11, 12))

            self.assertIn("没有", note)
            self.assertEqual(events, [])

    def test_run_uses_timezone_from_configuration_for_state_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            state = root / "state.json"
            output = root / "brief.md"
            config.write_text(
                json.dumps(
                    {
                        "timezone": "America/New_York",
                        "calendar_ics": None,
                        "watchlist": [{"name": "Example", "url": "https://example.test/feed"}],
                    }
                ),
                encoding="utf-8",
            )
            original_fetch = morning.fetch
            morning.fetch = lambda url, timeout: b"<rss><channel><item><title>Release</title><link>https://example.test/1</link></item></channel></rss>"
            try:
                morning.run(config, state, output, date(2026, 11, 12))
            finally:
                morning.fetch = original_fetch

            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertTrue(saved["feeds"]["Example"]["last_read"].endswith("-05:00"))

    def test_utc_event_uses_configured_timezone_at_day_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar = root / "calendar.ics"
            calendar.write_text(
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261114T233000Z\n"
                "SUMMARY:Late UTC meeting\nEND:VEVENT\nEND:VCALENDAR\n",
                encoding="utf-8",
            )
            note, events = morning.calendar_summary(calendar, date(2026, 11, 14), local_tz=morning.ZoneInfo("America/New_York"))
            self.assertIn("1 个事件", note)
            self.assertEqual(events[0]["time"], "18:30")
            note, events = morning.calendar_summary(calendar, date(2026, 11, 15), local_tz=morning.ZoneInfo("Asia/Shanghai"))
            self.assertIn("1 个事件", note)
            self.assertEqual(events[0]["time"], "07:30")


if __name__ == "__main__":
    unittest.main()

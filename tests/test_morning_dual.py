import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "morning_dual.py"
spec = importlib.util.spec_from_file_location("morning_dual", SCRIPT)
morning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(morning)


class MorningDualTests(unittest.TestCase):
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
            self.assertEqual(first["status"], "ok")
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


if __name__ == "__main__":
    unittest.main()

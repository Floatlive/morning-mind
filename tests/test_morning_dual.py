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


if __name__ == "__main__":
    unittest.main()

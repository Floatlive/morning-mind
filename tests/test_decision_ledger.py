import importlib.util
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "decision_ledger.py"
spec = importlib.util.spec_from_file_location("decision_ledger", SCRIPT)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class DecisionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.json"

    def tearDown(self):
        self.temp.cleanup()

    def add(self, today=date(2026, 11, 12), revisit_days=1):
        return ledger.add_item(Namespace(
            ledger=self.path,
            confidence=70,
            revisit_days=revisit_days,
            decision="服务将在指定日期前发布正式版本",
            basis="近期公开版本号变化，且招聘页面出现相关岗位",
            expectation="2026-11-13 前公开发布正式版本，可由更新日志核验",
            domain="产品",
            today=today,
        ))

    def test_add_then_due_and_repeat_is_idempotent(self):
        first = self.add()
        self.assertEqual(first["id"], "d-20261112-001")
        self.assertEqual(len(ledger.due_items(ledger.load_ledger(self.path), date(2026, 11, 13))), 1)
        self.assertEqual(ledger.due_items(ledger.load_ledger(self.path), date(2026, 11, 12)), [])
        before = self.path.read_bytes()
        self.assertEqual(len(ledger.due_items(ledger.load_ledger(self.path), date(2026, 11, 13))), 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_day_ids_increment_without_overwrite(self):
        self.add()
        second = self.add()
        self.assertEqual(second["id"], "d-20261112-001")
        self.assertEqual(len(ledger.load_ledger(self.path)["items"]), 1)

    def test_distinct_same_day_record_gets_next_id(self):
        self.add()
        args = Namespace(ledger=self.path, confidence=60, revisit_days=30, decision="另一项独立判断需要验证", basis="依据另一组单独的公开数据作判断", expectation="2026-12-12 前发布版本 2.0，可查公开更新日志", domain="产品", today=date(2026, 11, 12))
        second = ledger.add_item(args)
        self.assertEqual(second["id"], "d-20261112-002")
        self.assertEqual(len(ledger.load_ledger(self.path)["items"]), 2)

    def test_vague_expectation_rejected_without_creating_ledger(self):
        args = Namespace(ledger=self.path, confidence=70, revisit_days=90, decision="做一个预测事项", basis="根据现有资料作出判断", expectation="应该能成", domain="产品", today=date(2026, 11, 12))
        with self.assertRaisesRegex(ValueError, "含糊"):
            ledger.add_item(args)
        self.assertFalse(self.path.exists())

    def test_corrupt_ledger_is_preserved(self):
        self.path.write_text("{broken", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            ledger.load_ledger(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_due_includes_awaiting_actual_without_closed_items(self):
        item = self.add()
        data = ledger.load_ledger(self.path)
        data["items"][0]["status"] = "awaiting_actual"
        self.assertEqual([entry["id"] for entry in ledger.due_items(data, date(2026, 12, 1))], [item["id"]])


if __name__ == "__main__":
    unittest.main()

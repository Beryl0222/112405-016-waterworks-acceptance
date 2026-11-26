"""命令行冒烟测试：录入、确认、签认、查询、撤回。"""
import json
import tempfile
import unittest
from pathlib import Path

from hecha.cli import main

ROOT = Path(__file__).parents[1]
CASE = ROOT / "data" / "case.json"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        self.tmp.close()
        self.journal = self.tmp.name

    def run_cli(self, *argv):
        return main(["--case", str(CASE), "--journal", self.journal, *argv])

    def test_query_runs(self):
        self.assertEqual(self.run_cli("查询"), 0)

    def test_ingest_and_signoff_flow(self):
        self.assertEqual(self.run_cli(
            "录入", "--编号", "WA-T3", "--种类", "pressure-test",
            "--原文", "八月六日J100两侧接口及B试压0.8MPa连续30分钟，无渗漏。"), 0)
        self.assertEqual(self.run_cli(
            "录入", "--编号", "WA-F4", "--种类", "flushing",
            "--原文", "八月六日L2A2每秒6升连续冲洗25分钟。"), 0)
        self.assertEqual(self.run_cli(
            "录入", "--编号", "WA-F5", "--种类", "flushing",
            "--原文", "八月六日B每秒6升连续冲洗25分钟。"), 0)
        self.assertEqual(self.run_cli("签认", "--段", "L2A2", "B"), 0)
        journal = json.loads(Path(self.journal).read_text(encoding="utf-8"))
        kinds = [e["type"] for e in journal["entries"]]
        self.assertEqual(kinds, ["record", "record", "record", "signoff"])

    def test_signoff_refused_when_unready(self):
        self.assertEqual(self.run_cli("签认", "--段", "L2A2"), 1)

    def test_confirm_rejected_without_source(self):
        self.assertEqual(self.run_cli(
            "确认", "--针对", "WA-F1", "--参数", "连续时长", "--值", "25"), 1)

    def test_withdraw_finds_dependents(self):
        self.assertEqual(self.run_cli("撤回", "--记录", "WA-R1"), 0)
        self.assertEqual(self.run_cli("查询", "--段", "L2A1"), 0)

    def test_duplicate_record_rejected(self):
        self.assertEqual(self.run_cli(
            "录入", "--编号", "WA-T1", "--种类", "flushing",
            "--原文", "重复编号。"), 1)


if __name__ == "__main__":
    unittest.main()

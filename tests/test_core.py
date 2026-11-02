# -*- coding: utf-8 -*-
"""core 层测试。运行：python3 -m unittest discover -s tests"""
import os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import money, report
from core.evidence import Claim, Evidence, EvidenceError
from core.gate import Gate
from core.store import Store
from core.text import clean, norm_key, redact, split_list, truncate
from helper import rm_tree


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="store_")
        self.s = Store(self.dir)

    def tearDown(self):
        rm_tree(self.dir)

    def test_write_json_then_read(self):
        self.s.write_json("a/b.json", {"k": 1, "中": "文"})
        self.assertEqual(self.s.read_json("a/b.json"), {"k": 1, "中": "文"})

    def test_write_json_leaves_no_tmp(self):
        self.s.write_json("x.json", {"k": 1})
        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".tmp_")]
        self.assertEqual(leftovers, [])

    def test_failed_write_does_not_destroy_previous(self):
        self.s.write_json("x.json", {"v": 1})

        class Boom:
            pass
        with self.assertRaises(TypeError):
            self.s.write_json("x.json", {"v": Boom()})
        self.assertEqual(self.s.read_json("x.json"), {"v": 1})

    def test_append_and_read_log(self):
        self.s.append("l.jsonl", {"a": 1})
        self.s.append("l.jsonl", {"a": 2})
        recs, bad = self.s.read_log("l.jsonl")
        self.assertEqual([r["a"] for r in recs], [1, 2])
        self.assertEqual(bad, [])

    def test_bad_lines_isolated_with_line_numbers(self):
        p = self.s.path("l.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a":1}\n')
            f.write('not json\n')
            f.write('[1,2,3]\n')
            f.write('{"a":2}\n')
        recs, bad = self.s.read_log("l.jsonl")
        self.assertEqual(len(recs), 2)
        self.assertEqual([b["line"] for b in bad], [2, 3])

    def test_read_log_missing_file_is_empty(self):
        self.assertEqual(self.s.read_log("nope.jsonl"), ([], []))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gate_")
        self.g = Gate(Store(self.dir))

    def tearDown(self):
        rm_tree(self.dir)

    def test_unapproved_is_rejected(self):
        ok, why = self.g.check("d1", "正文")
        self.assertFalse(ok)
        self.assertIn("未审批", why)

    def test_approved_passes(self):
        self.g.approve("d1", "正文", "alice")
        ok, why = self.g.check("d1", "正文")
        self.assertTrue(ok, why)

    def test_one_byte_change_invalidates(self):
        self.g.approve("d1", "正文", "alice")
        ok, why = self.g.check("d1", "正文。")
        self.assertFalse(ok)
        self.assertIn("内容已改动", why)

    def test_version_bump_requires_new_approval(self):
        self.g.approve("d1", "正文", "alice", version=1)
        ok, why = self.g.check("d1", "正文", version=2)
        self.assertFalse(ok)
        self.assertIn("版本不符", why)

    def test_blank_approver_refused(self):
        with self.assertRaises(ValueError):
            self.g.approve("d1", "正文", "   ")

    def test_require_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            self.g.require("d1", "正文")

    def test_latest_approval_wins(self):
        self.g.approve("d1", "v1 body", "alice")
        self.g.approve("d1", "v2 body", "bob")
        ok, why = self.g.check("d1", "v2 body")
        self.assertTrue(ok, why)
        self.assertIn("bob", why)


class MoneyTest(unittest.TestCase):
    def test_no_float_drift_on_sum(self):
        a = money.to_micro("0.1")
        b = money.to_micro("0.2")
        self.assertEqual(money.fmt_usd(a + b), "0.3000")

    def test_cost_of_integer_micro(self):
        self.assertEqual(money.cost_of(1000000, 0, "0.5", "1.5"), 500000)

    def test_cost_of_rejects_negative_tokens(self):
        with self.assertRaises(money.MoneyError):
            money.cost_of(-1, 0, "1", "1")

    def test_cost_of_rejects_bool_tokens(self):
        with self.assertRaises(money.MoneyError):
            money.cost_of(True, 0, "1", "1")

    def test_wilson_bounds(self):
        p, lo, hi = money.wilson(9, 10)
        self.assertTrue(0.0 <= lo <= p <= hi <= 1.0)

    def test_wilson_zero_n(self):
        self.assertEqual(money.wilson(0, 0), (0.0, 0.0, 0.0))

    def test_wilson_out_of_range(self):
        with self.assertRaises(money.MoneyError):
            money.wilson(11, 10)

    def test_fmt_places(self):
        self.assertEqual(money.fmt_usd(400000000, 2), "400.00")


class TextTest(unittest.TestCase):
    def test_control_chars_removed(self):
        self.assertEqual(clean("a\x00b\x07c"), "abc")

    def test_zero_width_removed(self):
        self.assertEqual(clean("a\u200bb\ufeffc"), "abc")

    def test_fullwidth_space_collapsed(self):
        self.assertEqual(clean("a\u3000\u3000b\t c"), "a b c")

    def test_split_list_dedupes_and_keeps_order(self):
        self.assertEqual(split_list("保温;保温，防锈|保温"), ["保温", "防锈"])

    def test_norm_key_ignores_space_case_punct(self):
        self.assertEqual(norm_key(" ＡＢＣ 部 "), norm_key("abc部"))

    def test_redact_email_phone(self):
        self.assertEqual(redact("a@bc.com"), "a***@bc.com")
        self.assertEqual(redact("alice@corp.cn"), "a***@corp.cn")
        self.assertNotIn("@***@", redact("a@bc.com"))
        self.assertEqual(redact("13812345678"), "138****5678")

    def test_truncate_keeps_limit(self):
        self.assertEqual(len(truncate("x" * 50, 10)), 10)

    def test_clean_none(self):
        self.assertEqual(clean(None), "")


class ReportTest(unittest.TestCase):
    def test_md_table_pads_cells(self):
        t = report.md_table(["a", "bb"], [["1", "2"]])
        lines = t.split("\n")
        self.assertEqual(lines[0], "| a | bb |")
        self.assertTrue(lines[1].startswith("|---"))

    def test_csv_uses_lf_only(self):
        self.assertNotIn("\r", report.csv_text(["a"], [[1]]))

    def test_pct_and_num(self):
        self.assertEqual(report.pct(0.925), "92.5%")
        self.assertEqual(report.num(1), "1.00")


class EvidenceTest(unittest.TestCase):
    def test_missing_ref_raises(self):
        ev = Evidence({"d1": {"title": "t"}})
        with self.assertRaises(EvidenceError):
            ev.check(["d1", "d2"])

    def test_claim_kind_validated(self):
        with self.assertRaises(EvidenceError):
            Claim("x", "guess")

    def test_claim_render_has_tag(self):
        c = Claim("毛利 1", "fact", ["d1"])
        self.assertIn("【事实】", c.render())


if __name__ == "__main__":
    unittest.main()

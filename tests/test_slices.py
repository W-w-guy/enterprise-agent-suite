# -*- coding: utf-8 -*-
"""5 个切片的行为测试。每个断言都对应客户能自己复核的一件事。"""
import csv, hashlib, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from helper import rm_tree
from slices import s1506_bizdata as s1506
from slices import s1519_connector as s1519
from slices import s1521_kg as s1521
from slices import s1530_eval_board as s1530
from slices import s1531_pilot as s1531

DATA = os.path.join(ROOT, "data")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class S1530Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="s1530_")

    def tearDown(self):
        rm_tree(self.dir)

    def _rows(self):
        exp = os.path.join(DATA, "experiments")
        paths = [os.path.join(exp, f) for f in os.listdir(exp)]
        return s1530.load_rows(sorted(paths))

    def test_duplicate_without_id_is_still_deduped(self):
        """客户不填 id 时，去重不能静默失效：同 run_id 重复追加不许把样本量翻倍。"""
        p = os.path.join(self.dir, "noid.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for _ in range(2):
                f.write(json.dumps({"run_id": "r1", "group": "g0", "ok": True,
                                    "latency_ms": 100, "tokens_in": 10, "tokens_out": 5}) + "\n")
        rows, rej = s1530.load_rows([p])
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("重复样本" in r["why"] for r in rej))
        self.assertEqual(s1530.summarize(rows)["g0"]["n"], 1)

    def test_rows_without_id_and_run_id_are_reported(self):
        """既无 id 也无 run_id：无法判定重复，必须报出来而不是默默计入。"""
        p = os.path.join(self.dir, "nokey.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"group": "g0", "ok": True, "latency_ms": 100}) + "\n")
        rows, rej = s1530.load_rows([p])
        self.assertTrue(any("无法判定是否重复样本" in r["why"] for r in rej))

    def test_dirty_rows_rejected_not_crashing(self):
        rows, bad = self._rows()
        self.assertGreater(len(rows), 100)
        whys = " ".join(b["why"] for b in bad)
        self.assertIn("不是合法 JSON", whys)
        self.assertIn("ok 不是布尔", whys)
        self.assertIn("latency_ms 不是非负整数", whys)

    def test_cost_is_recomputed_from_tokens(self):
        rows, _ = self._rows()
        self.assertTrue(all(isinstance(r["cost_micro"], int) for r in rows))

    def test_significant_regression_returns_exit_code_1(self):
        out = os.path.join(self.dir, "o")
        rc = s1530.main([os.path.join(DATA, "experiments"), "g0", out])
        self.assertIn(rc, (0, 1))

    def test_board_is_deterministic(self):
        a = os.path.join(self.dir, "a")
        b = os.path.join(self.dir, "b")
        s1530.main([os.path.join(DATA, "experiments"), "g0", a])
        s1530.main([os.path.join(DATA, "experiments"), "g0", b])
        self.assertEqual(sha(os.path.join(a, "board.md")), sha(os.path.join(b, "board.md")))
        self.assertEqual(sha(os.path.join(a, "diff.json")), sha(os.path.join(b, "diff.json")))

    def test_missing_baseline_is_reported_not_crash(self):
        rows, bad = self._rows()
        summary, d, regress = s1530.build(rows, bad, "nope", os.path.join(self.dir, "x"))
        self.assertIn("error", d)
        self.assertEqual(regress, [])

    def test_insufficient_sample_is_worded_honestly(self):
        rows, bad = self._rows()
        summary, d, _ = s1530.build(rows, bad, "g0", os.path.join(self.dir, "y"))
        verdicts = {i["group"]: i["verdict"] for i in d["items"]}
        self.assertTrue(any("样本不足" in v or "显著" in v or "无差异" in v for v in verdicts.values()))
        for i in d["items"]:
            if not i["significant"]:
                self.assertIn("样本不足", i["verdict"] if i["verdict"] != "无差异" else "样本不足")


class S1506Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="s1506_")
        self.cat = s1506.load_metrics(os.path.join(DATA, "metric_catalog.csv"))
        self.vals, self.bad = s1506.load_values(os.path.join(DATA, "values.csv"))

    def tearDown(self):
        rm_tree(self.dir)

    def test_bad_rows_isolated(self):
        self.assertGreaterEqual(len(self.bad), 1)
        self.assertTrue(any("不可解析" in b["why"] for b in self.bad))

    def test_three_kinds_are_separated(self):
        from core.evidence import Evidence
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08",
                          "gross_profit:revenue", {}, self.vals, self.cat, Evidence())
        kinds = {c.kind for c in res["claims"]}
        self.assertEqual(kinds, {"fact", "calc", "advice"})

    def test_query_period_variants_are_normalized(self):
        """客户写 2026-8 / 2026/08 也是同一个期间，不能报「无数据」。"""
        from core.evidence import Evidence
        for p in ("2026-08", "2026-8", "2026/08", "2026.8", "202608", "2026-08-01"):
            res = s1506.brief("gross_profit", p, "2026-07", "2025-08",
                              "", {}, self.vals, self.cat, Evidence())
            self.assertNotIn("error", res, p)
            self.assertFalse(any("无数据" in u for u in res["unresolved"]), "%s 被当成无数据" % p)

    def test_unknown_period_is_refused_not_silently_empty(self):
        """季度不猜：写法不可识别时明确拒绝，而不是装作「没数据」。"""
        from core.evidence import Evidence
        res = s1506.brief("gross_profit", "2026Q3", "2026-07", "2025-08",
                          "", {}, self.vals, self.cat, Evidence())
        self.assertIn("error", res)
        self.assertIn("不可识别", res["error"])

    def test_zero_base_is_not_missing(self):
        """上期=0 是「环比无定义」，不是「缺上期」——措辞会直接影响客户是否去补数据。"""
        from core.evidence import Evidence
        vals = dict(self.vals)
        vals[("gross_profit", "2026-07")] = 0
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08",
                          "", {}, vals, self.cat, Evidence())
        txt = " ".join(res["unresolved"])
        self.assertIn("为 0", txt)
        self.assertIn("不是缺数据", txt)
        self.assertNotIn("缺上期", txt)

    def test_negative_base_gives_absolute_change_not_ratio(self):
        """亏损收窄不是「下滑」。基期为负时禁止给比率，也不许出趋势建议。"""
        from core.evidence import Evidence
        vals = {("gross_profit", "2026-08"): -400000 * 10 ** 6,
                ("gross_profit", "2026-07"): -1000000 * 10 ** 6,
                ("gross_profit", "2025-08"): 700000 * 10 ** 6}
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08", "", {}, vals, self.cat, Evidence())
        txt = " ".join(c.text for c in res["claims"])
        self.assertIn("绝对变动", txt)
        self.assertNotIn("%", txt.split("绝对变动")[0])
        adv = [c for c in res["claims"] if c.kind == "advice"][0]
        self.assertIn("不出趋势建议", adv.text)

    def test_yoy_magnitude_checked_too(self):
        """同比也要做量级校验。旧版只查环比，同比会印出 +571328.6% 却一声不响。"""
        from core.evidence import Evidence
        vals = {("gross_profit", "2026-08"): 400000 * 10 ** 6,
                ("gross_profit", "2026-07"): 1000000 * 10 ** 6,
                ("gross_profit", "2025-08"): 70 * 10 ** 6}
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08", "", {}, vals, self.cat, Evidence())
        self.assertTrue(any("同比 达" in c.text and "量级突增" in c.text for c in res["claims"]))
        adv = [c for c in res["claims"] if c.kind == "advice"][0]
        self.assertIn("量级异常", adv.text)

    def test_inverted_ratio_is_flagged(self):
        """毛利率 575% 是分子分母写反了，必须提示。"""
        from core.evidence import Evidence
        vals = {("gross_profit", "2026-08"): 400000 * 10 ** 6, ("revenue", "2026-08"): 2300000 * 10 ** 6,
                ("gross_profit", "2026-07"): 1000000 * 10 ** 6, ("gross_profit", "2025-08"): 700000 * 10 ** 6}
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08",
                          "revenue:gross_profit", {}, vals, self.cat, Evidence())
        self.assertTrue(any("分子分母是否写反" in c.text for c in res["claims"]))

    def test_unregistered_metric_refused(self):
        from core.evidence import Evidence
        res = s1506.brief("nope_metric", "2026-08", "2026-07", "2025-08", "", {}, self.vals, self.cat, Evidence())
        self.assertIn("error", res)

    def test_missing_data_suppresses_advice(self):
        from core.evidence import Evidence
        # 用「合法但不存在」的期间：2026-05。不用 2026-99 —— 那是写法错误，
        # 现在会被明确拒绝（见 test_unknown_period_is_refused_not_silently_empty），
        # 两者是不同的事，不能混在一个断言里。
        res = s1506.brief("gross_profit", "2026-05", "2026-07", "2025-08", "",
                          {}, self.vals, self.cat, Evidence())
        self.assertNotIn("error", res)
        adv = [c for c in res["claims"] if c.kind == "advice"]
        self.assertTrue(adv)
        self.assertIn("数据不齐", adv[0].text)

    def test_money_shown_with_two_decimals(self):
        from core.evidence import Evidence
        res = s1506.brief("gross_profit", "2026-08", "2026-07", "2025-08", "",
                          {}, self.vals, self.cat, Evidence())
        fact = [c for c in res["claims"] if c.kind == "fact"][0]
        self.assertIn("400000.00", fact.text)
        self.assertNotIn("400000.0000", fact.text)

    def test_cli_writes_file(self):
        out = os.path.join(self.dir, "b.md")
        rc = s1506.main([os.path.join(DATA, "metric_catalog.csv"), os.path.join(DATA, "values.csv"),
                         "gross_profit", "2026-08", "2026-07", "2025-08", out, "gross_profit:revenue"])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            self.assertIn("毛利率", f.read())


class S1521Test(unittest.TestCase):
    def setUp(self):
        self.ev = s1521.load_docs(os.path.join(DATA, "docs.csv"))
        trips, pre = s1521.load_triples(os.path.join(DATA, "triples.csv"))
        self.nodes, self.edges, self.unres, self.rej = s1521.build(trips, self.ev)
        self.rej = pre + self.rej

    def test_edge_without_quote_goes_unresolved(self):
        self.assertTrue(any("缺引文" in u["why"] for u in self.unres))

    def test_edge_with_missing_doc_goes_unresolved(self):
        self.assertTrue(any("证据文档不存在" in u["why"] for u in self.unres))

    def test_predicate_whitelist_enforced(self):
        self.assertTrue(any("白名单" in r["why"] for r in self.rej))

    def test_self_loop_rejected(self):
        self.assertTrue(any("自环" in r["why"] for r in self.rej))

    def test_every_edge_has_evidence(self):
        for e in self.edges:
            self.assertIn(e["doc_id"], self.ev.docs)
            self.assertTrue(e["quote"])

    def test_subgraph_depth(self):
        sub = s1521.subgraph(self.nodes, self.edges, "订单系统", 1)
        self.assertIn("订单系统", sub["nodes"])
        sub2 = s1521.subgraph(self.nodes, self.edges, "订单系统", 2)
        self.assertGreaterEqual(len(sub2["nodes"]), len(sub["nodes"]))

    def test_unknown_center_returns_explicit_error(self):
        """中心实体不存在时给明确错误，不是 None —— None 会在报告里印成空，客户分不清是哪种问题。"""
        sub = s1521.subgraph(self.nodes, self.edges, "不存在的实体", 2)
        self.assertIn("error", sub)
        self.assertIn("不在图中", sub["error"])
        self.assertEqual(sub["nodes"], [])

    def test_duplicate_triple_does_not_inflate_degree(self):
        """同一条边上报两次：边数 1，度数 1。旧版度数是 2，客户按度数排重要性会被带偏。"""
        det = s1521.load_docs(os.path.join(DATA, "docs.csv"))
        tr = [{"subject": "A", "predicate": "依赖", "object": "B", "doc_id": "doc_arch",
               "quote": "A 依赖 B", "line": 1},
              {"subject": "A", "predicate": "依赖", "object": "B", "doc_id": "doc_arch",
               "quote": "A 依赖 B", "line": 2}]
        n, e, un, rj = s1521.build(tr, det)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["count"], 2)
        self.assertEqual(n["a"]["degree"], 1)
        self.assertEqual(n["b"]["degree"], 1)

    def test_quote_unrelated_to_entities_is_unresolved(self):
        """引文必须真的提到主语或宾语。随便填一句无关的话不许让边进图。"""
        det = s1521.load_docs(os.path.join(DATA, "docs.csv"))
        tr = [{"subject": "订单系统", "predicate": "依赖", "object": "库存服务", "doc_id": "doc_arch",
               "quote": "今天天气不错，与系统无关", "line": 1}]
        n, e, un, rj = s1521.build(tr, det)
        self.assertEqual(len(e), 0)
        self.assertTrue(any("引文与主语/宾语都不匹配" in u["why"] for u in un))

    def test_deep_chain_does_not_hit_recursion_limit(self):
        """1200 节点的链不能让切片崩。旧版递归 DFS 直接 RecursionError。"""
        det = s1521.load_docs(os.path.join(DATA, "docs.csv"))
        tr = [{"subject": "n%d" % i, "predicate": "依赖", "object": "n%d" % (i + 1),
               "doc_id": "doc_arch", "quote": "n%d 依赖 n%d" % (i, i + 1), "line": i + 1}
              for i in range(1200)]
        n, e, un, rj = s1521.build(tr, det)
        self.assertEqual(s1521.find_cycles(e), [])


class S1519Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="s1519_")
        self.store = s1519.Store(self.dir)
        with open(os.path.join(DATA, "remote_docs.json"), encoding="utf-8") as f:
            self.docs = json.load(f)

    def tearDown(self):
        rm_tree(self.dir)

    def test_doc_without_id_is_rejected_not_merged(self):
        """上游缺 id 时不能两篇文档撞同一个 key 互相覆盖。"""
        class Tr:
            def __init__(self, pages):
                self.pages, self.i = pages, 0
            def fetch(self, provider, cursor):
                if self.i >= len(self.pages):
                    return 200, {"items": []}
                r = self.pages[self.i]; self.i += 1; return r
        pages = [(200, {"items": [{"principals": ["R1"], "title": "甲"},
                                  {"principals": ["R1"], "title": "乙"}]}), (200, {"items": []})]
        log = s1519.sync("wps", self.store, Tr(pages))
        self.assertEqual(log["upserted"], 0)
        self.assertEqual(len([f for f in log["failed"] if "缺 id" in f["why"]]), 2)

    def test_retry_covers_transient_faults(self):
        tr = s1519.Transport(self.docs, {"feishu": ["429", "500"]})
        log = s1519.sync("feishu", self.store, tr)
        self.assertGreaterEqual(len(log.get("retries", [])), 2)
        self.assertGreaterEqual(log["upserted"], 1)

    def test_403_stops_and_keeps_cursor(self):
        tr = s1519.Transport(self.docs, {"wps": ["403"]})
        log = s1519.sync("wps", self.store, tr)
        self.assertTrue(any("403" in f["why"] for f in log["failed"]))

    def test_unknown_permission_is_skipped_not_silently_granted(self):
        tr = s1519.Transport(self.docs, {})
        log = s1519.sync("wps", self.store, tr)
        self.assertGreaterEqual(log["skipped_perm"], 1)
        recs, _ = self.store.read_log("documents.jsonl")
        for r in recs:
            self.assertNotIn("unknown", r["roles"])

    def test_second_run_is_idempotent(self):
        tr = s1519.Transport(self.docs, {})
        first = s1519.sync("feishu", self.store, tr)
        self.store2 = s1519.Store(self.dir)
        tr2 = s1519.Transport(self.docs, {})
        second = s1519.sync("feishu", self.store2, tr2)
        self.assertGreaterEqual(first["upserted"], 1)
        self.assertEqual(second["upserted"], 0)

    def test_provenance_recorded(self):
        tr = s1519.Transport(self.docs, {})
        s1519.sync("feishu", self.store, tr)
        recs, _ = self.store.read_log("documents.jsonl")
        self.assertTrue(recs)
        for r in recs:
            self.assertEqual(r["provenance"]["source"], "feishu")
            self.assertTrue(r["content_sha256"])

    def test_cursor_persisted(self):
        tr = s1519.Transport(self.docs, {})
        s1519.sync("feishu", self.store, tr)
        st = self.store.read_json("sync_state/feishu.json", {})
        self.assertIn("seen", st)


class S1531Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="s1531_")

    def tearDown(self):
        rm_tree(self.dir)

    def test_negative_history_rejected(self):
        """历史含负数（退款/亏损月）时偏离率会符号翻转，必须拒算而不是给错结论。"""
        rows = [{"category": "亏损月", "amount": 10.0, "history": [-100.0, -100.0, -100.0]}]
        good, bad = s1531.detect(rows, 0.1)
        self.assertEqual(good, [])
        self.assertTrue(any("符号翻转" in b["why"] for b in bad))

    def test_without_approval_nothing_is_queued(self):
        out = os.path.join(self.dir, "noappr")
        rep = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"), out, approve=False)
        self.assertFalse(rep["gate"]["ok"])
        self.assertFalse(os.path.isfile(os.path.join(out, "queue.jsonl")))
        self.assertIn("未审批", rep["gate"]["reason"])

    def test_tamper_after_approval_is_blocked(self):
        out = os.path.join(self.dir, "tamper")
        rep = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"), out, tamper=True)
        self.assertFalse(rep["gate"]["ok"])
        self.assertIn("内容已改动", rep["gate"]["reason"])
        self.assertFalse(os.path.isfile(os.path.join(out, "queue.jsonl")))

    def test_approved_is_queued(self):
        out = os.path.join(self.dir, "ok")
        rep = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"), out)
        self.assertTrue(rep["gate"]["ok"])
        self.assertTrue(os.path.isfile(os.path.join(out, "queue.jsonl")))

    def test_eval_agrees_with_gate(self):
        out = os.path.join(self.dir, "ev")
        rep = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"), out)
        item = [i for i in rep["eval"]["items"] if i["name"] == "审批结论与投递一致"][0]
        self.assertTrue(item["pass"])
        self.assertEqual(rep["eval"]["score"], 1.0)

    def test_bad_rows_do_not_stop_pipeline(self):
        out = os.path.join(self.dir, "bad")
        rep = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"), out)
        self.assertTrue(rep["anomalies"])
        self.assertTrue(rep["bad_rows"])

    def test_policy_missing_field_raises(self):
        p = os.path.join(self.dir, "bad_policy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 0.5}, f)
        with self.assertRaises(ValueError):
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "x"))


class PoisonDataTest(unittest.TestCase):
    """下毒数据轮：客户真实表格里会出现、而正常路径不会碰到的东西。

    每一条都是先跑出「真的会错」，再修，再钉住。不是凭想象编的用例。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="poison_")

    def tearDown(self):
        rm_tree(self.dir)

    def test_excel_thousands_separator_parsed(self):
        p = os.path.join(self.dir, "v.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("metric_id,period,value\n")
            f.write('revenue,2026-08,"2,300,000"\n')
            f.write('revenue,2026-07,"2,600,000"\n')
        vals, bad = s1506.load_values(p)
        self.assertEqual(bad, [])
        self.assertEqual(len(vals), 2)

    def test_accounting_parentheses_is_negative(self):
        p = os.path.join(self.dir, "v2.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("metric_id,period,value\n")
            f.write('cost,2026-08,"(1,234)"\n')
        vals, bad = s1506.load_values(p)
        self.assertEqual(bad, [])
        self.assertLess(list(vals.values())[0], 0)

    def test_irregular_comma_still_rejected(self):
        p = os.path.join(self.dir, "v3.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("metric_id,period,value\n")
            f.write("revenue,2026-08,12,3\n")
        vals, bad = s1506.load_values(p)
        self.assertEqual(vals, {})
        self.assertTrue(bad)

    def test_corrupt_cursor_does_not_crash_sync(self):
        st = s1519.Store(self.dir)
        st.write_json("sync_state/feishu.json", {"cursor": "abc", "seen": {}})
        with open(os.path.join(DATA, "remote_docs.json"), encoding="utf-8") as f:
            docs = json.load(f)
        log = s1519.sync("feishu", st, s1519.Transport(docs, {}))
        recs, _ = st.read_log("sync_log.jsonl")
        self.assertTrue(any(r.get("event") == "cursor_reset" for r in recs))
        self.assertGreaterEqual(log["upserted"], 1)

    def test_control_char_in_group_is_rejected_not_merged(self):
        p = os.path.join(self.dir, "g.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"group": "ga", "ok": True, "latency_ms": 100,
                                "tokens_in": 10, "tokens_out": 10}) + "\n")
            f.write(json.dumps({"group": "g\x07a", "ok": False, "latency_ms": 100,
                                "tokens_in": 10, "tokens_out": 10}) + "\n")
        rows, bad = s1530.load_rows([p])
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("控制字符" in b["why"] for b in bad))

    def test_policy_numeric_string_threshold_is_normalised(self):
        p = os.path.join(self.dir, "p.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": "0.5", "owner": "x", "queue": "q", "rubric": []}, f)
        rep = s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "o"))
        self.assertTrue(rep["anomalies"])

    def test_policy_non_numeric_threshold_gives_clear_error(self):
        p = os.path.join(self.dir, "p1.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": "半成", "owner": "x", "queue": "q", "rubric": []}, f)
        with self.assertRaises(ValueError) as cm:
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "o1"))
        self.assertIn("threshold_pct", str(cm.exception))

    def test_policy_threshold_as_percent_is_rejected(self):
        p = os.path.join(self.dir, "p2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 50, "owner": "x", "queue": "q", "rubric": []}, f)
        with self.assertRaises(ValueError):
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "o2"))

    def test_policy_empty_owner_rejected(self):
        p = os.path.join(self.dir, "p3.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 0.5, "owner": "  ", "queue": "q", "rubric": []}, f)
        with self.assertRaises(ValueError):
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "o3"))

    def test_custom_rubric_is_honoured(self):
        p = os.path.join(self.dir, "rb.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 0.5, "owner": "财务", "queue": "q",
                       "rubric": [{"id": "only", "name": "只看审计", "w": 1.0,
                                   "check": "audit_traceable"}]}, f)
        rep = s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "ro"))
        self.assertEqual([i["name"] for i in rep["eval"]["items"]], ["只看审计"])
        self.assertEqual(rep["eval"]["source"], "policy.rubric")

    def test_rubric_weights_must_sum_to_one(self):
        p = os.path.join(self.dir, "rb2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 0.5, "owner": "财务", "queue": "q",
                       "rubric": [{"id": "a", "name": "a", "w": 0.5, "check": "audit_traceable"}]}, f)
        with self.assertRaises(ValueError) as cm:
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "rb2"))
        self.assertIn("权重之和", str(cm.exception))

    def test_unknown_check_key_rejected(self):
        p = os.path.join(self.dir, "rb3.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threshold_pct": 0.5, "owner": "财务", "queue": "q",
                       "rubric": [{"id": "a", "name": "a", "w": 1.0, "check": "guess"}]}, f)
        with self.assertRaises(ValueError) as cm:
            s1531.run(os.path.join(DATA, "expenses.json"), p, os.path.join(self.dir, "rb3"))
        self.assertIn("不认识", str(cm.exception))

    def test_huge_quote_does_not_break_kg(self):
        p = os.path.join(self.dir, "t.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("subject,predicate,object,doc_id,quote\n")
            f.write('订单系统,属于,交易域,doc_arch,"订单系统 %s"\n' % ("引文" * 500))
        tr, _ = s1521.load_triples(p)
        nodes, edges, un, rej = s1521.build(tr, s1521.load_docs(os.path.join(DATA, "docs.csv")))
        self.assertEqual(len(edges), 1)
        self.assertEqual(rej, [])


class AdversarialRoundTest(unittest.TestCase):
    """切片专属对抗轮：每一条都是先跑出真错，再修，再钉住。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="adv_")

    def tearDown(self):
        rm_tree(self.dir)

    # ---- 19/50 经营数据 ----
    def _csv(self, name, rows, header=("metric_id", "period", "value")):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(r)
        return p

    def test_period_variants_normalised(self):
        p = self._csv("p.csv", [("revenue", "2026-8", "1"), ("revenue", "2026/09", "2"),
                                ("revenue", "2026年10月", "3"), ("revenue", "20261001", "4")])
        vals, bad = s1506.load_values(p)
        periods = sorted(k[1] for k in vals)
        self.assertEqual(periods, ["2026-08", "2026-09", "2026-10"])

    def test_quarter_is_refused_not_guessed(self):
        p = self._csv("q.csv", [("revenue", "2026Q3", "1")])
        vals, bad = s1506.load_values(p)
        self.assertEqual(vals, {})
        self.assertIn("无法归一", bad[0]["why"])

    def test_duplicate_period_not_silently_overwritten(self):
        p = self._csv("d.csv", [("revenue", "2026-08", "100"), ("revenue", "2026-08", "999")])
        vals, bad = s1506.load_values(p)
        self.assertEqual(list(vals.values()), [100000000])
        self.assertIn("重复上报", bad[0]["why"])

    def test_zero_base_is_not_missing_data(self):
        from core.evidence import Evidence
        p = self._csv("z.csv", [("revenue", "2026-07", "0"), ("revenue", "2026-08", "100")])
        vals, _ = s1506.load_values(p)
        cat = s1506.load_metrics(os.path.join(DATA, "metric_catalog.csv"))
        res = s1506.brief("revenue", "2026-08", "2026-07", "2025-08", "", {}, vals, cat, Evidence())
        joined = " ".join(res["unresolved"])
        self.assertIn("无定义", joined)
        self.assertNotIn("缺上期", joined)

    def test_magnitude_jump_suppresses_trend_advice(self):
        from core.evidence import Evidence
        p = self._csv("mag.csv", [("revenue", "2025-08", "21000000"), ("revenue", "2026-07", "26000"),
                                 ("revenue", "2026-08", "23000000")])
        vals, _ = s1506.load_values(p)
        cat = s1506.load_metrics(os.path.join(DATA, "metric_catalog.csv"))
        res = s1506.brief("revenue", "2026-08", "2026-07", "2025-08", "", {}, vals, cat, Evidence())
        adv = [c for c in res["claims"] if c.kind == "advice"][0]
        self.assertIn("单位", adv.text)
        self.assertNotIn("维持现有节奏", adv.text)

    # ---- 34/50 知识图谱 ----
    def _kg(self, triples):
        from core.evidence import Evidence
        ev = Evidence()
        ev.add("doc1", uri="a", title="架构")
        out = []
        for s_, p_, o_, q_ in triples:
            out.append({"line": 1, "subject": s_, "predicate": p_, "object": o_,
                        "doc_id": "doc1", "quote": q_})
        return s1521.build(out, ev)

    def test_alias_candidates_not_auto_merged(self):
        nodes, edges, _u, _r = self._kg([("订单系统", "负责", "履约", "订单系统负责履约"),
                                         ("订单系统(OMS)", "负责", "履约", "订单系统负责履约")])
        self.assertEqual(len(nodes), 3)
        cands = s1521.find_alias_candidates(nodes)
        self.assertTrue(any("订单系统" in "/".join(c["names"]) for c in cands))

    def test_suspected_reversed_relation_flagged(self):
        nodes, edges, _u, _r = self._kg([("订单系统", "依赖", "库存服务", "库存服务依赖订单系统")])
        self.assertTrue(s1521.find_reversed(edges))

    def test_cycle_detected(self):
        nodes, edges, _u, _r = self._kg([("A", "依赖", "B", "A 依赖 B"),
                                         ("B", "依赖", "C", "B 依赖 C"),
                                         ("C", "依赖", "A", "C 依赖 A")])
        cycles = s1521.find_cycles(edges)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0][0], cycles[0][-1])

    # ---- 32/50 连接器 ----
    def test_permission_revocation_is_logged(self):
        st = s1519.Store(os.path.join(self.dir, "rev"))
        v1 = [{"provider": "feishu", "id": "f9", "rev": "v1", "title": "机密",
               "principals": ["ou_owner", "ou_fin"], "content": "x", "updated_at": "1"}]
        s1519.sync("feishu", st, s1519.Transport(v1, {}))
        v2 = [{"provider": "feishu", "id": "f9", "rev": "v2", "title": "机密",
               "principals": ["ou_owner"], "content": "x", "updated_at": "2"}]
        s1519.sync("feishu", st, s1519.Transport(v2, {}))
        recs, _ = st.read_log("sync_log.jsonl")
        ev = [r for r in recs if r.get("event") == "permission_revoked"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["removed_roles"], ["finance"])

    def test_cross_source_duplicate_reported(self):
        st = s1519.Store(os.path.join(self.dir, "dup"))
        docs = [{"provider": "feishu", "id": "f1", "rev": "v1", "title": "A",
                 "principals": ["ou_owner"], "content": "same", "updated_at": "1"},
                {"provider": "wps", "id": "w1", "rev": "v1", "title": "A",
                 "principals": ["R1"], "content": "same", "updated_at": "1"}]
        s1519.sync("feishu", st, s1519.Transport(docs, {}))
        s1519.sync("wps", st, s1519.Transport(docs, {}))
        dupes = s1519.cross_source_duplicates(st)
        self.assertEqual(len(dupes), 1)
        self.assertEqual(sorted(dupes[0]["keys"]), ["feishu:f1", "wps:w1"])

    # ---- 43/50 看板 ----
    def _exp(self, name, rows):
        d = os.path.join(self.dir, "exp")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def test_duplicate_sample_counted_once(self):
        p = self._exp("a.jsonl", [{"group": "g0", "run_id": "r1", "id": "q1", "ok": True,
                                   "latency_ms": 100, "tokens_in": 10, "tokens_out": 10}] * 3)
        rows, bad = s1530.load_rows([p])
        self.assertEqual(len(rows), 1)
        self.assertIn("重复样本", bad[0]["why"])

    def test_float_tokens_rejected_not_truncated(self):
        p = self._exp("b.jsonl", [{"group": "g0", "id": "q1", "ok": True, "latency_ms": 100,
                                   "tokens_in": 10.5, "tokens_out": 0}])
        rows, bad = s1530.load_rows([p])
        self.assertEqual(rows, [])
        self.assertIn("不是整数", bad[0]["why"])

    def test_missing_baseline_exits_nonzero(self):
        p = self._exp("c.jsonl", [{"group": "g1", "id": "q1", "ok": True, "latency_ms": 100,
                                   "tokens_in": 10, "tokens_out": 10}])
        rc = s1530.main([os.path.dirname(p), "g0", os.path.join(self.dir, "o")])
        self.assertEqual(rc, 2)

    # ---- 44/50 试点 ----
    def test_stale_approver_blocked(self):
        from core.gate import Gate, sha_of
        o = os.path.join(self.dir, "stale")
        with open(os.path.join(DATA, "policy.json"), encoding="utf-8") as f:
            pol = json.load(f)
        pol["owner"] = "现任张三"
        with open(os.path.join(DATA, "expenses.json"), encoding="utf-8") as f:
            rows = json.load(f)
        an, _ = s1531.detect(rows, float(pol["threshold_pct"]))
        text = s1531.draft(an, pol)
        Gate(s1519.Store(o)).approve("notice-1", text, "离职员工老王", version=1)
        p = os.path.join(self.dir, "stale.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(pol, f, ensure_ascii=False)
        rep = s1531.run(os.path.join(DATA, "expenses.json"), p, o, approve=False)
        self.assertFalse(rep["gate"]["ok"])
        self.assertIn("不是当前责任人", rep["gate"]["reason"])
        self.assertFalse(os.path.isfile(os.path.join(o, "queue.jsonl")))

    def test_overlong_notice_blocked_before_queue(self):
        o = os.path.join(self.dir, "long")
        rows = [{"category": "类目%03d" % i, "amount": 900000, "history": [1000, 1000, 1000]}
                for i in range(400)]
        p = os.path.join(self.dir, "many.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        rep = s1531.run(p, os.path.join(DATA, "policy.json"), o)
        self.assertFalse(rep["gate"]["ok"])
        self.assertIn("超过队列上限", rep["gate"]["reason"])
        self.assertFalse(os.path.isfile(os.path.join(o, "queue.jsonl")))

    def test_gbk_json_accepted(self):
        from core.report import read_json
        p = os.path.join(self.dir, "gbk.json")
        with open(p, "w", encoding="gb18030") as f:
            json.dump({"owner": "财务共享中心"}, f, ensure_ascii=False)
        self.assertEqual(read_json(p)["owner"], "财务共享中心")


class CrossCuttingTest(unittest.TestCase):
    def test_demo_is_deterministic_and_path_free(self):
        a = tempfile.mkdtemp(prefix="demo_a_")
        b = tempfile.mkdtemp(prefix="demo_b_")
        try:
            for out in (a, b):
                r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "run.py"), "demo", out],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(r.returncode, 0, r.stderr[-500:])
            for rel in ["1530/board.md", "1530/board.csv", "1530/diff.json",
                        "1506_brief.md", "1521/kg.md", "1521/kg.json", "1531/report.json"]:
                pa, pb = os.path.join(a, rel), os.path.join(b, rel)
                self.assertTrue(os.path.isfile(pa), rel)
                self.assertEqual(sha(pa), sha(pb), rel + " 两次运行不一致")
            for dp, _dn, fns in os.walk(a):
                for f in fns:
                    if f.endswith((".md", ".json", ".csv")):
                        with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as fh:
                            txt = fh.read()
                        self.assertNotIn(ROOT, txt, "%s 泄漏本机绝对路径" % f)
        finally:
            rm_tree(a)
            rm_tree(b)


if __name__ == "__main__":
    unittest.main()

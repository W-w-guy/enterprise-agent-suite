# -*- coding: utf-8 -*-
"""43/50 评测对照与成本看板：六组实验结果汇总 + 回归差异 + 成本统计。

输入 experiments/*.jsonl，每行一个样本结果：
  {"group":"g1","run_id":"r1","id":"q001","ok":true,"latency_ms":820,
   "tokens_in":300,"tokens_out":120}
成本不直接读原始 cost 字段（客户那张表口径不一），而是按 token 单价重算，
所以同一份数据换了价目表也能重出看板，且金额是整数微美元、不飘。
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import money, report
from core.text import clean

PRICES = {"in": "0.5", "out": "1.5"}   # 每百万 token 的美元单价（示例价目表）

def load_rows(paths):
    rows, rejected = [], []
    for p in sorted(paths):
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                where = "%s:%d" % (os.path.basename(p), i)
                try:
                    r = json.loads(line)
                except ValueError:
                    rejected.append({"where": where, "why": "不是合法 JSON", "raw": line[:80]})
                    continue
                if not isinstance(r, dict):
                    rejected.append({"where": where, "why": "不是对象", "raw": line[:80]})
                    continue
                raw_g = r.get("group")
                g = clean(raw_g)
                if not g:
                    rejected.append({"where": where, "why": "缺 group", "raw": line[:80]})
                    continue
                if any(ord(ch) < 32 or ch in "\u200b\ufeff" for ch in str(raw_g)):
                    # 静默清洗会把 "g\x07a" 与 "ga" 并成一组，两组数据混在一起还算得头头是道，
                    # 这是最坏的错法；宁可拒绝并报出行号让人去改源数据。
                    rejected.append({"where": where,
                                     "why": "group 含控制字符或不可见字符（清洗后会与已有组重名）",
                                     "raw": repr(raw_g)[:60]})
                    continue
                ok = r.get("ok")
                if not isinstance(ok, bool):
                    rejected.append({"where": where, "why": "ok 不是布尔（收到 %r）" % (ok,), "raw": line[:80]})
                    continue
                lat = r.get("latency_ms")
                if not isinstance(lat, int) or isinstance(lat, bool) or lat < 0:
                    rejected.append({"where": where, "why": "latency_ms 不是非负整数（收到 %r）" % (lat,), "raw": line[:80]})
                    continue
                for nm in ("tokens_in", "tokens_out"):
                    tv = r.get(nm, 0)
                    if tv is None:
                        tv = 0
                    if isinstance(tv, bool) or not isinstance(tv, int):
                        # 浮点 token 会被 int() 静默截断（10.5 → 10），成本就少算了。
                        # 客户用估算模型时常写出小数，必须让他知道被拒了。
                        rejected.append({"where": where,
                                         "why": "%s 不是整数（收到 %r；成本按整数 token 计）" % (nm, tv),
                                         "raw": line[:80]})
                        break
                    if tv < 0 or tv > 10 ** 9:
                        rejected.append({"where": where, "why": "%s 超出合理范围（%r）" % (nm, tv),
                                         "raw": line[:80]})
                        break
                else:
                    pass
                if len(rejected) and rejected[-1]["where"] == where:
                    continue
                try:
                    c = money.cost_of(int(r.get("tokens_in") or 0), int(r.get("tokens_out") or 0),
                                      PRICES["in"], PRICES["out"])
                except (money.MoneyError, TypeError, ValueError) as e:
                    rejected.append({"where": where, "why": "token 字段不可用：%s" % e, "raw": line[:80]})
                    continue
                rows.append({"group": g, "id": clean(r.get("id")), "ok": ok,
                             "latency_ms": lat, "cost_micro": c,
                             "run_id": clean(r.get("run_id"))})
    # 重复样本：客户重跑实验时常常是「追加」而不是「覆盖」，
    # 同一 (group, run_id, id) 出现两次会让样本量翻倍、通过率被拉向重复的那一侧。
    # 保留首次出现的，其余进 rejected 并说明，不静默去重。
    seen, dedup, dup = set(), [], []
    for r in rows:
        # ★ 2026-11-02 修：旧版靠 id 去重，客户不填 id 时去重**静默失效**，
        # 同一 run_id 重复追加就把样本量翻倍、通过率被拉向重复那侧，而报告一个字都不提。
        # 现在：有 id 用 (group,run_id,id)；没 id 用内容指纹；两者都没有就明确报出来。
        if r["id"]:
            k = ("id", r["group"], r["run_id"], r["id"])
            tag = r["id"]
        elif r["run_id"]:
            k = ("fp", r["group"], r["run_id"], r["latency_ms"], r["ok"], r["cost_micro"])
            tag = "%s/%s/%s/%s" % (r["run_id"], r["latency_ms"], r["ok"], r["cost_micro"])
        else:
            k = None
            tag = ""
        if k is None:
            dup.append({"where": r["group"], "why": "既无 id 也无 run_id，无法判定是否重复样本（已计入，可能重复计数）",
                        "raw": str(r["latency_ms"])})
            dedup.append(r)
            continue
        if k in seen:
            dup.append({"where": "%s/%s" % (r["group"], tag),
                        "why": "重复样本（同 group+run_id+%s，保留首次）" % ("id" if r["id"] else "指纹"),
                        "raw": tag})
            continue
        seen.add(k)
        dedup.append(r)
    return dedup, rejected + dup

def p50(vals):
    if not vals:
        return 0
    v = sorted(vals)
    n = len(v)
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) // 2

def summarize(rows):
    groups = sorted({r["group"] for r in rows})
    out = {}
    for g in groups:
        rs = [r for r in rows if r["group"] == g]
        n = len(rs)
        ok = sum(1 for r in rs if r["ok"])
        p, lo, hi = money.wilson(ok, n)
        out[g] = {"n": n, "ok": ok, "pass_rate": p, "ci_lo": lo, "ci_hi": hi,
                  "p50_latency_ms": p50([r["latency_ms"] for r in rs]),
                  "cost_micro": money.sum_micro([r["cost_micro"] for r in rs]),
                  "cost_per_sample_micro": (money.sum_micro([r["cost_micro"] for r in rs]) // n) if n else 0}
    return out

def diff_against(summary, base):
    """与基线比。只有当两组 Wilson 区间【不重叠】才算显著，否则记不显著。"""
    if base not in summary:
        return {"base": base, "error": "基线组不存在：%s" % base}
    b = summary[base]
    items = []
    for g in sorted(summary):
        if g == base:
            continue
        c = summary[g]
        d_rate = c["pass_rate"] - b["pass_rate"]
        separated = (c["ci_hi"] < b["ci_lo"]) or (c["ci_lo"] > b["ci_hi"])
        verdict = "无差异"
        if separated and d_rate < 0:
            verdict = "显著变差"
        elif separated and d_rate > 0:
            verdict = "显著变好"
        elif abs(d_rate) >= 0.05:
            verdict = "方向性%s但样本不足" % ("变差" if d_rate < 0 else "变好")
        items.append({"group": g,
                      "pass_rate": c["pass_rate"], "base_pass_rate": b["pass_rate"],
                      "delta": d_rate,
                      "significant": bool(separated),
                      "verdict": verdict,
                      "latency_delta_ms": c["p50_latency_ms"] - b["p50_latency_ms"],
                      "cost_delta_micro": c["cost_micro"] - b["cost_micro"],
                      "cost_delta_pct": (float(c["cost_micro"] - b["cost_micro"]) / b["cost_micro"] * 100.0) if b["cost_micro"] else 0.0,
                      "n_group": c["n"], "n_base": b["n"],
                      "n_needed_per_group": _n_needed(b["pass_rate"], max(abs(d_rate), 1e-9))})
    return {"base": base, "items": items}

def _n_needed(p0, delta, z=1.96, power=0.8):
    """两组等样本、双侧 5%、power 80% 的粗算：每组需要多少样本才能判出 delta 的差。

    只用来回答「还要跑多少」，不做精确功效分析。
    """
    if delta <= 0:
        return None
    zb = 0.8416
    p1 = min(1.0, p0 + delta)
    var = p0 * (1 - p0) + p1 * (1 - p1)
    n = ((z + zb) ** 2 * var) / (delta ** 2)
    return int(n) + 1

def build(rows, rejected, base, outdir):
    summary = summarize(rows)
    d = diff_against(summary, base)
    os.makedirs(outdir, exist_ok=True)
    hdr = ["group", "n", "ok", "pass_rate", "ci95", "p50_latency_ms", "cost_usd", "usd_per_sample"]
    trows = []
    for g in sorted(summary):
        s = summary[g]
        trows.append([g, s["n"], s["ok"], report.pct(s["pass_rate"]),
                      "%s–%s" % (report.pct(s["ci_lo"]), report.pct(s["ci_hi"])),
                      s["p50_latency_ms"], money.fmt_usd(s["cost_micro"]),
                      money.fmt_usd(s["cost_per_sample_micro"], 6)])
    lines = ["# 评测对照与成本看板", "",
             "样本合计 %d 条；脏行 %d 条（见文末）。成本按价目表重算，非原始 cost 字段。" % (len(rows), len(rejected)), "",
             report.md_table(hdr, trows), ""]
    if "items" in d:
        dh = ["group", "pass_rate", "vs base", "是否显著", "判定", "每组需样本", "p50 差(ms)", "成本差"]
        drows = [[i["group"], report.pct(i["pass_rate"]), "%+.1f pt" % (100 * i["delta"]),
                  "是" if i["significant"] else "否", i["verdict"],
                  (i["n_needed_per_group"] if i["n_needed_per_group"] else "-"),
                  "%+d" % i["latency_delta_ms"],
                  "%+s (%+.1f%%)" % (money.fmt_usd(i["cost_delta_micro"]), i["cost_delta_pct"])]
                 for i in d["items"]]
        lines += ["## 与基线 `%s` 的差异" % base, "", report.md_table(dh, drows), ""]
    else:
        lines += ["## 与基线 `%s` 的差异" % base, "", "⚠ %s" % d["error"], ""]
    if rejected:
        lines += ["## 未计入的脏行（%d 条）" % len(rejected), "",
                  report.md_table(["位置", "原因"], [[r["where"], r["why"]] for r in rejected]), ""]
    report.write_text(os.path.join(outdir, "board.md"), "\n".join(lines) + "\n")
    report.write_text(os.path.join(outdir, "board.csv"), report.csv_text(hdr, trows))
    report.write_json(os.path.join(outdir, "diff.json"),
                      {"summary": summary, "diff": d, "rejected": rejected})
    regress = [i["group"] for i in d.get("items", []) if i["verdict"] == "显著变差"]
    return summary, d, regress

def main(argv):
    expdir, base, outdir = argv[0], argv[1], argv[2]
    paths = [os.path.join(expdir, f) for f in os.listdir(expdir) if f.endswith(".jsonl")]
    rows, rejected = load_rows(paths)
    if not rows:
        print("没有任何有效样本；脏行 %d 条" % len(rejected))
        for r in rejected[:5]:
            print("  -", r["where"], r["why"])
        return 2
    summary, d, regress = build(rows, rejected, base, outdir)
    print("组数 %d，样本 %d，脏行 %d" % (len(summary), len(rows), len(rejected)))
    if "error" in d:
        # 基线不存在时若返回 0，CI 会当成「差异检查通过」，这是假的通过。
        print("基线组不存在：%s（可用组：%s）" % (base, ", ".join(sorted(summary))))
        return 2
    for i in d.get("items", []):
        print("  %-6s %s  %s%s" % (i["group"], report.pct(i["pass_rate"]),
                                   i["verdict"], "" if i["significant"] else "（区间重叠，不算数）"))
    if regress:
        print("发现显著变差：%s" % ", ".join(regress))
        return 1
    return 0

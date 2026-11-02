# -*- coding: utf-8 -*-
"""19/50 经营数据 Agent：配置指标查询与分析 Bot，区分事实 / 计算 / 建议。

口径是三段分离，不许混着说：
  事实 = 直接来自明细数据，必须带来源行
  计算 = 由事实按明确公式推出，公式要写出来
  建议 = 规则命中的判断，必须能追溯到命中的规则
数据缺失时【不给建议】—— 拿不全的数据给建议，比不给更糟。
"""
import csv, json, os, sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import money
from core.evidence import Claim, Evidence
from core.text import clean, norm_key

RULES = [
    {"id": "R1", "when": "mom <= -0.10", "advice": "环比下滑超过 10%，先查单价与销量拆分，再谈促销"},
    {"id": "R2", "when": "yoy <= -0.10", "advice": "同比下滑超过 10%，检查是否有渠道或客户流失"},
    {"id": "R3", "when": "gm <= 0.15", "advice": "毛利率低于 15%，复核成本口径与折扣审批"},
]

def _enc(path):
    """探测编码：中文 Windows 上 CSV 常是 GBK。依次试 utf-8-sig / utf-8 / gb18030。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, encoding=enc) as f:
                f.read(4096)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"

def load_metrics(path):
    out = {}
    with open(path, encoding=_enc(path), newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            mid = clean(row.get("metric_id") or row.get("指标") or "")
            if not mid:
                continue
            out[norm_key(mid)] = {"metric_id": mid,
                                  "name": clean(row.get("name") or mid),
                                  "unit": clean(row.get("unit") or ""),
                                  "doc_id": clean(row.get("doc_id") or ""),
                                  "formula": clean(row.get("formula") or "")}
    return out

_THOUSANDS = (" ", "\u00a0", "\u3000")

def parse_amount(raw):
    """把客户表格里的金额字符串转成 Decimal。

    Excel 导出默认带千分位（"2,300,000"），会计口径还有括号负数（"(1,234)"）。
    这两种都是合法数据，不能当脏行丢掉；但 "12,3" 这种不规则的必须拒。
    """
    t = clean(raw).replace(",", "").replace("，", "")
    for ch in _THOUSANDS:
        t = t.replace(ch, "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.startswith("-"):
        neg, t = True, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    if not t:
        raise ValueError("空数值")
    d = Decimal(t)
    return -d if neg else d

_PERIOD_PAT = None

def norm_period(p):
    """把客户表格里的期间写法归一成 YYYY-MM。

    "2026-8" / "2026/08" / "2026.8" / "2026年8月" / "202608" / "2026-08-01" 都归到 2026-08。
    ★ 不归一的后果不是报错，是「明明有这个月的数据，却报缺数据，然后不给建议」——
    看起来像保守，实际是静默丢数。
    季度（2026Q3）不猜，返回 None 由调用方拒绝：猜季度=替客户决定口径。
    """
    import re
    global _PERIOD_PAT
    t = clean(p).replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"(19|20)(\d{2})[-/.年]?(\d{1,2})月?(?:[-/.]\d{1,2})?", t)
    if m:
        y, mo = int(m.group(1) + m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return "%04d-%02d" % (y, mo)
    return None

def load_values(path):
    """返回 {(metric, period): value_micro} + 被拒行。数值一律转千分位整数避免浮点。"""
    vals, rejected = {}, []
    with open(path, encoding=_enc(path), newline="") as f:
        rd = csv.DictReader(f)
        for i, row in enumerate(rd, 2):
            # ★ 多出来的列必须拒：值里带逗号又没加引号时（revenue,2026-08,12,3），
            # csv 会把 value 截成 "12"，于是 $12 被当成 $12,000,000 的附近值静默入库。
            # 这是最难发现的错法——不报错、数字看起来还像真的。
            extra = row.get(None)
            if extra:
                rejected.append({"line": i, "why": "该行列数多于表头（值里可能有未加引号的逗号）",
                                 "raw": ",".join(str(x) for x in list(row.values())[:4])})
                continue
            m = clean(row.get("metric_id") or "")
            p_raw = clean(row.get("period") or "")
            raw = clean(row.get("value") or "")
            if not m or not p_raw or not raw:
                rejected.append({"line": i, "why": "缺 metric_id/period/value", "raw": str(row)[:80]})
                continue
            p = norm_period(p_raw)
            if p is None:
                rejected.append({"line": i,
                                 "why": "期间无法归一成 YYYY-MM（原值 %r；季度/自定义口径请先换算）" % p_raw,
                                 "raw": p_raw[:40]})
                continue
            try:
                micro = money.to_micro(parse_amount(raw))
            except Exception:
                # 先判数值再看重复：一行同时「重复且数值坏」时，
                # 报「数值坏」对客户更有用（他要去改的是数据，不是重复）。
                rejected.append({"line": i, "why": "数值不可解析（原值 %r）" % raw[:20], "raw": raw[:40]})
                continue
            key = (norm_key(m), p)
            if key in vals:
                # 同一指标同一期间重复上报：静默取后者会让「我表里写 100，报告说 999」，
                # 对账时无法解释。改为拒后者并说明，保留第一版。
                rejected.append({"line": i,
                                 "why": "同指标同期间重复上报（保留首次出现的行）",
                                 "raw": "%s %s" % (m, p)})
                continue
            vals[key] = micro
    return vals, rejected

def _get(vals, metric, period):
    return vals.get((norm_key(metric), period))

def brief(metric_id, period, prev, prev_year, cents, prices, vals, catalog, ev):
    """产出一条分析。返回 dict(claims, unresolved, meta)。"""
    # ★ 2026-11-02 修：查询参数也必须归一。原先只有数据行归了一，客户写 "2026-8"
    # 就会被告知「无数据」——数据明明在，这是静默丢数，比报错更坏。
    dsp = {"period": period, "prev": prev or "未提供", "prev_year": prev_year or "未提供"}
    for _k in ("period", "prev", "prev_year"):
        _v = dsp[_k] if _k == "period" else (prev if _k == "prev" else prev_year)
        if _v and norm_period(_v) is None:
            return {"error": "期间写法不可识别：%s=%r（支持 2026-08/2026-8/2026/08/2026年8月/202608/2026-08-01；季度不猜）" % (_k, _v)}
    period = norm_period(period)
    prev = norm_period(prev) if prev else None
    prev_year = norm_period(prev_year) if prev_year else None
    claims, unresolved = [], []
    meta = catalog.get(norm_key(metric_id))
    if meta is None:
        return {"error": "指标未登记：%s（未登记的指标一律不分析）" % metric_id}
    cur = _get(vals, metric_id, period)
    if cur is None:
        unresolved.append("%s 在 %s 无数据" % (metric_id, dsp["period"]))
    if meta["doc_id"]:
        ev.add(meta["doc_id"], uri="metric_catalog.csv", title="指标口径")
    if cur is not None:
        unit = meta["unit"] or ""
        places = 2 if unit.upper() in ("USD", "CNY", "RMB", "") else 6
        shown = money.fmt_usd(cur, places) + (" " + unit if unit else "")
        claims.append(Claim("%s 在 %s 为 %s" % (meta["name"], period, shown),
                            "fact", [meta["doc_id"]] if meta["doc_id"] else []))
    prev_v = _get(vals, metric_id, prev)
    yoy_v = _get(vals, metric_id, prev_year)
    # ★ 0 是合法数值，不是"没数据"。旧代码用 `if prev_v:` 判存在，
    # 于是上期=0 被报成「缺上期」，报告在撒谎（数据明明在）。
    prev_has = prev_v is not None
    yoy_has = yoy_v is not None
    mom = yoy = None
    sign_flip = False   # 基期为负 / 正负转换：比率无经济意义，禁止拿来出建议

    def _abs_change(cur_v, base_v, tag, base_label):
        """基期为负或正负转换时，只给绝对变动，不给比率。

        ★ 2026-11-02 修：旧代码直接 (本期-基期)/基期。亏损从 -100 万收窄到 -40 万，
        算出来是 -60%，报告写「环比下滑 60%」——实际情况是**好转**，
        并且会据此给出「先查单价与销量」这种方向相反的经营建议。
        比率在负基准下符号会翻转，这不是精度问题，是结论会反。
        """
        diff = Decimal(cur_v) - Decimal(base_v)
        word = "增加" if diff > 0 else ("减少" if diff < 0 else "持平")
        places = 2
        return Claim("%s %s %s（由 %s 变为 %s；基期为负或正负转换，比率无经济意义，只给绝对变动）"
                     % (tag, word, money.fmt_usd(abs(diff), places),
                        money.fmt_usd(base_v, places), money.fmt_usd(cur_v, places)),
                     "calc", [], formula="绝对变动：本期-基期")

    def _rel(cur_v, base_v, tag, base_label):
        """返回 (ratio, claim, note)。note 非空表示这一项算不出来或不该算。"""
        if base_v == 0:
            return None, None, "%s %s 为 0，%s无定义（不是缺数据）" % (metric_id, base_label, tag)
        if base_v < 0 or (cur_v < 0) != (base_v < 0):
            return None, _abs_change(cur_v, base_v, tag, base_label), None
        ratio = (Decimal(cur_v) - Decimal(base_v)) / Decimal(base_v)
        return ratio, Claim("%s %s（%s → %s）" % (tag, _fmtp(ratio), base_label, dsp["period"]),
                            "calc", [], formula="(本期-基期)/基期"), None

    if cur is not None and prev_has:
        if prev_v < 0 or (cur < 0) != (prev_v < 0):
            sign_flip = True
        mom, cl, note = _rel(cur, prev_v, "环比", dsp["prev"])
        if cl is not None:
            claims.append(cl)
        if note:
            unresolved.append(note)
    elif cur is not None:
        unresolved.append("%s 缺上期 %s，无法算环比" % (metric_id, dsp["prev"]))
    if cur is not None and yoy_has:
        if yoy_v < 0 or (cur < 0) != (yoy_v < 0):
            sign_flip = True
        yoy, cl, note = _rel(cur, yoy_v, "同比", dsp["prev_year"])
        if cl is not None:
            claims.append(cl)
        if note:
            unresolved.append(note)
    elif cur is not None:
        unresolved.append("%s 缺去年同期 %s，无法算同比" % (metric_id, dsp["prev_year"]))
    gm = None
    if cents and ":" in str(cents):
        num_m, den_m = [x.strip() for x in str(cents).split(":", 1)]
        n1 = _get(vals, num_m, dsp["period"])
        d1 = _get(vals, den_m, dsp["period"])
        if n1 is None or d1 is None:
            unresolved.append("缺 %s 或 %s 的 %s 数据，无法算比率" % (num_m, den_m, dsp["period"]))
        elif d1 == 0:
            unresolved.append("%s 在 %s 为 0，比率无定义（不是缺数据）" % (den_m, dsp["period"]))
        elif d1 < 0 or (n1 < 0) != (d1 < 0):
            sign_flip = True
            claims.append(Claim("%s / %s 基数为负或正负转换，比率无经济意义，不给比率（%s=%s，%s=%s）"
                                % (num_m, den_m, num_m, n1, den_m, d1), "calc", [],
                                formula="%s / %s（分母<=0 或符号相反，拒绝计算）" % (num_m, den_m)))
        else:
            gm = Decimal(n1) / Decimal(d1)
            claims.append(Claim("毛利率 %s（%s / %s）" % (_fmtp(gm), num_m, den_m),
                                "calc", [], formula="%s / %s" % (num_m, den_m)))
            if gm > 1 or gm < 0:
                # 毛利率这类比率 >100% 或为负，绝大多数是分子分母写反了。
                claims.append(Claim("比率 %s 超出 0~100%%，请核对分子分母是否写反（当前按 %s:%s 计算）"
                                    % (_fmtp(gm), num_m, den_m), "calc", [],
                                    formula="比率合理性校验：0 <= 比率 <= 1"))
    # 量级突增校验：比率超过 100 倍，几乎都是「单位混填」（万元 vs 元）而不是真实增长。
    # 不拦，但必须提示 —— 否则报告会印出「同比 +571328.6%」这种客户看一眼就不信的结论。
    # ★ 2026-11-02 修：旧代码只校验环比，同比同样会印出 57 万个百分点却一声不响。
    magnitude_suspect = False
    for _tag, _val in (("环比", mom), ("同比", yoy)):
        if _val is not None and _val > Decimal("100"):
            magnitude_suspect = True
            claims.append(Claim("%s 达 %s，量级突增，请先确认前期与本期单位是否一致（万元/元）"
                                % (_tag, _fmtp(_val)), "calc", [], formula="量级校验：%s > 100 倍" % _tag))
    # 建议：只有数据齐全、且比率本身有意义时才给
    hit = []
    for r in RULES:
        ok = False
        if r["id"] == "R1" and mom is not None:
            ok = mom <= Decimal("-0.10")
        if r["id"] == "R2" and yoy is not None:
            ok = yoy <= Decimal("-0.10")
        if r["id"] == "R3" and gm is not None:
            ok = gm <= Decimal("0.15")
        if ok:
            hit.append(r)
    if unresolved:
        claims.append(Claim("数据不齐或不可算（%s），本报告不给建议" % "；".join(unresolved), "advice",
                            formula="见上列未解决项"))
    elif sign_flip:
        # 亏损收窄、转亏、亏损扩大这几种情形，比率的正负会骗人（且方向可能正好相反），
        # 拿它套阈值规则会给出方向相反的经营建议。这里只陈述绝对变动，不出趋势建议。
        claims.append(Claim("基期为负或正负转换，比率无经济意义，本报告不出趋势建议；请按绝对额判断",
                            "advice", formula="符号校验未通过"))
    elif magnitude_suspect:
        # 数据本身可疑时给"维持现有节奏"是危险的：客户会以为一切正常。
        claims.append(Claim("量级异常，疑似单位混填；先核对单位，本报告不出趋势建议", "advice",
                            formula="量级校验未通过"))
    else:
        for r in hit:
            claims.append(Claim(r["advice"], "advice", [], formula="命中规则 %s（%s）" % (r["id"], r["when"])))
        if not hit:
            claims.append(Claim("未命中任何异常规则，维持现有节奏", "advice", [], formula="规则 R1–R3 均未命中"))
    return {"claims": claims, "unresolved": unresolved, "meta": meta}

def _fmtp(d):
    return "%+.1f%%" % (float(d) * 100.0)

def render(res, title):
    lines = ["# %s" % title, ""]
    if "error" in res:
        return "\n".join(lines + ["⚠ " + res["error"]]) + "\n"
    kinds = {"fact": [], "calc": [], "advice": []}
    for c in res["claims"]:
        kinds[c.kind].append(c.render())
    for k, name in (("fact", "一、事实（来自明细数据）"),
                    ("calc", "二、计算（公式写在括号里）"),
                    ("advice", "三、建议（可追溯到命中规则）")):
        lines.append("## " + name)
        lines += kinds[k] or ["- （无）"]
        lines.append("")
    if res["unresolved"]:
        lines += ["## 未解决", ""] + ["- " + u for u in res["unresolved"]] + [""]
    return "\n".join(lines)

def main(argv):
    metrics_p, values_p, metric_id, period, prev, prev_year, out_p = argv[:7]
    cents = argv[7] if len(argv) > 7 else ""
    catalog = load_metrics(metrics_p)
    vals, rejected = load_values(values_p)
    ev = Evidence()
    res = brief(metric_id, period, prev, prev_year, cents, {}, vals, catalog, ev)
    text = render(res, "%s 经营数据简报" % metric_id)
    from core import report as _rep
    _rep.write_text(out_p, text)
    print(text)
    if rejected:
        print("被拒数据行 %d 条：" % len(rejected))
        for r in rejected[:5]:
            print("  - line %s: %s" % (r["line"], r["why"]))
    return 0 if "error" not in res else 2

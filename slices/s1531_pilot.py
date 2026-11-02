# -*- coding: utf-8 -*-
"""44/50 非提案业务试点：把「知识 → 编排 → 审批 → 评测」串成一条能跑完的链。

选的任务是公司级非提案任务：月度费用异常核查（走知识库口径 → 检出异常 →
生成通知 → 人工审批 → 投递队列 → 评测打分）。

这条链的价值不在检出规则多聪明，而在三件事可验证：
  1) 审批没过，绝不投递（硬门，不是流程装饰）
  2) 审批后改了正文，立刻失效（哈希绑定）
  3) 每一步都有审计记录，崩在中间能看出断在哪
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.gate import Gate
from core.store import Store
from core.text import clean

STEPS = ["load_policy", "detect", "draft", "approve", "deliver", "evaluate"]
# 队列侧一般都有长度上限（钉钉/飞书/邮件正文都有），超长会被网关直接拒。
# 在这里拦住，比让客户去队列日志里找原因好。
MAX_NOTICE = 4000

def load_policy(path):
    from core.report import read_json
    pol = read_json(path)
    need = ("threshold_pct", "owner", "queue", "rubric")
    miss = [k for k in need if k not in pol]
    if miss:
        raise ValueError("知识库口径缺字段：%s" % ", ".join(miss))
    # 类型校验：policy.json 是人手写的，阈值写成 "0.5"（字符串）在 JSON 里完全合法，
    # 但一路跑到比较处会抛 TypeError —— 那不是「配置有误」，是程序没校验。
    try:
        thr = float(pol["threshold_pct"])
    except (TypeError, ValueError):
        raise ValueError("threshold_pct 必须是数字（收到 %r）" % (pol["threshold_pct"],))
    if not (0 <= thr < 1):
        raise ValueError("threshold_pct 应在 [0,1) 内表示比例（收到 %r；想写 50%% 请写 0.5）" % (pol["threshold_pct"],))
    pol["threshold_pct"] = thr
    if not isinstance(pol["rubric"], list):
        raise ValueError("rubric 必须是列表")
    for k in ("owner", "queue"):
        if not str(pol[k]).strip():
            raise ValueError("%s 不能为空" % k)
    return pol

def detect(rows, threshold_pct):
    """按「本月 vs 近三月均值」判异常。坏行跳过并记录，不整批停。"""
    good, bad = [], []
    for r in rows:
        try:
            cat = clean(r["category"])
            cur = float(r["amount"])
            hist = [float(x) for x in r["history"]]
        except (KeyError, TypeError, ValueError) as e:
            bad.append({"row": r, "why": "%s: %s" % (type(e).__name__, e)})
            continue
        if not hist:
            bad.append({"row": r, "why": "无历史，无法判基准"})
            continue
        base = sum(hist) / len(hist)
        if base == 0:
            bad.append({"row": r, "why": "历史均值为 0，无法算偏离"})
            continue
        if base < 0 or any(h <= 0 for h in hist):
            # ★ 2026-11-02 修：历史含 0 或负数（退款、冲销、亏损月）时，均值会被正负抵消成很小的分母，
            # 偏离率被放大到几百个百分点；更糟的是全负时 (本期-基期)/基期 会**符号翻转**——
            # 亏损从 -100 收窄到 -40 会被算成 -60%，于是「好转」进不了异常清单，直接漏报。
            # 不猜口径：拒绝计算并说清原因，让客户决定用绝对值还是指定基准。
            bad.append({"row": r, "why": "历史含 0 或负值，偏离率基准无意义（负数基准会使涨跌符号翻转），请改绝对值口径或指定基准"})
            continue
        dev = (cur - base) / base
        if dev >= threshold_pct:
            good.append({"category": cat, "amount": cur, "base": round(base, 2),
                         "deviation_pct": round(dev * 100, 1)})
    good.sort(key=lambda x: (-x["deviation_pct"], x["category"]))
    return good, bad

def draft(anomalies, policy):
    if not anomalies:
        return "本月无超阈值费用项，无需通知。"
    lines = ["【月度费用异常核查 · 请确认】", ""]
    for a in anomalies:
        lines.append("- %s：本月 %.2f，近三月均值 %.2f，偏离 +%.1f%%" % (
            a["category"], a["amount"], a["base"], a["deviation_pct"]))
    lines += ["", "口径：偏离 ≥ %.0f%% 视为异常（知识库 policy.json）" % (policy["threshold_pct"] * 100),
              "责任人：%s" % policy["owner"]]
    return "\n".join(lines)

CHECKS = {
    "anomalies_reviewable": lambda a, d: all("category" in x and "deviation_pct" in x for x in a) if a else True,
    "approval_consistency": lambda a, d: (d.get("gate_ok") is True and d.get("queued") is True)
                                        or (d.get("gate_ok") is not True and d.get("queued") is not True),
    "audit_traceable": lambda a, d: d.get("audit_lines", 0) >= 5,
}

def evaluate(anomalies, policy, delivered):
    """按 policy.json 里的 rubric 打分。

    这里判的是「系统行为是否与审批结论一致」，不是「结果好不好看」：
    - 门通过 → 必须真的投递了（否则等于悄悄吞掉）
    - 门拦下 → 必须没有投递（否则等于门形同虚设）

    ★ rubric 必须真的从 policy 读。上一版把三条判据硬编码在代码里，
    客户改 policy.json 的 rubric 完全不生效 —— 报告照样输出，看不出没生效，
    这是最坏的一种「可配置」：以为配了。
    """
    rubric = policy.get("rubric") or []
    if not rubric:
        rubric = [{"id": "r1", "name": "检出结果可复核", "w": 0.3, "check": "anomalies_reviewable"},
                  {"id": "r2", "name": "审批结论与投递一致", "w": 0.4, "check": "approval_consistency"},
                  {"id": "r3", "name": "审计可追溯", "w": 0.3, "check": "audit_traceable"}]
    total_w = sum(float(r.get("w", 0)) for r in rubric)
    if abs(total_w - 1.0) > 0.01:
        raise ValueError("rubric 权重之和必须为 1.0，当前 %.3f" % total_w)
    items, score = [], 0.0
    for r in rubric:
        key = r.get("check")
        if key not in CHECKS:
            raise ValueError("rubric 里的 check=%r 不认识，可用：%s" % (key, ", ".join(sorted(CHECKS))))
        ok = bool(CHECKS[key](anomalies, delivered))
        w = float(r.get("w", 0))
        if ok:
            score += w
        items.append({"id": r.get("id", key), "name": r.get("name", key), "check": key,
                      "pass": ok, "weight": w})
    return {"score": round(score, 2), "items": items, "source": "policy.rubric" if policy.get("rubric") else "builtin"}

def run(data_p, policy_p, outdir, approve=True, tamper=False):
    store = Store(outdir)
    audit = lambda ev, **kw: store.append("audit.jsonl", {"event": ev, **kw})
    audit("run_start", outdir=outdir)
    policy = load_policy(policy_p)
    audit("policy_loaded", threshold_pct=policy["threshold_pct"])
    from core.report import read_json
    rows = read_json(data_p) or []
    anomalies, bad = detect(rows, policy["threshold_pct"])
    audit("detected", anomalies=len(anomalies), bad_rows=len(bad))
    text = draft(anomalies, policy)
    audit("drafted", chars=len(text))
    gate = Gate(store)
    if approve:
        from core.gate import sha_of
        prev = gate.latest("notice-1")
        if prev is not None and prev.get("content_sha256") == sha_of(text):
            # 同一正文被重复审批：不阻止（可能是换了个人复核），
            # 审计上必须能看出「这份正文被批了几次、都是谁」。
            audit("approval_repeated", previous_approver=prev.get("approver"),
                  approver=policy["owner"])
        gate.approve("notice-1", text, approver=policy["owner"], version=1)
        audit("approved", approver=policy["owner"])
    if tamper:
        text_to_send = text + "\n（附：另加一条未审批的费用）"
        audit("tampered", chars=len(text_to_send))
    else:
        text_to_send = text
    if len(text_to_send) > MAX_NOTICE:
        audit("notice_too_long", chars=len(text_to_send), limit=MAX_NOTICE)
        delivered = {"gate_ok": False, "queued": False, "reason":
                     "通知正文 %d 字超过队列上限 %d，未投递（需缩短或改附件）" % (len(text_to_send), MAX_NOTICE)}
        return {"anomalies": anomalies, "bad_rows": bad, "notice": text,
                "gate": {"ok": False, "reason": delivered["reason"]},
                "eval": evaluate(anomalies, policy, delivered),
                "audit_records": len(store.read_log("audit.jsonl")[0]),
                "audit_bad_lines": store.read_log("audit.jsonl")[1]}
    ok, reason = gate.check("notice-1", text_to_send, version=1,
                            expected_approver=policy["owner"])
    audit("gate_check", ok=ok, reason=reason)
    delivered = {"gate_ok": bool(ok), "queued": False, "audit_lines": 0, "reason": reason}
    if ok:
        store.append("queue.jsonl", {"to": policy["queue"], "text": text_to_send})
        delivered["queued"] = True
        audit("delivered", queue=policy["queue"])
    else:
        audit("blocked", reason=reason)
    recs, badlog = store.read_log("audit.jsonl")
    delivered["audit_lines"] = len(recs)
    ev = evaluate(anomalies, policy, delivered)
    audit("evaluated", score=ev["score"])
    recs, badlog = store.read_log("audit.jsonl")
    report = {"anomalies": anomalies, "bad_rows": bad, "notice": text,
              "gate": {"ok": ok, "reason": reason}, "eval": ev,
              "audit_records": len(recs), "audit_bad_lines": badlog}
    store.write_json("report.json", report)
    return report

def render(report):
    lines = ["# 月度费用异常核查 · 试点报告", ""]
    lines.append("## 一、检出（%d 项）" % len(report["anomalies"]))
    for a in report["anomalies"]:
        lines.append("- %s：%.2f（基准 %.2f，+%.1f%%）" % (a["category"], a["amount"], a["base"], a["deviation_pct"]))
    if report["bad_rows"]:
        lines += ["", "## 二、被跳过的数据行（%d 行，未影响其他行）" % len(report["bad_rows"])]
        for b in report["bad_rows"]:
            lines.append("- %s" % b["why"])
    lines += ["", "## 三、审批门", "",
              "- 结果：%s" % ("通过并已投递" if report["gate"]["ok"] else "**拦下，未投递**"),
              "- 原因：%s" % report["gate"]["reason"], "",
              "## 四、投递正文", "", "```", report["notice"], "```", "",
              "## 五、评测", "", "- 得分：%.2f" % report["eval"]["score"]]
    for i in report["eval"]["items"]:
        lines.append("  - %s：%s（权重 %.1f）" % (i["name"], "通过" if i["pass"] else "未通过", i["weight"]))
    lines += ["", "## 六、审计", "", "- 记录 %d 条，坏行 %d 条" % (report["audit_records"], len(report["audit_bad_lines"]))]
    return "\n".join(lines) + "\n"

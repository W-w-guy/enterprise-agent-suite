# -*- coding: utf-8 -*-
"""34/50 知识图谱子域：在高价值业务子域建立实体、关系与证据引用。

硬口径：**没有证据引用的边不进图**。
客户 AI 最难被信任的地方就是「它说有」，所以每条边必须能指回原文一句引文；
指不回去的边进 unresolved，且不影响其他边建图。
"""
import csv, json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.evidence import Evidence, EvidenceError
from core.text import clean, norm_key

PRED_WHITELIST = {"属于", "负责", "位于", "供应", "使用", "依赖", "版本", "同义", "part_of", "owned_by"}
# 有方向含义的谓语：引文里若两个实体都出现但顺序相反，很可能主宾写反了
DIRECTIONAL = {"依赖", "属于", "负责", "供应", "使用", "part_of", "owned_by"}

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

def load_docs(path):
    ev = Evidence()
    if not os.path.isfile(path):
        return ev
    with open(path, encoding=_enc(path), newline="") as f:
        for row in csv.DictReader(f):
            did = clean(row.get("doc_id") or "")
            if did:
                ev.add(did, uri=clean(row.get("uri") or ""), title=clean(row.get("title") or ""),
                       sha256=clean(row.get("sha256") or ""))
    return ev

def load_triples(path):
    rows, rejected = [], []
    with open(path, encoding=_enc(path), newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            s = clean(row.get("subject") or "")
            p = clean(row.get("predicate") or "")
            o = clean(row.get("object") or "")
            doc = clean(row.get("doc_id") or "")
            quote = clean(row.get("quote") or "")
            if not (s and p and o):
                rejected.append({"line": i, "why": "主语/谓语/宾语有空值", "raw": str(row)[:80]})
                continue
            rows.append({"line": i, "subject": s, "predicate": p, "object": o,
                         "doc_id": doc, "quote": quote})
    return rows, rejected

def build(triples, ev):
    nodes, edges, unresolved, rejected = {}, [], [], []
    for t in triples:
        if t["predicate"] not in PRED_WHITELIST:
            rejected.append({"line": t["line"], "why": "谓语不在白名单：%s" % t["predicate"],
                             "raw": "%s-%s-%s" % (t["subject"], t["predicate"], t["object"])})
            continue
        if norm_key(t["subject"]) == norm_key(t["object"]):
            rejected.append({"line": t["line"], "why": "自环（主语=宾语）",
                             "raw": "%s-%s-%s" % (t["subject"], t["predicate"], t["object"])})
            continue
        refs = [t["doc_id"]] if t["doc_id"] else []
        if not t["quote"]:
            unresolved.append({"line": t["line"], "why": "缺引文，无法核对",
                               "edge": "%s -%s-> %s" % (t["subject"], t["predicate"], t["object"])})
            continue
        # ★ 2026-11-02 修：引文必须真的提到主语或宾语之一。
        # 旧代码只查「quote 非空、doc_id 存在」，于是随便填一句无关的话就能让这条边进图——
        # 「没有证据引用的边不进图」这句承诺形同虚设，而它正是本切片立单的理由。
        _q = norm_key(t["quote"])
        _probe = [norm_key(t["subject"]), norm_key(t["object"]),
                  norm_key(re.sub(u"[（(].*?[）)]", "", t["subject"])),
                  norm_key(re.sub(u"[（(].*?[）)]", "", t["object"]))]
        if not any(x and x in _q for x in _probe):
            unresolved.append({"line": t["line"],
                               "why": "引文与主语/宾语都不匹配，证据不成立",
                               "edge": "%s -%s-> %s" % (t["subject"], t["predicate"], t["object"]),
                               "quote": t["quote"][:60]})
            continue
        missing = [r for r in refs if r not in ev.docs]
        if not refs or missing:
            unresolved.append({"line": t["line"],
                               "why": "证据文档不存在：%s" % (", ".join(missing) or "空 doc_id"),
                               "edge": "%s -%s-> %s" % (t["subject"], t["predicate"], t["object"])})
            continue
        for n in (t["subject"], t["object"]):
            nodes.setdefault(norm_key(n), {"name": n, "degree": 0, "docs": set()})
        edges.append({"subject": t["subject"], "predicate": t["predicate"], "object": t["object"],
                      "doc_id": t["doc_id"], "quote": t["quote"], "line": t["line"]})

    # 去重：同一条边（主语+谓语+宾语+文档）只留一条，但记录出现次数
    merged = {}
    for e in edges:
        k = (norm_key(e["subject"]), e["predicate"], norm_key(e["object"]), e["doc_id"])
        if k in merged:
            merged[k]["count"] += 1
        else:
            merged[k] = dict(e, count=1)
    edges = sorted(merged.values(), key=lambda e: (norm_key(e["subject"]), e["predicate"], norm_key(e["object"])))
    # ★ 2026-11-02 修：度数在【去重之后】按【不同边】计。
    # 旧代码在循环里对每条原始三元组 +1，同一条边上报两次就变成度数 2、边只有 1 条，
    # 客户按度数排「哪个系统最关键」时会被重复上报直接带偏。
    for e in edges:
        for side in ("subject", "object"):
            k = norm_key(e[side])
            nodes.setdefault(k, {"name": e[side], "degree": 0, "docs": set()})
            nodes[k]["degree"] += 1
            nodes[k]["docs"].add(e["doc_id"])
    for v in nodes.values():
        v["docs"] = sorted(v["docs"])
    return nodes, edges, unresolved, rejected

def find_alias_candidates(nodes):
    """同一实体不同写法的候选（只提示，不自动合并）。

    「订单系统」「订单系统(OMS)」「OMS」在客户文档里常同时存在。
    自动合并是危险的（可能把「订单系统(旧)」和「订单系统」并成一个），
    不提示则是另一种危险：度数被稀释，图上看像三个互不相干的系统。
    """
    import re
    cands = []
    keys = list(nodes)
    base = {}
    for k in keys:
        stripped = re.sub(u"[（(].*?[）)]", "", k).strip()
        base.setdefault(stripped, []).append(k)
    for stripped, ks in sorted(base.items()):
        if len(ks) > 1:
            cands.append({"kind": "括号变体", "names": sorted(nodes[k]["name"] for k in ks)})
    # 全称里包含另一个简称，且简称很短（英文缩写/两字以内）时也提示
    for a in keys:
        for b in keys:
            if a == b:
                continue
            if len(b) <= 4 and b in a and not a.startswith(b):
                cands.append({"kind": "包含关系", "names": sorted([nodes[a]["name"], nodes[b]["name"]])})
    uniq, seen = [], set()
    for c in cands:
        k = tuple(c["names"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq

def find_reversed(edges):
    """引文里两个实体都出现、但顺序与主宾相反 → 疑似方向写反。

    例：主语「库存服务」宾语「订单系统」，引文「下单需调用库存服务预占」，
    引文里「订单系统」不在，这条判不出来；但若引文是
    「库存服务依赖订单系统」而主语写成「订单系统」，就能判出来。
    仅在引用里两个名字都出现时才判，避免误报。
    """
    out = []
    for e in edges:
        if e["predicate"] not in DIRECTIONAL:
            continue
        q = e["quote"] or ""
        i_s, i_o = q.find(e["subject"]), q.find(e["object"])
        if i_s >= 0 and i_o >= 0 and i_o < i_s:
            out.append({"edge": "%s -%s-> %s" % (e["subject"], e["predicate"], e["object"]),
                        "why": "引文里宾语出现在主语之前，疑似主宾写反",
                        "quote": q[:80]})
    return out

def find_cycles(edges):
    """在有向图上找环。依赖/归属关系出现环，几乎一定是数据错误。

    ★ 2026-11-02 修：改成迭代 DFS。旧版是递归，1200 个节点的链直接 RecursionError，
    整份报告崩掉——客户的真实组织/系统依赖图比 6 个节点的样例深得多。
    """
    graph = {}
    for e in edges:
        graph.setdefault(norm_key(e["subject"]), set()).add(norm_key(e["object"]))
    cycles, WHITE, GREY, BLACK = [], 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    for root in sorted(graph):
        if color.get(root, WHITE) != WHITE:
            continue
        path, pos = [root], {root: 0}
        color[root] = GREY
        stack = [iter(sorted(graph.get(root, ())))]
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                stack.pop()
                v = path.pop()
                pos.pop(v, None)
                color[v] = BLACK
                continue
            c = color.get(nxt, WHITE)
            if c == GREY:
                i = pos.get(nxt)
                if i is not None:
                    cycles.append(path[i:] + [nxt])
            elif c == WHITE:
                color[nxt] = GREY
                pos[nxt] = len(path)
                path.append(nxt)
                stack.append(iter(sorted(graph.get(nxt, ()))))
    return cycles


def subgraph(nodes, edges, center, depth=2):
    if norm_key(center) not in nodes:
        # ★ 2026-11-02 修：中心实体不存在时返回 None，调用方要么崩、要么在报告里印一个
        # "None"，客户看不出是「你没连上」还是「这系统坏了」。现在给明确结构，
        # 并把最接近的候选实体列出来（多半是写法不一致）。
        cands = [v["name"] for k, v in nodes.items() if norm_key(center) and norm_key(center) in k][:5]
        return {"center": clean(center), "error": "中心实体不在图中：%s" % clean(center),
                "candidates": sorted(cands), "nodes": [], "edges": []}
    frontier = {norm_key(center)}
    seen = set(frontier)
    picked = []
    for _ in range(depth):
        nxt = set()
        for e in edges:
            s, o = norm_key(e["subject"]), norm_key(e["object"])
            if s in frontier and o not in seen:
                nxt.add(o); picked.append(e)
            elif o in frontier and s not in seen:
                nxt.add(s); picked.append(e)
            elif s in frontier and o in frontier:
                picked.append(e)
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    uniq = {(norm_key(e["subject"]), e["predicate"], norm_key(e["object"]), e["doc_id"]): e for e in picked}
    return {"center": nodes[norm_key(center)]["name"],
            "nodes": sorted(nodes[k]["name"] for k in seen),
            "edges": sorted(uniq.values(), key=lambda e: (norm_key(e["subject"]), e["predicate"], norm_key(e["object"])))}

def render(nodes, edges, unresolved, rejected, sub, center,
           aliases=None, reversed_edges=None, cycles=None):
    lines = ["# 知识图谱子域（%s）" % center, "",
             "节点 %d、边 %d；未入图 %d 条（缺证据）、被拒 %d 条（不合规）。" % (
                 len(nodes), len(edges), len(unresolved), len(rejected)), ""]
    aliases = aliases or []
    reversed_edges = reversed_edges or []
    cycles = cycles or []
    if aliases or reversed_edges or cycles:
        lines += ["## 零、需要人工确认的三类问题", ""]
        for a in aliases:
            lines.append("- 疑似同一实体的不同写法（**未自动合并**）：%s —— %s" % (" / ".join(a["names"]), a["kind"]))
        for rv in reversed_edges:
            lines.append("- 疑似主宾写反：`%s`，引文「%s」" % (rv["edge"], rv["quote"]))
        for c in cycles:
            lines.append("- 依赖成环：%s" % " → ".join(c))
        lines.append("")
    lines += ["## 一、子图（2 跳）", ""]
    if sub is None:
        lines.append("⚠ 中心实体 `%s` 不在图中" % center)
    else:
        lines.append("节点：%s" % "、".join(sub["nodes"]))
        lines.append("")
        for e in sub["edges"]:
            lines.append("- %s —%s→ %s　证据：%s「%s」" % (e["subject"], e["predicate"], e["object"],
                                                       e["doc_id"], e["quote"]))
    lines += ["", "## 二、节点度数（高到低）", ""]
    for k in sorted(nodes, key=lambda x: (-nodes[x]["degree"], x)):
        v = nodes[k]
        lines.append("- %s：度 %d，涉及 %d 份文档" % (v["name"], v["degree"], len(v["docs"])))
    if unresolved:
        lines += ["", "## 三、未入图（缺证据，需补原文）", ""]
        for u in unresolved:
            lines.append("- line %s：%s　`%s`" % (u["line"], u["why"], u["edge"]))
    if rejected:
        lines += ["", "## 四、被拒（不合规，需人工改）", ""]
        for r in rejected:
            lines.append("- line %s：%s　`%s`" % (r["line"], r["why"], r["raw"]))
    return "\n".join(lines) + "\n"

def main(argv):
    triples_p, docs_p, center, outdir = argv[:4]
    ev = load_docs(docs_p)
    triples, pre = load_triples(triples_p)
    nodes, edges, unresolved, rejected = build(triples, ev)
    rejected = pre + rejected
    sub = subgraph(nodes, edges, center, 2)
    os.makedirs(outdir, exist_ok=True)
    from core import report as _rep
    aliases = find_alias_candidates(nodes)
    reversed_edges = find_reversed(edges)
    cycles = find_cycles(edges)
    _rep.write_text(os.path.join(outdir, "kg.md"),
                    render(nodes, edges, unresolved, rejected, sub, center,
                           aliases, reversed_edges, cycles))
    _rep.write_json(os.path.join(outdir, "kg.json"),
                    {"nodes": {k: {"name": v["name"], "degree": v["degree"], "docs": v["docs"]} for k, v in nodes.items()},
                     "edges": edges, "subgraph": sub, "unresolved": unresolved, "rejected": rejected,
                     "alias_candidates": aliases, "suspected_reversed": reversed_edges,
                     "cycles": cycles})
    print("节点 %d 边 %d 未入图 %d 被拒 %d；别名候选 %d / 疑似反向 %d / 成环 %d" % (
        len(nodes), len(edges), len(unresolved), len(rejected),
        len(aliases), len(reversed_edges), len(cycles)))
    if sub is None:
        print("中心实体不在图中：%s" % center)
        return 2
    return 0

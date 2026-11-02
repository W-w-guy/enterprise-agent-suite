# -*- coding: utf-8 -*-
"""32/50 飞书与 WPS 连接器：授权文档同步接口，保留来源和权限映射。

两家 API 的真实差异就在这三点，mock 按真实差异建模：
  1) 分页：飞书 page_token（无 page_size）；WPS offset+limit
  2) 权限：飞书 openid + 部门/群组；WPS 角色码 + 显式 acl
  3) 限流：飞书 429 + Retry-After 秒；WPS 500 抖动，无 Retry-After
交付物里给的是同一套「拉取 → 权限映射 → 幂等 upsert → 落来源戳」，
换真实 API 只需换 transport，不改上面四步。
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.store import Store
from core.text import clean

DEFAULT_PROVIDERS = {
    "feishu": {"page_size": 2, "perm_model": "openid+group",
               "role_map": {"ou_owner": "owner", "ou_fin": "finance", "ou_eng": "engineer",
                            "grp_all": "reader"}},
    "wps": {"page_size": 3, "perm_model": "rolecode",
            "role_map": {"R1": "owner", "R2": "finance", "R3": "reader", "R9": "unknown"}},
}

class Transport:
    """mock transport：按 provider 返回不同分页形状，并可注入故障。"""
    def __init__(self, docs, faults=None):
        self.docs = docs
        self.faults = faults or {}
        self.calls = 0

    def fetch(self, provider, cursor=None):
        self.calls += 1
        plan = self.faults.get(provider) or []
        if plan:
            f = plan.pop(0)
            if f == "429":
                return 429, {"Retry-After": 1}
            if f == "500":
                return 500, {}
            if f == "403":
                return 403, {}
        items = [d for d in self.docs if d["provider"] == provider]
        if provider == "feishu":
            start = int(cursor or 0)
            page = items[start:start + DEFAULT_PROVIDERS["feishu"]["page_size"]]
            nxt = str(start + len(page)) if start + len(page) < len(items) else None
            return 200, {"items": page, "page_token": nxt}
        off = int(cursor or 0)
        lim = DEFAULT_PROVIDERS["wps"]["page_size"]
        page = items[off:off + lim]
        nxt = str(off + lim) if off + lim < len(items) else None
        return 200, {"data": page, "next_offset": nxt}

def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def map_roles(provider, principals):
    m = DEFAULT_PROVIDERS[provider]["role_map"]
    roles, unmapped = [], []
    for p in principals or []:
        r = m.get(clean(p))
        if r and r != "unknown":
            roles.append(r)
        else:
            unmapped.append(clean(p))
    return sorted(set(roles)), sorted(set(unmapped))

def sync(provider, store, transport, max_pages=50, max_retries=3,
         allow=("owner", "finance", "engineer", "reader")):
    """增量同步：游标落盘，失败可从断点续传；upsert 幂等。"""
    roles_by_key = {}
    for r in (store.read_json("doc_collect.json", {}) or {}).get(provider, {}).items():
        roles_by_key[r[0]] = r[1]
    state = store.read_json("sync_state/%s.json" % provider, {}) or {}
    cursor = state.get("cursor")
    seen = state.get("seen") or {}
    if cursor not in (None, "") and not str(cursor).isdigit():
        # 游标损坏（手工改过、旧版本写的、被截断）时不能让它把整轮同步炸掉：
        # 从头发起，代价是重扫一遍（upsert 幂等，不会重复入库），并留痕。
        store.append("sync_log.jsonl", {"event": "cursor_reset", "provider": provider,
                                         "bad_cursor": str(cursor)[:60]})
        cursor = None
    log = {"provider": provider, "pages": 0, "upserted": 0, "skipped_perm": 0,
           "failed": [], "documents": []}
    attempts = 0
    while True:
        if log["pages"] >= max_pages:
            log["failed"].append({"why": "超过最大页数 %d，停止" % max_pages})
            break
        code, body = transport.fetch(provider, cursor)
        log["pages"] += 1
        if code in (429, 500):
            attempts += 1
            if attempts > max_retries:
                log["failed"].append({"why": "上游 %d 连续 %d 次，停止并保留游标" % (code, attempts), 
                                      "cursor": cursor, "page": log["pages"]})
                break
            wait = int(body.get("Retry-After") or 1) if code == 429 else 1
            log.setdefault("retries", []).append({"code": code, "wait_s": wait,
                                                  "page": log["pages"], "attempt": attempts})
            continue
        if code == 403:
            log["failed"].append({"why": "上游 403（授权失效），需人工重新授权", "page": log["pages"]})
            break
        attempts = 0
        items = body.get("items") or body.get("data") or []
        for d in items:
            doc_id = clean(d.get("id"))
            rev = clean(d.get("rev"))
            if not doc_id:
                # ★ 2026-11-02 修：上游不返回 id 时，key 会退化成 "provider:"，两篇不同的文档
                # 撞同一个 key，后一篇覆盖前一篇 —— 同步报告只说 upsert 1 篇，实际有 2 篇，
                # 而且被吞掉的那篇永远不会出现在任何日志里。宁可拒收并报出来。
                log["failed"].append({"why": "上游文档缺 id，无法幂等 upsert（拒收，避免互相覆盖）",
                                      "page": log["pages"], "title": clean(d.get("title"))[:40]})
                store.append("sync_log.jsonl", {"event": "skip_no_id", "provider": provider,
                                                 "title": clean(d.get("title"))[:40], "rev": rev})
                continue
            roles, unmapped = map_roles(provider, d.get("principals"))
            allowed = [r for r in roles if r in allow]
            if not allowed:
                log["skipped_perm"] += 1
                store.append("sync_log.jsonl", {"event": "skip_perm", "provider": provider,
                                                 "doc_id": doc_id, "unmapped": unmapped, "roles": roles})
                continue
            key = "%s:%s" % (provider, doc_id)
            if seen.get(key) == rev:
                store.append("sync_log.jsonl", {"event": "unchanged", "key": key, "rev": rev})
                continue
            # 权限收回复核：新版本的角色若比上一版少，必须单独留事件。
            # 只写一条 upsert 记录在审计上回答不了「谁在什么时候失去了访问权」，
            # 而这正是合规检查最常问的一句。
            prev = roles_by_key.get(key)
            if prev is not None:
                revoked = sorted(set(prev) - set(allowed))
                granted = sorted(set(allowed) - set(prev))
                if revoked:
                    store.append("sync_log.jsonl", {"event": "permission_revoked", "key": key,
                                                     "rev": rev, "removed_roles": revoked,
                                                     "remaining_roles": allowed})
                if granted:
                    store.append("sync_log.jsonl", {"event": "permission_granted", "key": key,
                                                     "rev": rev, "added_roles": granted})
            roles_by_key[key] = allowed
            rec = {"key": key, "provider": provider, "remote_id": doc_id, "rev": rev,
                   "title": clean(d.get("title")), "roles": allowed,
                   "unmapped_principals": unmapped,
                   "content_sha256": sha(d.get("content")),
                   "fetched_at": clean(d.get("updated_at")),
                   "provenance": {"source": provider, "endpoint": "/open-apis/docs/list",
                                  "cursor": cursor}}
            store.append("documents.jsonl", {"event": "upsert", **rec})
            seen[key] = rev
            log["upserted"] += 1
            log["documents"].append(rec)
        cursor = body.get("page_token") or body.get("next_offset")
        store.write_json("sync_state/%s.json" % provider,
                         {"cursor": cursor, "seen": seen, "updated_at": "mock"})
        store.write_json("doc_collect.json",
                         dict((store.read_json("doc_collect.json", {}) or {}),
                              **{provider: roles_by_key}))
        if not cursor:
            break
    return log

def cross_source_duplicates(store):
    """同一份内容在两家平台各存一份时，键不同但内容 sha 相同。

    这种重复不会报错，但后果是：客户在自己那边看到的文档数是两份，
    权限也分两处维护，收回其中一处仍能被另一处访问 —— 属于「权限绕过」的隐患。
    这里只报出来并要求人工确认共用哪一处的权限，不自动去重（去重会丢来源）。
    """
    recs, _ = store.read_log("documents.jsonl")
    by_sha = {}
    for r in recs:
        by_sha.setdefault(r.get("content_sha256"), []).append(r)
    out = []
    for sha_v, rs in by_sha.items():
        if len(rs) < 2:
            continue
        providers = {r["provider"] for r in rs}
        if len(providers) < 2:
            continue
        out.append({"content_sha256": sha_v, "keys": sorted(r["key"] for r in rs),
                    "roles": {r["key"]: r["roles"] for r in rs}})
    return out

def main(argv):
    data_p, outdir = argv[0], argv[1]
    store = Store(outdir)
    from core import report as _rep
    docs = _rep.read_json(data_p)
    faults = {"feishu": ["429", "500"], "wps": ["500", "403"]}
    transport = Transport(docs, {k: list(v) for k, v in faults.items()})
    lines = ["# 飞书 / WPS 文档同步报告", ""]
    for provider in ("feishu", "wps"):
        log = sync(provider, store, transport)
        lines.append("## %s" % provider)
        lines.append("- 页数 %d，upsert %d 篇，权限不足跳过 %d 篇" % (log["pages"], log["upserted"], log["skipped_perm"]))
        if log.get("retries"):
            lines.append("- 限流重试：%s" % json.dumps(log["retries"], ensure_ascii=False))
        for f in log["failed"]:
            lines.append("- ⚠ 失败：%s" % f["why"])
        lines.append("")
    dupes = cross_source_duplicates(store)
    if dupes:
        lines += ["## 跨平台同内容（需人工确认权限以哪边为准）", ""]
        for u in dupes:
            lines.append("- 内容 %s… 同时存在：%s" % (u["content_sha256"][:12], " / ".join(u["keys"])))
            for k, v in sorted(u["roles"].items()):
                lines.append("    - %s → %s" % (k, ", ".join(v)))
        lines.append("")
    _rep.write_text(os.path.join(outdir, "sync.md"), "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0

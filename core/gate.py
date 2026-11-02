# -*- coding: utf-8 -*-
"""审批硬门：没批过不许往下走，批过改了内容立刻失效。

不是流程装饰，是防「审的是 A、发的是 B」：
- 审批记录里存 content_sha256（被审内容的哈希）
- 执行前重算哈希，与审批记录比对；不一致直接拒绝，并留痕
- 审批人必须非空；approver 为空按未审批处理（防自动批准）
- version 变化也必须重新审批（防旧批文套新内容）
"""
import hashlib, time

from .store import Store

REQUIRED = ("approver", "content_sha256", "version", "approved_at")

def sha_of(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

class Gate:
    def __init__(self, store: Store, log="approvals.jsonl"):
        self.store = store
        self.log = log

    def approve(self, item_id, content, approver, version=1, note=""):
        if not (approver or "").strip():
            raise ValueError("审批人不能为空")
        if not (item_id or "").strip():
            raise ValueError("item_id 不能为空")
        rec = {"item_id": item_id, "approver": approver.strip(),
               "content_sha256": sha_of(content), "version": int(version),
               "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note}
        self.store.append(self.log, rec)
        return rec

    def latest(self, item_id):
        recs, _bad = self.store.read_log(self.log)
        hit = [r for r in recs if r.get("item_id") == item_id]
        if not hit:
            return None
        return sorted(hit, key=lambda r: r.get("approved_at", ""))[-1]

    def check(self, item_id, content, version=1, expected_approver=None):
        """返回 (ok, reason)。reason 直接给人看。

        expected_approver：当前责任人。传了就必须与批文人一致。
        否则会出现「离职员工上周批的字，今天仍然能放行」——
        哈希和版本都对得上，但批的人已经无权代表这个岗位了。
        """
        rec = self.latest(item_id)
        if rec is None:
            return False, "未审批：%s 没有审批记录" % item_id
        if expected_approver is not None:
            if not str(expected_approver).strip():
                return False, "当前责任人为空，无法确认批文人是否有权"
            if str(rec.get("approver", "")).strip() != str(expected_approver).strip():
                return False, ("批文人不是当前责任人：审批由「%s」完成，当前责任人是「%s」，"
                               "需由现任重新审批" % (rec.get("approver"), expected_approver))
        if int(rec.get("version", 0)) != int(version):
            return False, "版本不符：审批 version=%s，当前 version=%s，需重新审批" % (rec.get("version"), version)
        if rec.get("content_sha256") != sha_of(content):
            return False, "内容已改动：审批哈希 %s… 与当前 %s… 不一致" % (
                str(rec.get("content_sha256"))[:12], sha_of(content)[:12])
        return True, "已审批 by %s" % rec.get("approver")

    def require(self, item_id, content, version=1, expected_approver=None):
        ok, reason = self.check(item_id, content, version, expected_approver)
        if not ok:
            raise PermissionError(reason)
        return True

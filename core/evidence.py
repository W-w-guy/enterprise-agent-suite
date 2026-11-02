# -*- coding: utf-8 -*-
"""证据引用：每条结论都要能指回来源，指不回去就算未验证。

这是 5 个 slice 共用的硬约束：知识图谱要 evidence、经营数据要 source、
连接器要 provenance、看板要 run_id、试点要 audit。指不回来源的结论
一律进 unresolved，不进正文。
"""
class EvidenceError(ValueError):
    pass

class Evidence:
    def __init__(self, docs=None):
        # docs: {doc_id: {"title":..., "uri":..., "sha256":...}}
        self.docs = dict(docs or {})

    def add(self, doc_id, uri="", title="", sha256=""):
        if not (doc_id or "").strip():
            raise EvidenceError("doc_id 不能为空")
        self.docs[doc_id] = {"uri": str(uri), "title": str(title), "sha256": str(sha256)}
        return doc_id

    def resolve(self, refs):
        """返回 (found, missing)。missing 里的引用是因为不存在，不是空字符串。"""
        found, missing = [], []
        for r in refs or []:
            r = str(r or "").strip()
            if not r:
                missing.append("")
            elif r in self.docs:
                found.append(r)
            else:
                missing.append(r)
        return found, missing

    def check(self, refs):
        found, missing = self.resolve(refs)
        if missing:
            raise EvidenceError("证据引用无效：%s" % ", ".join(m for m in missing if m) or "空引用")
        return found

    def cite(self, refs):
        parts = []
        for r in self.check(refs):
            d = self.docs[r]
            parts.append("[%s]" % (d.get("title") or r))
        return " ".join(parts)

class Claim:
    """一条结论：kind 必须是 fact / calc / advice 之一。

    经营数据 Agent 的核心口径：事实、计算、建议必须分开且可标。
    """
    KINDS = ("fact", "calc", "advice")

    def __init__(self, text, kind, refs=None, formula=""):
        if kind not in self.KINDS:
            raise EvidenceError("kind 必须是 %s，收到 %r" % ("/".join(self.KINDS), kind))
        self.text = str(text)
        self.kind = kind
        self.refs = list(refs or [])
        self.formula = formula

    def as_dict(self):
        return {"text": self.text, "kind": self.kind, "refs": self.refs, "formula": self.formula}

    def render(self):
        tag = {"fact": "事实", "calc": "计算", "advice": "建议"}[self.kind]
        tail = ""
        if self.refs:
            tail += " " + " ".join("[%s]" % r for r in self.refs)
        if self.formula:
            tail += " (%s)" % self.formula
        return "- 【%s】%s%s" % (tag, self.text, tail)

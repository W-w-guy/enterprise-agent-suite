# -*- coding: utf-8 -*-
"""原子写 + 追加式台账。所有 slice 的状态落盘都走这里。

设计口径：
1) 任何落盘都不能出现「半个文件」——先写 .tmp 再 os.replace（同盘原子）。
2) 台账一律追加（append-only），每行一个 JSON；崩在中间只会少一行，不会写坏前文。
3) 读坏行不整段抛：坏行进 orphans，好行继续用。
"""
import json, os, tempfile

class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def path(self, name):
        return os.path.join(self.root, name)

    def write_json(self, name, obj):
        p = self.path(name)
        d = os.path.dirname(p)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return p

    def read_json(self, name, default=None):
        p = self.path(name)
        if not os.path.isfile(p):
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def append(self, name, record):
        p = self.path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        return p

    def read_log(self, name):
        """返回 (records, orphans)。坏行不抛，进 orphans（带行号）。"""
        p = self.path(name)
        recs, bad = [], []
        if not os.path.isfile(p):
            return recs, bad
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    bad.append({"line": i, "raw": line[:120]})
                    continue
                if not isinstance(obj, dict):
                    bad.append({"line": i, "raw": line[:120]})
                    continue
                recs.append(obj)
        return recs, bad

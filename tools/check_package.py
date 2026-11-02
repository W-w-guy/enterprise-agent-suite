#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包前检查（我方用）。回答一个问题：这个目录能不能原样打给客户。

与 tools/verify.py 的分工：
  verify.py      客户拿到包后跑，验「行为对不对」
  check_package  我方打包前跑，验「包里有没有不该有的东西」
缓存/产物这类副产物只有打包前才该判，客户跑完测试必然生成 __pycache__。
"""
import json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD_DIRS = {"__pycache__", ".pytest_cache", "out"}
SKIP_DIRS = {".git"}   # 仓库元数据，克隆后必然存在，不算交付缺陷
BAD_EXT = (".pyc", ".pyo", ".zip", ".bak")

def main():
    fails, warns = [], []
    present = set()
    for dp, dns, fns in os.walk(HERE):
        keep = []
        for d in dns:
            if d in SKIP_DIRS:
                continue
            if d in BAD_DIRS:
                fails.append("不该有的目录: " + os.path.relpath(os.path.join(dp, d), HERE))
            else:
                keep.append(d)
        dns[:] = keep
        for f in fns:
            rel = os.path.relpath(os.path.join(dp, f), HERE).replace(os.sep, "/")
            present.add(rel)
            if f.endswith(BAD_EXT):
                fails.append("不该有的文件: " + rel)
            if f == "MANIFEST.json":
                continue
    man_p = os.path.join(HERE, "MANIFEST.json")
    if not os.path.isfile(man_p):
        fails.append("缺 MANIFEST.json")
    else:
        with open(man_p, encoding="utf-8") as fh:
            man = json.load(fh)
        listed = {i["path"] for i in man["files"]}
        for rel in sorted(present):
            if rel == "MANIFEST.json":
                continue
            if rel not in listed:
                fails.append("MANIFEST 未覆盖: " + rel)
        for rel in sorted(listed - present):
            fails.append("MANIFEST 有、磁盘上缺: " + rel)
        if man["file_count"] != len(man["files"]):
            fails.append("file_count 与实际条目不一致")
        if man["total_bytes"] != sum(i["bytes"] for i in man["files"]):
            fails.append("total_bytes 与条目字节数之和不一致")
    print("=" * 60)
    for x in fails:
        print("[FAIL]", x)
    for x in warns:
        print("[WARN]", x)
    print("=" * 60)
    print("打包检查：%d 个文件，%d 项失败" % (len(present), len(fails)))
    return 0 if not fails else 1

if __name__ == "__main__":
    sys.exit(main())

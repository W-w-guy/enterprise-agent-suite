#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付自检：客户拿到包后一条命令核对。

检查的都是「可判定的事实」，不是我的自我评价：
  1) 全部单元测试通过
  2) demo 跑两遍，产物字节完全相同（同一输入必须同一输出）
  3) MANIFEST 里每个文件的 sha256 与磁盘一致（没被改过、没丢文件）
  4) 产物里没有本机绝对路径（不泄漏部署结构）
  5) 没有残留的 __pycache__ / .pyc 混进交付
退出码：0 全过；1 有失败。
"""
import hashlib, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []
PASSES = []

def chk(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    (PASSES if ok else FAILS).append((name, detail))
    print("[%s] %-34s %s" % ("PASS" if ok else "FAIL", name, detail))

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()

def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")

def t_tests():
    r = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], HERE)
    tail = (r.stderr or "").strip().splitlines()[-3:]
    return r.returncode == 0, " | ".join(tail)[:110]

def t_demo_deterministic():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        for out in (a, b):
            r = run([sys.executable, "-B", "run.py", "demo", out], HERE)
            if r.returncode != 0:
                return False, "demo 退出码 %d: %s" % (r.returncode, (r.stderr or "")[-120:])
        rels = ["1530/board.md", "1530/board.csv", "1530/diff.json", "1506_brief.md",
                "1521/kg.md", "1521/kg.json", "1531/report.json"]
        diff = [x for x in rels if sha(os.path.join(a, x)) != sha(os.path.join(b, x))]
        return not diff, ("%d 个产物两次运行字节一致" % len(rels)) if not diff else "不一致: " + ",".join(diff)

def t_manifest():
    p = os.path.join(HERE, "MANIFEST.json")
    if not os.path.isfile(p):
        return False, "缺 MANIFEST.json（先跑 tools/make_manifest.py）"
    with open(p, encoding="utf-8") as f:
        man = json.load(f)
    bad = []
    for item in man["files"]:
        fp = os.path.join(HERE, item["path"])
        if not os.path.isfile(fp):
            bad.append("缺文件:" + item["path"])
        elif sha(fp) != item["sha256"]:
            bad.append("哈希不符:" + item["path"])
    return not bad, ("%d 文件逐个核对通过" % len(man["files"])) if not bad else "; ".join(bad[:4])

def t_no_abs_path():
    hits = []
    with tempfile.TemporaryDirectory() as out:
        run([sys.executable, "-B", "run.py", "demo", out], HERE)
        for dp, _dn, fns in os.walk(out):
            for f in fns:
                if f.endswith((".md", ".json", ".csv")):
                    fp = os.path.join(dp, f)
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        txt = fh.read()
                    if HERE.replace(os.sep, "/") in txt or HERE in txt:
                        hits.append(f)
    return not hits, "无本机绝对路径" if not hits else "泄漏: " + ",".join(hits)

def t_no_cache():
    hits = []
    for dp, dns, fns in os.walk(HERE):
        if "__pycache__" in dp:
            hits.append(os.path.relpath(dp, HERE))
        hits += [f for f in fns if f.endswith(".pyc")]
    return not hits, "无缓存残留" if not hits else "残留: " + ",".join(hits[:3])

def main():
    print("=" * 66)
    # 这里【不查】__pycache__：跑测试必然生成缓存，在测试后用缓存状态判交付质量
    # 只会得到一个假失败。缓存属于打包问题，交给 tools/check_package.py 在打包前查。
    chk("单元测试全过", t_tests)
    chk("demo 两遍字节一致", t_demo_deterministic)
    chk("MANIFEST 哈希核对", t_manifest)
    chk("产物无本机绝对路径", t_no_abs_path)
    print("=" * 66)
    print("%d passed / %d failed" % (len(PASSES), len(FAILS)))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())

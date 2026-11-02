#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 MANIFEST.json：每个文件的 sha256 + 字节数。交付前跑一次。"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "out", ".pytest_cache"}

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def walk(root):
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for f in sorted(fns):
            if f == "MANIFEST.json" or f.endswith(".pyc"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            files.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p)})
    return files

def main():
    files = walk(HERE)
    man = {"root": os.path.basename(HERE), "file_count": len(files),
           "total_bytes": sum(f["bytes"] for f in files), "files": files}
    out = os.path.join(HERE, "MANIFEST.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print("MANIFEST.json: %d 文件 / %d 字节" % (man["file_count"], man["total_bytes"]))
    return 0

if __name__ == "__main__":
    sys.exit(main())

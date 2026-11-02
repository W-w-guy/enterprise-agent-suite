#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理打包残留：__pycache__ / *.pyc / out/。打包前必跑。"""
import os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
removed = []

def rm(path):
    try:
        os.remove(path); removed.append(path)
    except OSError:
        pass

def main():
    for dp, dns, fns in os.walk(HERE, topdown=False):
        for f in fns:
            if f.endswith(".pyc"):
                rm(os.path.join(dp, f))
        for d in list(dns):
            if d == "__pycache__":
                full = os.path.join(dp, d)
                for f in os.listdir(full):
                    rm(os.path.join(full, f))
                try:
                    os.rmdir(full); removed.append(full)
                except OSError:
                    pass
    print("清理 %d 项" % len(removed))
    return 0

if __name__ == "__main__":
    sys.exit(main())

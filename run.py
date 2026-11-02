#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业 Agent 系统 · 5 个切片统一入口。

用法：
  python3 run.py demo [outdir]           # 用自带样例跑全部 5 个切片
  python3 run.py 1530 board <expdir> <base> <outdir>
  python3 run.py 1506 brief <metrics.csv> <values.csv> <metric> <period> <prev> <prev_year> <out.md> [num:den]
  python3 run.py 1521 kg <triples.csv> <docs.csv> <center> <outdir>
  python3 run.py 1519 sync <docs.json> <outdir>
  python3 run.py 1531 pilot <rows.json> <policy.json> <outdir> [--no-approve] [--tamper]

退出码：0 正常；1 发现显著回归（1530）；2 输入不可用。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from slices import s1530_eval_board as s1530
from slices import s1506_bizdata as s1506
from slices import s1521_kg as s1521
from slices import s1519_connector as s1519
from slices import s1531_pilot as s1531

DATA = os.path.join(HERE, "data")

def demo(outdir="out"):
    out = os.path.join(HERE, outdir)
    rc = 0
    print("== 1530 评测看板")
    rc |= s1530.main([os.path.join(DATA, "experiments"), "g0", os.path.join(out, "1530")])
    print("\n== 1506 经营数据简报")
    rc |= s1506.main([os.path.join(DATA, "metric_catalog.csv"), os.path.join(DATA, "values.csv"),
                      "gross_profit", "2026-08", "2026-07", "2025-08",
                      os.path.join(out, "1506_brief.md"), "gross_profit:revenue"])
    print("\n== 1521 知识图谱")
    rc |= s1521.main([os.path.join(DATA, "triples.csv"), os.path.join(DATA, "docs.csv"),
                      "订单系统", os.path.join(out, "1521")])
    print("\n== 1519 连接器同步")
    rc |= s1519.main([os.path.join(DATA, "remote_docs.json"), os.path.join(out, "1519")])
    print("\n== 1531 非提案试点")
    r = s1531.run(os.path.join(DATA, "expenses.json"), os.path.join(DATA, "policy.json"),
                  os.path.join(out, "1531"))
    print(s1531.render(r))
    return rc

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ or "see header")
        return 0
    if argv[0] == "demo":
        return demo(argv[1] if len(argv) > 1 else "out")
    which, cmd, rest = argv[0], argv[1], argv[2:]
    if which == "1530":
        return s1530.main(rest)
    if which == "1506":
        return s1506.main(rest)
    if which == "1521":
        return s1521.main(rest)
    if which == "1519":
        return s1519.main(rest)
    if which == "1531":
        flags = [a for a in rest if a.startswith("--")]
        pos = [a for a in rest if not a.startswith("--")]
        data_p, policy_p, outdir = pos[:3]
        return 0 if s1531.run(data_p, policy_p, outdir,
                              approve="--no-approve" not in flags,
                              tamper="--tamper" in flags) else 1
    print("未知切片：%s" % which)
    return 2

if __name__ == "__main__":
    # 路径写错时给一句人话 + 退出码 2，而不是把 FileNotFoundError 的 traceback 甩给客户。
    try:
        sys.exit(main(sys.argv[1:]))
    except FileNotFoundError as e:
        print("输入文件不存在：%s" % (e.filename or e))
        print("用法见上方说明；示例数据在 data/ 目录。")
        sys.exit(2)
    except IndexError:
        print("参数不足。用法见：python3 run.py -h")
        sys.exit(2)

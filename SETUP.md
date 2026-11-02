# 使用说明

## 环境
Python 3.9 及以上，零第三方依赖。Windows / macOS / Linux 均可。

## 三分钟
    unzip enterprise-agent-suite.zip
    cd enterprise-agent-suite
    python3 run.py demo out
    python3 tools/verify.py

第二条命令会把四个切片的产物写到 `out/`；第三条跑交付自检（109 项测试 + 字节一致性 + 清单核对）。

## 单独跑某一单

    43/50：python3 run.py 1530 board data/experiments g0 out/1530
    34/50：python3 run.py 1521 kg data/triples.csv data/docs.csv 订单系统 out/1521
    19/50：python3 run.py 1506 brief data/metric_catalog.csv data/values.csv \
                 gross_profit 2026-08 2026-07 2025-08 out/brief.md gross_profit:revenue
    44/50：python3 run.py 1531 pilot data/expenses.json data/policy.json out/1531
    32/50：python3 run.py 1519 sync data/remote_docs.json out/1519

## 换自己的数据
- 看板：把 `experiments/*.jsonl` 换成你的实验结果，改 `s1506` 里的 `PRICES` 价目表
- 图谱：三元组每行必须有 `doc_id` 与 `quote`，否则不入图（这是故意的）
- 经营数据：先在 `metric_catalog.csv` 登记口径，未登记的指标不分析
- 试点：改 `policy.json` 的阈值、责任人、队列名
- 连接器：实现自己的 `transport.fetch(provider, cursor)`，其余四步不用改

## 退出码
0 正常｜1 看板发现显著回归｜2 输入不可用（如中心实体不在图中）

## 遇到问题
先跑 `python3 tools/verify.py`。4 项里哪项红，就是哪层坏了。

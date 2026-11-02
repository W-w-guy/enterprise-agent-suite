# -*- coding: utf-8 -*-
"""确定性报表：同样的输入必须产出同样的字节。

两条纪律：
1) 所有表/行都显式排序，不依赖 dict 顺序或文件系统顺序。
2) 数值格式化统一走这里，不在各处 f-string，避免 1.0 与 1 混排。
"""
import csv, io

def md_table(headers, rows):
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    w = [len(str(h)) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            w[i] = max(w[i], len(c))
    def line(cells):
        return "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(cells)) + " |"
    out = [line([str(h) for h in headers]),
           "|" + "|".join("-" * (w[i] + 2) for i in range(len(w))) + "|"]
    out += [line(r) for r in rows]
    return "\n".join(out)

def csv_text(headers, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if c is None else c for c in r])
    return buf.getvalue()

def pct(x, places=1):
    return ("%." + str(places) + "f%%") % (100.0 * float(x))

def num(x, places=2):
    return ("%." + str(places) + "f") % float(x)

def stable_rows(rows, key=None):
    return sorted(rows, key=key if key is not None else (lambda r: [str(c) for c in r]))


def write_text(path, text):
    """写文本，强制 utf-8 + LF，且一定关闭句柄（Windows 上不关会被占用）。"""
    import os
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def write_json(path, obj):
    import json
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

def read_text(path):
    """按常见编码依次尝试。

    中文 Windows 上用记事本保存 JSON/CSV 默认是 GBK（gb18030 超集），
    直接按 utf-8 打开会抛 UnicodeDecodeError —— 对客户来说就是「你的程序崩了」，
    而实际只是编码不同。这不是宽容，是必须。
    """
    last = None
    for enc in ENCODINGS:
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError as e:
            last = e
        except FileNotFoundError:
            raise
    # 三种编码都不行：给出能看懂的原因，而不是让 UnicodeDecodeError 冒到顶层
    raise ValueError("%s 的编码无法识别（已试 utf-8-sig/utf-8/gb18030）：%s"
                     % (os.path.basename(path), last))

def read_json(path, default=None):
    import json, os
    if not os.path.isfile(path):
        return default
    raw = read_text(path)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ValueError("%s 不是合法 JSON：%s（已按 utf-8-sig/utf-8/gb18030 依次尝试读取）"
                         % (os.path.basename(path), e))

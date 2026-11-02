# -*- coding: utf-8 -*-
"""成本口径：钱一律用「整数微美元」算，禁止浮点累加。

踩过的坑：0.1+0.2 在报表里变成 0.30000000000000004，客户拿去对账对不上。
这里所有金额都换成 micro_usd（1e-6 美元）整数，只在最后展示时除。
"""
from decimal import Decimal, ROUND_HALF_UP

class MoneyError(ValueError):
    pass

def to_micro(usd):
    """'0.0021' / 0.0021 / Decimal -> 整数微美元。按 half-up 到 1e-6。"""
    d = usd if isinstance(usd, Decimal) else Decimal(str(usd))
    micro = (d * 1000000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(micro)

def to_usd(micro):
    return Decimal(int(micro)) / Decimal(1000000)

def fmt_usd(micro, places=4):
    q = Decimal(1).scaleb(-places)
    return str((to_usd(micro)).quantize(q, rounding=ROUND_HALF_UP))

def sum_micro(values):
    return sum(int(v) for v in values)

def cost_of(tokens_in, tokens_out, price_in_per_mtok, price_out_per_mtok):
    """按每百万 token 单价算成本，返回微美元整数。

    tokens 必须是整数；单价用字符串/Decimal 传，避免二进制浮点。
    """
    for name, v in (("tokens_in", tokens_in), ("tokens_out", tokens_out)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise MoneyError("%s 必须是非负整数，收到 %r" % (name, v))
    pin = price_in_per_mtok if isinstance(price_in_per_mtok, Decimal) else Decimal(str(price_in_per_mtok))
    pout = price_out_per_mtok if isinstance(price_out_per_mtok, Decimal) else Decimal(str(price_out_per_mtok))
    if pin < 0 or pout < 0:
        raise MoneyError("单价不能为负")
    micro = (Decimal(tokens_in) * pin + Decimal(tokens_out) * pout)
    return int(micro.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def wilson(ok, n, z=1.96):
    """Wilson 区间。分组样本量不同时直接比通过率会骗人，必须给区间。"""
    if n <= 0:
        return (0.0, 0.0, 0.0)
    if ok < 0 or ok > n:
        raise MoneyError("ok 必须在 [0, n] 内")
    p = ok / float(n)
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    lo = max(0.0, (c - r) / d)
    hi = min(1.0, (c + r) / d)
    return (p, lo, hi)

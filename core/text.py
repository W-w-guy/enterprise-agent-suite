# -*- coding: utf-8 -*-
"""文本清洗：控制字符、不可见字符、超长截断、脱敏。

客户数据来自 Excel / 复制粘贴 / 旧系统导出，最常见的脏东西是 \x00 \x07 \ufeff
和全角空格；这些进了正文会让人以为程序崩了。统一在这里清。
"""
import re, unicodedata

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ZERO_WIDTH = re.compile(u"[\u200b\u200c\u200d\u2060\ufeff]")
SPACES = re.compile(u"[ \u00a0\u3000\t]+")

def clean(s, keep_newline=False):
    if s is None:
        return ""
    s = str(s)
    s = ZERO_WIDTH.sub("", s)
    if keep_newline:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = CTRL.sub("", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
    else:
        s = CTRL.sub("", s)
        s = SPACES.sub(" ", s).strip()
    return s

def norm_key(s):
    """做去重键：NFKC + 去空白 + 小写。中文标点统一。"""
    s = unicodedata.normalize("NFKC", clean(s))
    s = re.sub(r"\s+", "", s)
    return s.lower()

def truncate(s, limit, note="…"):
    s = clean(s)
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    if limit <= len(note):
        return s[:limit]
    return s[: limit - len(note)] + note

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")

def _mask_email(m):
    """只露首字符：a@bc.com -> a***@bc.com；单字符邮箱不要变成 a@***@bc.com。"""
    local, _, domain = m.group(0).partition("@")
    return (local[:1] if local else "") + "***@" + domain

def redact(s):
    s = EMAIL.sub(_mask_email, str(s))
    s = PHONE.sub(lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:], s)
    return s

def split_list(s):
    """'保温;保温,防锈;' -> ['保温','防锈']：去重保序，去空。"""
    if s is None:
        return []
    parts = re.split(u"[;,；，、|/]", str(s))
    seen, out = set(), []
    for p in parts:
        p = clean(p)
        if not p:
            continue
        k = norm_key(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out

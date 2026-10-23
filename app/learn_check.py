# -*- coding: utf-8 -*-
"""术语自动学习 + 译文质量检查。

设计目标：**不依赖 LLM**，所以免费引擎（Bing/Google）也能用。

自动学习原理
------------
术语表里的词已经在翻译时被替换成中文了，所以不能直接扫译文找术语。
换个思路 —— 用「双向证据」：

1. 从**原文**里挑候选：连续 1~3 个英文单词、首字母大写或全大写、
   长度合适、出现次数 ≥2、不在停用词表里。
2. 在**译文**里找它对应的中文：查现有术语表有没有；没有就标记为「待确认」，
   把原文和上下文一起给你看，你填中文即可。

这样即使引擎是机器翻译，也能积累出「这个词在这份文档里频繁出现」的清单，
比 LLM 抽取弱一些，但零成本、离线可用。

质量检查原理
------------
翻完后扫译文，找几类可疑信号：
- 整段还是英文（可能漏翻）
- 数字/单位在原文与译文里对不上（可能翻错）
- 出现乱码般的控制字符
- 连续重复同一句（可能引擎抽风）
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- 停用词
_STOP = set("""
a an the and or but if then else when while for with without within into onto
of in on at by to from as is are was were be been being do does did done
this that these those it its it's their there here which who whom whose what
can could may might must shall should will would have has had not no nor
all any both each few more most other some such only own same so than too
very just also about above after again against because before below between
during through under until up down out off over
figure figures table tables page pages note notes see refer shown show
typical min max typ
""".split())

# 常见但没必要进术语表的词（数据手册里高频）
_NOISE = set("""
voltage current temperature package pin pins part number order ordering
information features applications description general absolute maximum
ratings electrical characteristics typical performance curves
""".split())


def _is_candidate(w: str) -> bool:
    if not w or len(w) < 3 or len(w) > 28:
        return False
    if w.lower() in _STOP or w.lower() in _NOISE:
        return False
    if w.isdigit():
        return False
    # 排除纯符号 / 纯数字混排
    if not re.search(r"[A-Za-z]", w):
        return False
    return True


# 词组里不该作为「术语开头/结尾」的碎片词
_FRAGMENT = set("""
step down up high low fast slow soft hard over under on off in out
the a an and or of for with from to by at as is are
""".split())


def extract_candidates(text: str, top: int = 60) -> list[tuple[str, int]]:
    """从原文里挑候选术语。

    关注这几类（工程文档里最可能是术语的）：
      · 全大写缩写（PWM、ESR、MOSFET）
      · 首字母大写的词组（Buck Converter）
      · 带数字的型号词（SGM6130）

    实测调优点：单纯按「首字母大写」抓会得到 "Step"、"Down Converter"
    这类碎片（来自 "Step-Down Converter" 断词），所以做了两处过滤：
      · 单词候选必须是常见缩写样式（全大写或含数字）
      · 词组的两端不能是 down/up/step 这类方向词
    """
    counts: Counter = Counter()

    # 1) 全大写缩写（2~8 个字符）—— PWM、ESR、MOSFET
    for m in re.finditer(r"\b[A-Z][A-Z0-9]{1,7}\b", text):
        w = m.group(0)
        if _is_candidate(w) and not w.isdigit():
            counts[w] += 1

    # 2) 型号样式（字母+数字）—— SGM6130、B340A
    for m in re.finditer(r"\b[A-Z]{2,}[0-9]{2,}[A-Z0-9]*\b", text):
        w = m.group(0)
        if _is_candidate(w):
            counts[w] += 1

    # 3) 首字母大写的 2~3 词词组 —— Buck Converter
    for m in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,2}\b", text):
        w = m.group(0)
        if not _is_candidate(w):
            continue
        parts = w.split()
        # 两端不能是方向/碎片词
        if parts[0].lower() in _FRAGMENT or parts[-1].lower() in _FRAGMENT:
            continue
        counts[w] += 1

    # 按 次数 × 词长 排序：长词更有信息量
    ranked = sorted(counts.items(), key=lambda x: -(x[1] * (1 + len(x[0]) / 10.0)))
    return [(w, c) for w, c in ranked if c >= 2][:top]


def _glossary_map(csv_path: str | Path | None = None) -> dict:
    """读术语表成 {小写英文: 中文}。"""
    import csv as _csv
    m: dict = {}
    p = Path(csv_path) if csv_path else (Path(__file__).resolve().parent / "glossary.csv")
    try:
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in _csv.DictReader(fh):
                s = (row.get("source") or "").strip()
                t = (row.get("target") or "").strip()
                if s and t:
                    m.setdefault(s.lower(), t)
    except Exception:
        pass
    return m


def _suggest_from_translation(term: str, src_text: str, dst_text: str) -> str:
    """从译文里猜这个术语的中文。

    思路：找到术语在原文中的位置 → 取它前后一小段英文 →
    在译文里找同一段（靠相邻的专有名词/数字定位）→ 截出对应中文。

    做不到精确对齐时退回空字符串（宁可留空，不要给错的中文）。
    """
    if not term or not dst_text:
        return ""
    # 用术语前后出现的「独一无二的锚点」（型号、数字）来定位
    m = re.search(re.escape(term), src_text)
    if not m:
        return ""
    # 取术语后面 80 字符里的数字/型号做锚
    tail = src_text[m.end(): m.end() + 120]
    anchors = re.findall(r"\b[A-Z]{2,}\d*\b|\b\d+(?:\.\d+)?\b", tail)
    for a in anchors[:4]:
        if a.upper() == term.upper() or len(a) < 2:
            continue
        i = dst_text.find(a)
        if i < 0:
            continue
        # 锚点前 20 字符里的中文，很可能就是这个术语的译法
        seg = dst_text[max(0, i - 24): i]
        zh = re.findall(r"[\u4e00-\u9fff]{2,8}", seg)
        if zh:
            return zh[-1]
    return ""


def learn_from_pdf(src_pdf: str | Path, existing: set[str],
                   top: int = 60, glossary: dict | None = None,
                   translated_pdf: str | Path | None = None) -> list[dict]:
    """从 PDF 提取候选术语，并尽量给出中文建议。

    中文来源（按可靠性排序）：
      1. 术语表里已有的 → 直接回填（准确）
      2. 从译文 PDF 对齐推断 → 可能准，留给你确认
      3. 都没有 → 留空，等你手填

    返回 [{term, count, context, suggestion, source}]，
    界面把 suggestion 填进「中文」列，你只需确认或修改。
    """
    import fitz

    doc = fitz.open(str(src_pdf))
    full = []
    for pg in doc:
        full.append(pg.get_text() or "")
    doc.close()
    text = "\n".join(full)

    dst_text = ""
    if translated_pdf:
        try:
            d2 = fitz.open(str(translated_pdf))
            dst_text = "\n".join((pg.get_text() or "") for pg in d2)
            d2.close()
        except Exception:
            dst_text = ""

    gmap = glossary if glossary is not None else _glossary_map()
    cands = extract_candidates(text, top=top)
    low_existing = {s.lower() for s in existing}
    out = []
    for w, c in cands:
        if w.lower() in low_existing:
            continue
        ctx = ""
        i = text.find(w)
        if i >= 0:
            ctx = re.sub(r"\s+", " ", text[max(0, i - 50): i + len(w) + 50]).strip()

        sug, src_kind = "", ""
        # 1) 术语表里有
        if w.lower() in gmap:
            sug, src_kind = gmap[w.lower()], "术语表"
        else:
            # 2) 试着从译文推断
            s2 = _suggest_from_translation(w, text, dst_text)
            if s2:
                sug, src_kind = s2, "译文推断"
        out.append({"term": w, "count": c, "context": ctx,
                    "suggestion": sug, "source": src_kind})
    return out


def suggest_translation(term: str, engine: str = "bing") -> str:
    """用免费引擎翻一个词，作为中文建议（可选功能）。

    只在用户点「自动翻译建议」时调用，避免每次学习都发网络请求。
    """
    try:
        import urllib.parse
        import urllib.request

        q = urllib.parse.quote(term)
        url = ("https://cn.bing.com/ttranslatev3?isVertical=1&&IG=1&IID=translator"
               "&fromLang=en&to=zh-Hans&text=" + q)
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://cn.bing.com/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read().decode("utf-8", "replace")
        m = re.search(r'"translations":\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"', data)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 质量检查
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def check_quality(src_pdf: str | Path, out_pdf: str | Path) -> list[dict]:
    """对比原文与译文，给出可疑点清单。

    返回 [{kind, detail, page}]，kind 取值：
        untranslated  整页几乎没变（可能漏翻）
        english_left  中文版里仍有成句英文
        number_mismatch 数字数量对不上
        control_char  含非法控制字符
        duplicate     连续重复段落
    """
    import fitz

    issues: list[dict] = []
    try:
        a = fitz.open(str(src_pdf))
        b = fitz.open(str(out_pdf))
    except Exception as exc:
        return [{"kind": "error", "detail": str(exc), "page": 0}]

    n = min(len(a), len(b))
    for i in range(n):
        ta = a[i].get_text() or ""
        tb = b[i].get_text() or ""

        # 1) 控制字符
        if _CTRL.search(tb):
            issues.append({"kind": "control_char",
                           "detail": "第 %d 页译文含非法控制字符" % (i + 1),
                           "page": i + 1})

        # 2) 漏翻：原页有较多文字，译文与原文高度相似且几乎没有中文
        zh = sum(1 for c in tb if "\u4e00" <= c <= "\u9fff")
        if len(ta.strip()) > 200 and zh < 5:
            issues.append({"kind": "untranslated",
                           "detail": "第 %d 页几乎没有中文（可能漏翻）" % (i + 1),
                           "page": i + 1})
            continue

        # 3) 成句英文残留
        if zh > 20:
            for m in re.finditer(r"(?:[A-Za-z]{2,}\s+){5,}[A-Za-z]{2,}", tb):
                s = m.group(0).strip()
                if len(s) > 40:
                    issues.append({"kind": "english_left",
                                   "detail": "第 %d 页残留英文句：%s" % (i + 1, s[:60]),
                                   "page": i + 1})
                    break

        # 4) 数字数量差异太大
        na = len(re.findall(r"\d+(?:\.\d+)?", ta))
        nb = len(re.findall(r"\d+(?:\.\d+)?", tb))
        if na >= 8 and nb < na * 0.5:
            issues.append({"kind": "number_mismatch",
                           "detail": "第 %d 页数字个数 %d -> %d，可能丢失" % (i + 1, na, nb),
                           "page": i + 1})

        # 5) 连续重复段落
        lines = [l.strip() for l in tb.splitlines() if len(l.strip()) > 12]
        for k in range(len(lines) - 2):
            if lines[k] == lines[k + 1] == lines[k + 2]:
                issues.append({"kind": "duplicate",
                               "detail": "第 %d 页有连续重复段落" % (i + 1),
                               "page": i + 1})
                break

    a.close()
    b.close()
    return issues

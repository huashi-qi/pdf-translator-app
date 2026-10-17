# -*- coding: utf-8 -*-
"""翻译质检与辅助工具。

包含几项独立能力：
  1. 术语命中报告 —— 术语表到底有没有起作用，哪些词从没用上
  2. 术语表自检 —— 重复条目、同词多译、空的译法
  3. 扫描版检测 —— 提醒用户这份 PDF 没有文字层
  4. 译法一致性 —— 同一英文词在译文不同页出现不同中文时给出提示
  5. 文本对照抽取 —— 把原文/译文按段落对齐，供逐段核对

都用 PyMuPDF，不引入新依赖。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")


# ---------------------------------------------------------------- 1. 命中报告
def format_term_report(term_hits: dict, total_terms: int = 0,
                       limit: int = 25) -> list:
    """把术语命中统计整理成可打印的几行。"""
    if not term_hits:
        return ["本次没有命中任何术语" + ("（术语表共 %d 条）" % total_terms
                                          if total_terms else "")]
    items = sorted(term_hits.items(), key=lambda x: -x[1])
    out = ["本次命中 %d 个术语，总计 %d 次：" % (
        len(items), sum(term_hits.values()))]
    for k, v in items[:limit]:
        out.append("   %-22s %3d 次" % (k[:22], v))
    if len(items) > limit:
        out.append("   …还有 %d 个" % (len(items) - limit))
    if total_terms:
        unused = total_terms - len(items)
        if unused > 0:
            out.append("   （术语表共 %d 条，本次用上 %d 条）" % (total_terms, len(items)))
    return out


# ---------------------------------------------------------------- 2. 术语表自检
def audit_glossary(csv_path: str | Path) -> dict:
    """检查术语表本身的问题。"""
    import csv as _csv

    csv_path = Path(csv_path)
    dup: list = []
    multi: dict = defaultdict(set)
    empty: list = []
    too_short: list = []
    rows = []

    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            for r in _csv.DictReader(fh):
                s = (r.get("source") or "").strip()
                t = (r.get("target") or "").strip()
                if not s and not t:
                    continue
                if not s or not t:
                    empty.append(s or t)
                    continue
                rows.append((s, t))
    except Exception as exc:
        return {"error": str(exc), "total": 0}

    seen = {}
    for s, t in rows:
        low = s.lower()
        if low in seen:
            if seen[low] != t:
                dup.append((s, seen[low], t))
        else:
            seen[low] = t
        multi[low].add(t)
        if len(s) <= 2 and not s.isupper():
            too_short.append(s)

    conflicts = [(s, sorted(v)) for s, v in multi.items() if len(v) > 1]
    return {
        "total": len(rows),
        "duplicate": dup,           # 同一英文，出现多次且译法不同
        "conflicts": conflicts,     # 同一英文有多个译法
        "empty": empty,             # 缺一边
        "too_short": too_short[:20],  # 过短的词容易误替换
    }


# ---------------------------------------------------------------- 3. 扫描版检测
def detect_scanned(pdf_path: str | Path, sample: int = 8) -> dict:
    """判断 PDF 是不是扫描件（没有文字层）。

    做法：抽几页看文字量。文字很少但图片不少 → 大概率是扫描件。
    """
    import fitz

    pdf_path = Path(pdf_path)
    try:
        with fitz.open(str(pdf_path)) as d:
            n = len(d)
            take = min(sample, n)
            chars = 0
            imgs = 0
            for i in range(take):
                try:
                    chars += len(d[i].get_text() or "")
                    imgs += len(d[i].get_images())
                except Exception:
                    pass
            per_page = chars / max(take, 1)
            return {
                "pages": n,
                "chars_per_page": int(per_page),
                "images_sampled": imgs,
                "scanned": per_page < 80 and imgs > 0,
                "definitely_text": per_page >= 200,
            }
    except Exception as exc:
        return {"error": str(exc), "scanned": False, "pages": 0}


# ---------------------------------------------------------------- 4. 译法一致性
def check_consistency(src_pdf: str | Path, out_pdf: str | Path,
                      max_terms: int = 12) -> list:
    """检查同一英文词在译文里是否译法不一。

    做法：原 PDF 里挑出现多次的大写缩写/型号，看看译文对应位置附近的中文
    是否一致 —— 由于没法精确对齐，这里退一步：
    统计译文里出现的「似乎是缩写残留」和「重复的短中文片段」，给出提示。
    """
    import fitz

    issues: list = []
    try:
        with fitz.open(str(out_pdf)) as d:
            text = "\n".join((pg.get_text() or "") for pg in d)
    except Exception as exc:
        return [{"kind": "error", "detail": str(exc)}]

    # 译文里残留的连续大写缩写（可能是没翻的术语）
    leftovers = Counter()
    for m in re.finditer(r"\b[A-Z]{2,8}\b", text):
        w = m.group(0)
        if w in ("PDF", "OK", "ID", "USB", "CPU", "LED", "LCD", "STM", "ARM"):
            continue
        leftovers[w] += 1
    for w, c in leftovers.most_common(max_terms):
        if c >= 3:
            issues.append({"kind": "leftover_abbr",
                           "detail": "译文里 %s 出现 %d 次（未翻译的缩写）" % (w, c)})

    # 重复出现的中文短句（可能引擎抽风）
    seg = Counter()
    for m in re.finditer(r"[\u4e00-\u9fff]{6,20}", text):
        seg[m.group(0)] += 1
    for s, c in seg.most_common(6):
        if c >= 8:
            issues.append({"kind": "repeat_zh",
                           "detail": "「%s」重复 %d 次" % (s[:16], c)})
    return issues


# ---------------------------------------------------------------- 6. 目录页识别
def detect_toc_pages(pdf_path: str | Path, max_check: int = 30) -> list:
    """识别目录页。

    目录页的特点是「一行一条 + 点线 + 页码」，而 babeldoc 会把这种结构
    合并成一个段落（实测 STM32 的 71 行变成 10 行），点线和页码全丢。

    识别出来之后用户可以：
      · 跳过这些页不翻（保留原始目录，页码仍准确）
      · 或者接受合并结果

    判定依据（累计打分，≥5 分算目录页）：
      · 点线出现 ≥3 次              +3
      · 行尾「点线+数字」≥3 次       +3
      · 有 Contents / 目录 标题     +3
      · 编号条目 ≥8 个              +2
      · 85% 以上是短行且行数 >15    +1
    """
    import fitz

    hits: list = []
    try:
        with fitz.open(str(pdf_path)) as d:
            for i in range(min(len(d), max_check)):
                try:
                    t = d[i].get_text() or ""
                except Exception:
                    continue
                if not t.strip():
                    continue
                score = 0
                dots = len(re.findall(r"\.{4,}", t))
                if dots >= 3:
                    score += 3
                tails = len(re.findall(r"\.{2,}\s*\d+\s*$", t, re.M))
                if tails >= 3:
                    score += 3
                if re.search(r"^(Contents|Table of Contents|目录|CONTENTS)\s*$",
                             t, re.M | re.I):
                    score += 3
                nums = len(re.findall(r"^\s*\d+(?:\.\d+)*\s+\S", t, re.M))
                if nums >= 8:
                    score += 2
                lines = [l.strip() for l in t.splitlines() if l.strip()]
                if lines:
                    short = sum(1 for l in lines if len(l) < 90)
                    if short / len(lines) > 0.85 and len(lines) > 15:
                        score += 1
                if score >= 5:
                    hits.append({"page": i + 1, "score": score, "dots": dots,
                                 "tails": tails, "lines": len(lines)})
    except Exception:
        pass
    return hits


def toc_pages_spec(pdf_path: str | Path, max_check: int = 30) -> str:
    """把识别出的目录页拼成 --pages 能用的表达式，如 "2,3,4"。"""
    hits = detect_toc_pages(pdf_path, max_check)
    return ",".join(str(h["page"]) for h in hits)


# ---------------------------------------------------------------- 5. 段落对照
def extract_pairs(src_pdf: str | Path, out_pdf: str | Path,
                  page: int = 1, max_pairs: int = 200) -> list:
    """抽取某一页的「原文段落 / 译文段落」配对，供逐段核对。

    按阅读顺序取块，两边各取第 N 块做配对（版面差异下做不到精确，
    但按顺序对齐对校对已经够用）。
    """
    import fitz

    def _blocks(doc, pno):
        try:
            d = doc[pno].get_text("dict")
        except Exception:
            return []
        out = []
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            txt = " ".join(
                "".join(s.get("text", "") for s in ln.get("spans", []))
                for ln in b.get("lines", [])
            )
            txt = re.sub(r"\s+", " ", txt).strip()
            if len(txt) >= 2:
                out.append(txt)
        return out

    pairs: list = []
    try:
        a = fitz.open(str(src_pdf))
        b = fitz.open(str(out_pdf))
        n = min(len(a), len(b))
        pno = max(0, min(n - 1, page - 1))
        left = _blocks(a, pno)
        right = _blocks(b, pno)
        for i in range(min(len(left), len(right), max_pairs)):
            pairs.append({"n": i + 1, "src": left[i], "dst": right[i]})
        a.close()
        b.close()
    except Exception:
        pass
    return pairs

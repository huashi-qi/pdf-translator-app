# -*- coding: utf-8 -*-
"""PDF 编辑：合并 / 拆分 / 提取页 / 删除页 / 旋转 / 压缩。

只用 PyMuPDF，不引入新依赖（保证包体积）。
"""
from __future__ import annotations

from pathlib import Path

import fitz


def _parse_pages(spec: str, total: int) -> list[int]:
    """把 "1-3,5,8-" 解析成 0 基页号列表。"""
    out: list[int] = []
    spec = (spec or "").strip()
    if not spec:
        return list(range(total))
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start = int(a) if a.strip() else 1
                end = int(b) if b.strip() else total
            except ValueError:
                continue
            start = max(1, min(total, start))
            end = max(1, min(total, end))
            if start <= end:
                out.extend(range(start - 1, end))
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if 1 <= n <= total:
                out.append(n - 1)
    # 去重保序
    seen = set()
    uniq = []
    for i in out:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def merge_pdfs(files: list, dst: str | Path) -> dict:
    """把多个 PDF 合成一个。"""
    dst = Path(dst)
    out = fitz.open()
    n = 0
    for f in files:
        try:
            with fitz.open(str(f)) as d:
                out.insert_pdf(d)
                n += len(d)
        except Exception:
            continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(dst), deflate=True, garbage=3)
    out.close()
    return {"pages": n, "files": len(files), "size_kb": round(dst.stat().st_size / 1024)}


def extract_pages(src: str | Path, dst: str | Path, spec: str) -> dict:
    """按页号范围提取出新的 PDF。"""
    src, dst = Path(src), Path(dst)
    with fitz.open(str(src)) as d:
        idx = _parse_pages(spec, len(d))
        out = fitz.open()
        for i in idx:
            out.insert_pdf(d, from_page=i, to_page=i)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dst), deflate=True, garbage=3)
        out.close()
        return {"kept": len(idx), "total": len(d),
                "size_kb": round(dst.stat().st_size / 1024)}


def delete_pages(src: str | Path, dst: str | Path, spec: str) -> dict:
    """删掉指定页，剩下的另存。"""
    src, dst = Path(src), Path(dst)
    with fitz.open(str(src)) as d:
        total = len(d)
        kill = set(_parse_pages(spec, total))
        out = fitz.open()
        for i in range(total):
            if i not in kill:
                out.insert_pdf(d, from_page=i, to_page=i)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dst), deflate=True, garbage=3)
        out.close()
        return {"removed": len(kill), "left": total - len(kill), "total": total,
                "size_kb": round(dst.stat().st_size / 1024)}


def rotate_pages(src: str | Path, dst: str | Path, spec: str, angle: int) -> dict:
    """把指定页旋转 90/180/270 度。"""
    src, dst = Path(src), Path(dst)
    angle = int(angle) % 360
    with fitz.open(str(src)) as d:
        idx = _parse_pages(spec, len(d)) if spec.strip() else list(range(len(d)))
        for i in idx:
            d[i].set_rotation((d[i].rotation + angle) % 360)
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
        return {"rotated": len(idx), "angle": angle,
                "size_kb": round(dst.stat().st_size / 1024)}


def compress_pdf(src: str | Path, dst: str | Path, level: str = "medium") -> dict:
    """压缩 PDF。

    三级：
      light  —— 只重新整理对象（无损，通常小 5~15%）
      medium —— 再加图片降采样到 150dpi（肉眼几乎无差）
      strong —— 图片降到 96dpi + 强压缩（体积最小，画质有损）
    """
    src, dst = Path(src), Path(dst)
    cfg = {"light": (None, 85), "medium": (150, 70), "strong": (96, 50)}
    dpi, quality = cfg.get(level, cfg["medium"])
    before = src.stat().st_size

    with fitz.open(str(src)) as d:
        if dpi:
            for page in d:
                for img in page.get_images(full=True):
                    try:
                        xref = img[0]
                        pix = fitz.Pixmap(d, xref)
                        if pix.n - pix.alpha > 3:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        # 按目标 dpi 缩放
                        w_in = page.rect.width / 72.0
                        target_w = max(1, int(w_in * dpi))
                        if pix.width > target_w:
                            ratio = target_w / float(pix.width)
                            pix = fitz.Pixmap(
                                pix, int(pix.width * ratio), int(pix.height * ratio),
                                None)
                        jpg = pix.tobytes("jpeg", jpg_quality=quality)
                        page.replace_image(xref, stream=jpg)
                        pix = None
                    except Exception:
                        continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=4, clean=True)

    after = dst.stat().st_size
    saved = max(0, before - after)
    return {"before_kb": round(before / 1024), "after_kb": round(after / 1024),
            "saved_pct": round(saved * 100.0 / max(before, 1), 1), "level": level}


def pdf_info(src: str | Path) -> dict:
    """读一个 PDF 的基本信息，给编辑界面做预检。"""
    src = Path(src)
    with fitz.open(str(src)) as d:
        meta = d.metadata or {}
        encrypted = bool(d.is_encrypted)
        # 文本层占比，判断是不是扫描件
        sample = min(5, len(d))
        chars = 0
        for i in range(sample):
            try:
                chars += len(d[i].get_text() or "")
            except Exception:
                pass
        return {
            "pages": len(d),
            "size_kb": round(src.stat().st_size / 1024),
            "title": (meta.get("title") or "").strip(),
            "encrypted": encrypted,
            "has_text": chars > 50,
            "chars_sampled": chars,
        }


def _font_for(text: str) -> str:
    """按内容选字体。

    PyMuPDF 内置的 helv（Helvetica）**画不出中文** —— 之前加页码/
    水印时用的是 helv，结果报告成功但页面上什么都没有。
    含中文时改用内置的 china-s（简体中文字体）。
    """
    for ch in text or "":
        if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" \
                or "\uff00" <= ch <= "\uffef":
            return "china-s"
    return "helv"


def add_page_numbers(src: str | Path, dst: str | Path, fmt: str = "{n} / {total}",
                     position: str = "bottom-center", size: int = 9) -> dict:
    """给 PDF 加页码。fmt 支持 {n}（当前页）和 {total}（总页数）。"""
    src, dst = Path(src), Path(dst)
    with fitz.open(str(src)) as d:
        total = len(d)
        for i, page in enumerate(d):
            txt = (fmt or "{n}").replace("{n}", str(i + 1)).replace("{total}", str(total))
            w, h = page.rect.width, page.rect.height
            margin = 30
            y = margin if position.startswith("top") else h - margin
            if position.endswith("left"):
                x, align = margin, 0
            elif position.endswith("right"):
                x, align = w - margin, 2
            else:
                x, align = w / 2, 1
            rect = fitz.Rect(x - 130, y - size - 3, x + 130, y + size + 3)
            try:
                page.insert_textbox(rect, txt, fontsize=size,
                                    fontname=_font_for(txt),
                                    align=align, color=(0.25, 0.25, 0.25))
            except Exception:
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
    return {"pages": total, "size_kb": round(dst.stat().st_size / 1024)}


def add_watermark(src: str | Path, dst: str | Path, text: str,
                  size: int = 40, opacity: float = 0.13,
                  angle: int = 45) -> dict:
    """加斜向文字水印（如「内部资料」「草稿」）。

    实现说明：PyMuPDF 的 insert_text(rotate=...) 只接受 90 的整数倍，
    传 45 会报 "bad rotate value"（之前水印一直加不上就是这个原因）。
    想任意角度得用 morph 参数配一个旋转矩阵。
    """
    import math

    src, dst = Path(src), Path(dst)
    n = 0
    with fitz.open(str(src)) as d:
        for page in d:
            try:
                w, h = page.rect.width, page.rect.height
                # 以页面中心附近为基准点
                px, py = w * 0.5, h * 0.5
                rad = math.radians(float(angle) % 360)
                # 旋转矩阵（fitz.Matrix 支持旋转）
                mat = fitz.Matrix(1, 1).prerotate(float(angle) % 360)
                page.insert_text(
                    fitz.Point(px - w * 0.22, py + h * 0.06),
                    text, fontsize=size, fontname=_font_for(text),
                    color=(0.55, 0.55, 0.55),
                    fill_opacity=max(0.05, min(0.6, opacity)),
                    morph=(fitz.Point(px, py), mat))
                n += 1
            except Exception:
                # 退路：不加角度，至少能画上去
                try:
                    page.insert_text(
                        fitz.Point(page.rect.width * 0.15, page.rect.height * 0.62),
                        text, fontsize=size, fontname=_font_for(text),
                        color=(0.55, 0.55, 0.55),
                        fill_opacity=max(0.05, min(0.6, opacity)))
                    n += 1
                except Exception:
                    continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
    return {"pages": n, "text": text, "angle": angle,
            "size_kb": round(dst.stat().st_size / 1024)}


def insert_text(src: str | Path, dst: str | Path, text: str,
                page_no: int = 1, x: float = 72, y: float = 72,
                size: int = 12, color: str = "black") -> dict:
    """在指定页插入一段文字（坐标 pt，原点在左上角）。"""
    src, dst = Path(src), Path(dst)
    colors = {"黑色": (0, 0, 0), "红色": (0.8, 0, 0),
              "蓝色": (0, 0, 0.8), "灰色": (0.4, 0.4, 0.4)}
    with fitz.open(str(src)) as d:
        idx = max(0, min(len(d) - 1, int(page_no) - 1))
        d[idx].insert_text(fitz.Point(float(x), float(y)), text,
                           fontsize=size, fontname=_font_for(text),
                           color=colors.get(color, (0, 0, 0)))
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
    return {"page": idx + 1, "text": text[:30], "size_kb": round(dst.stat().st_size / 1024)}


def insert_image(src: str | Path, dst: str | Path, image: str | Path,
                 page_no: int = 1, x: float = 72, y: float = 72,
                 width: float = 200) -> dict:
    """在指定页插入一张图片（按宽度等比缩放）。"""
    src, dst, image = Path(src), Path(dst), Path(image)
    with fitz.open(str(src)) as d:
        idx = max(0, min(len(d) - 1, int(page_no) - 1))
        rect = fitz.Rect(float(x), float(y), float(x) + float(width),
                         float(y) + float(width))
        d[idx].insert_image(rect, filename=str(image), keep_proportion=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
    return {"page": idx + 1, "size_kb": round(dst.stat().st_size / 1024)}


def set_metadata(src: str | Path, dst: str | Path, title: str = "",
                 author: str = "", subject: str = "") -> dict:
    """改 PDF 的标题 / 作者 / 主题（阅读器里显示的信息）。"""
    src, dst = Path(src), Path(dst)
    with fitz.open(str(src)) as d:
        md = d.metadata or {}
        if title:
            md["title"] = title
        if author:
            md["author"] = author
        if subject:
            md["subject"] = subject
        d.set_metadata(md)
        dst.parent.mkdir(parents=True, exist_ok=True)
        d.save(str(dst), deflate=True, garbage=3)
    return {"title": title or (md.get("title") or ""),
            "size_kb": round(dst.stat().st_size / 1024)}


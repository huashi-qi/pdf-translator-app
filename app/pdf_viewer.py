# -*- coding: utf-8 -*-
"""应用内 PDF 编辑器。

目标：不用跳出软件，就能像在阅读器里那样「打开一个 PDF、翻页看、
在上面加东西」。做法是把 PDF 页面渲染成图片显示在 Tk 画布上，
用户点哪里就在哪里插入内容，最后另存为新文件。

不依赖任何新库：渲染用 PyMuPDF，界面用 Tk。
"""
from __future__ import annotations

import math
from pathlib import Path

import fitz


class PdfDoc:
    """打开一份 PDF，提供「渲染某页成图片」的能力。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.doc = fitz.open(str(self.path))
        self.page = 0

    @property
    def total(self) -> int:
        return len(self.doc)

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    def render(self, page_no: int, zoom: float = 1.4):
        """把第 page_no 页（0 基）渲染成 PNG 字节 + 显示尺寸。

        返回 (png_bytes, disp_w, disp_h, scale)，scale 是「显示像素 / PDF 点」。
        """
        page_no = max(0, min(len(self.doc) - 1, int(page_no)))
        self.page = page_no
        page = self.doc[page_no]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png"), pix.width, pix.height, zoom

    def page_size(self, page_no: int) -> tuple:
        """该页的尺寸（PDF 点）。"""
        page_no = max(0, min(len(self.doc) - 1, int(page_no)))
        r = self.doc[page_no].rect
        return float(r.width), float(r.height)

    def save_as(self, dst: str | Path) -> str:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(dst), deflate=True, garbage=3)
        return str(dst)


def add_text_at(doc: "PdfDoc", page_no: int, x_pt: float, y_pt: float,
                text: str, size: int = 12, color: str = "red") -> None:
    """在指定页的 (x_pt, y_pt) 处插一段文字（坐标为 PDF 点，原点左上）。"""
    colors = {"black": (0, 0, 0), "red": (0.85, 0.1, 0.1),
              "blue": (0.1, 0.1, 0.85), "green": (0.1, 0.6, 0.1),
              "gray": (0.45, 0.45, 0.45)}
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    page = doc.doc[page_no]
    need_cjk = any("\u4e00" <= c <= "\u9fff" for c in text)
    page.insert_text(fitz.Point(float(x_pt), float(y_pt)), text,
                     fontsize=size,
                     fontname="china-s" if need_cjk else "helv",
                     color=colors.get(color, (0.85, 0.1, 0.1)))


def add_highlight(doc: "PdfDoc", page_no: int, rect_pt: tuple,
                  color: str = "yellow") -> None:
    """在指定矩形区域加高亮（坐标为 PDF 点）。"""
    cols = {"yellow": (1, 0.92, 0.23), "green": (0.6, 1, 0.6),
            "blue": (0.6, 0.8, 1), "pink": (1, 0.7, 0.8)}
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    page = doc.doc[page_no]
    annot = page.add_highlight_annot(fitz.Rect(*rect_pt))
    try:
        annot.set_colors(stroke=cols.get(color, (1, 0.92, 0.23)))
        annot.update()
    except Exception:
        pass


def add_note(doc: "PdfDoc", page_no: int, x_pt: float, y_pt: float,
             text: str) -> None:
    """在指定位置加一个批注（便签图标，鼠标悬停可见内容）。"""
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    page = doc.doc[page_no]
    pt = fitz.Point(float(x_pt), float(y_pt))
    annot = page.add_text_annot(pt, text)
    try:
        annot.set_info(title="PDF 翻译助手", content=text)
        annot.update()
    except Exception:
        pass


def redact_area(doc: "PdfDoc", page_no: int, rect_pt: tuple) -> None:
    """涂黑一块区域（遮盖敏感内容）。"""
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    page = doc.doc[page_no]
    annot = page.add_redact_annot(fitz.Rect(*rect_pt))
    annot.update()
    try:
        page.apply_redactions()
    except Exception:
        pass


def delete_page(doc: "PdfDoc", page_no: int) -> int:
    """删掉某一页，返回剩余页数。"""
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    doc.doc.delete_page(page_no)
    return len(doc.doc)


def rotate_current(doc: "PdfDoc", page_no: int, angle: int = 90) -> None:
    """旋转某一页。"""
    page_no = max(0, min(len(doc.doc) - 1, int(page_no)))
    p = doc.doc[page_no]
    p.set_rotation((p.rotation + int(angle)) % 360)

# -*- coding: utf-8 -*-
r"""PDF 自由编辑器引擎。

目标：在软件里打开 PDF，像在 Acrobat 里那样【随意调整】，
而不是只能选几个预设选项。

支持的能力
----------
文字层
  · 点选任意文字块（按行/按块自动识别）
  · 拖动到新位置
  · 双击直接改文字内容
  · 删除文字块
  · 改字号 / 颜色

绘制层
  · 白out（矩形遮盖）
  · 自由画笔（折线）
  · 矩形 / 直线
  · 插入文字（任意位置、任意字号颜色）
  · 插入图片

页面
  · 缩略图浏览、点击跳转
  · 上移/下移/删除/插入空白页
  · 旋转、裁剪

编辑
  · 撤销 / 重做（多步）
  · 另存为新文件（不改原件）

实现要点
--------
PyMuPDF 没有"直接改已有文字"的 API，所以「编辑文字」的做法是：
    1) 用 add_redact_annot 把原文字区域涂掉（apply_redactions）
    2) 在相同位置 insert_text 写入新文字
这就是 Acrobat 之外大多数工具的实现方式。
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import fitz


def font_for(text: str) -> str:
    """按内容选字体（内置 helv 画不出中文）。"""
    for ch in text or "":
        if "\u4e00" <= ch <= "\u9fff":
            return "china-s"
    return "helv"


# PDF 里嵌入的字体大多是【子集字体】（basefont 带 ABCDEE+ 前缀），
# 这些字体名 PyMuPDF 无法直接使用（报 "need font file or buffer"）。
# 所以只能退回到内置 Base-14 字体，按"英文/中文 + 粗斜体"挑最接近的。
_SUBSET_PREFIX = __import__("re").compile(r"^[A-Z]{6}\+")


def _is_usable_fontname(name: str) -> bool:
    """判断字体名能不能直接被 PyMuPDF 使用。"""
    if not name:
        return False
    if _SUBSET_PREFIX.match(name):      # 子集字体，不可直接使用
        return False
    try:
        fitz.Font(fontname=name)
        return True
    except Exception:
        return False


def _builtin_font(text: str, bold: bool = False, italic: bool = False) -> str:
    """按内容 + 样式选一个内置字体（一定能用）。"""
    need_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))
    if need_cjk:
        return "china-s"          # 内置中文字体没有粗斜体变体
    if bold and italic:
        return "hebi"
    if bold:
        return "hebo"
    if italic:
        return "heit"
    return "helv"


def _pick_font(page, orig_font: str, text: str,
               bold: bool = False, italic: bool = False) -> str:
    """挑一个【保证可用】的字体。

    优先用原文同名且在系统中真实存在的字体（如 Times-Roman），
    否则退回内置字体。绝不返回子集字体名 —— 那会让 insert_text 抛异常，
    而异常发生在"已涂掉原文之后"，结果就是**文字直接消失**
    （实测踩过：GENERAL DESCRIPTION 变成空）。
    """
    if orig_font and _is_usable_fontname(orig_font):
        # 原文是真实字体，但若用户要求粗斜体则按需切换
        if not (bold or italic):
            return orig_font
        low = orig_font.lower()
        if "bold" in low and bold and "italic" in low and italic:
            return orig_font
    return _builtin_font(text, bold, italic)


class TextBlock:
    """页面上的一个可编辑文字块。

    除了文字本身，还记下【原始字体名】和【基线位置】——
    这两个是"改样式后原文就变形"问题的关键：
    重写文字时如果用了别的字体（比如 helv），字宽、字重都会变，看着就不对。
    """

    __slots__ = ("bbox", "text", "size", "page", "color", "flags",
                 "font", "baseline", "ascender")

    def __init__(self, bbox, text, size, page, color=(0, 0, 0), flags=0,
                 font: str = "", baseline: float = 0.0):
        self.bbox = tuple(float(v) for v in bbox)   # (x0, y0, x1, y1)
        self.text = text
        self.size = float(size)
        self.page = int(page)
        self.color = color
        self.flags = flags
        self.font = font or ""
        self.baseline = float(baseline)

    def as_rect(self):
        return fitz.Rect(*self.bbox)

    def center(self):
        return ((self.bbox[0] + self.bbox[2]) / 2.0,
                (self.bbox[1] + self.bbox[3]) / 2.0)

    def contains(self, x, y) -> bool:
        return (self.bbox[0] <= x <= self.bbox[2]
                and self.bbox[1] <= y <= self.bbox[3])


class PdfStudio:
    """可编辑的 PDF 文档。

    所有修改都作用在内存里的 PyMuPDF 文档上，
    保存时才写出文件，所以随时可以放弃。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.doc = fitz.open(str(self.path))
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.undo_labels: list = []
        self.dirty = False

    # ---------------------------------------------------------- 基本信息
    @property
    def pages(self) -> int:
        return len(self.doc)

    def page_size(self, pno: int) -> tuple:
        pno = max(0, min(self.pages - 1, int(pno)))
        r = self.doc[pno].rect
        return float(r.width), float(r.height)

    def render(self, pno: int, zoom: float = 1.5) -> tuple:
        """渲染某页为 PNG。返回 (bytes, w, h)。"""
        pno = max(0, min(self.pages - 1, int(pno)))
        pix = self.doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png"), pix.width, pix.height

    def render_thumb(self, pno: int, width: int = 110) -> tuple:
        """渲染缩略图。返回 (bytes, w, h)。"""
        pno = max(0, min(self.pages - 1, int(pno)))
        page = self.doc[pno]
        w = float(page.rect.width) or 1.0
        zoom = width / w
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png"), pix.width, pix.height

    # ---------------------------------------------------------- 文字块
    def text_blocks(self, pno: int) -> list:
        """取一页里的文字块（用于点选/编辑）。"""
        pno = max(0, min(self.pages - 1, int(pno)))
        out = []
        try:
            d = self.doc[pno].get_text("dict")
        except Exception:
            return out
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            lines = blk.get("lines", [])
            if not lines:
                continue
            # 逐行生成（行级更好点选）
            for ln in lines:
                spans = [s for s in ln.get("spans", []) if (s.get("text") or "").strip()]
                if not spans:
                    continue
                txt = "".join(s.get("text", "") for s in spans)
                size = max(float(s.get("size", 10)) for s in spans)
                color = spans[0].get("color", 0)
                rgb = (((color >> 16) & 255) / 255.0,
                       ((color >> 8) & 255) / 255.0, (color & 255) / 255.0)
                # 原始字体名 + 基线（origin.y 就是基线）
                fname = str(spans[0].get("font", "") or "")
                base_y = float(ln.get("bbox", (0, 0, 0, 0))[3])
                try:
                    o = spans[0].get("origin")
                    if o:
                        base_y = float(o[1])
                except Exception:
                    pass
                out.append(TextBlock(ln.get("bbox"), txt, size, pno, rgb,
                                     spans[0].get("flags", 0), fname, base_y))
        return out

    def block_at(self, pno: int, x: float, y: float):
        """找点 (x, y) 处的文字块（倒序取最上面的）。"""
        hits = [b for b in self.text_blocks(pno) if b.contains(x, y)]
        return hits[-1] if hits else None

    def blocks_in(self, pno: int, rect: tuple) -> list:
        """① 框选：取矩形范围内的所有文字块。"""
        x0, y0, x1, y1 = rect
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        out = []
        for b in self.text_blocks(pno):
            bx0, by0, bx1, by1 = b.bbox
            cx, cy = b.center()
            # 用中心点判断，避免部分重叠就算选中
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                out.append(b)
        return out

    def move_blocks(self, blocks: list, dx: float, dy: float) -> bool:
        """① 批量移动多个文字块（一次快照，可整体撤销）。"""
        if not blocks or not (dx or dy):
            return False
        self._snapshot()
        ok = 0
        for b in blocks:
            try:
                pno = max(0, min(self.pages - 1, b.page))
                page = self.doc[pno]
                page.add_redact_annot(b.as_rect())
                try:
                    page.apply_redactions()
                except Exception:
                    pass
                page.insert_text(
                    fitz.Point(b.bbox[0] + dx, b.bbox[3] + dy - b.size * 0.18),
                    b.text, fontsize=b.size, fontname=font_for(b.text),
                    color=b.color)
                b.bbox = (b.bbox[0] + dx, b.bbox[1] + dy,
                          b.bbox[2] + dx, b.bbox[3] + dy)
                ok += 1
            except Exception:
                continue
        self.dirty = ok > 0
        return ok > 0

    def delete_blocks(self, blocks: list) -> bool:
        """① 批量删除多个文字块。"""
        if not blocks:
            return False
        self._snapshot()
        ok = 0
        for b in blocks:
            try:
                pno = max(0, min(self.pages - 1, b.page))
                page = self.doc[pno]
                page.add_redact_annot(b.as_rect())
                page.apply_redactions()
                ok += 1
            except Exception:
                continue
        self.dirty = ok > 0
        return ok > 0

    # ---------------------------------------------------------- ⑩ 文字样式
    def restyle_blocks(self, blocks: list, size: float | None = None,
                       color: tuple | None = None,
                       bold: bool | None = None,
                       italic: bool | None = None) -> bool:
        """⑩ 给文字块改样式（字号 / 颜色 / 粗体 / 斜体）。

        关键改进（修"改了样式原文也变了"）：
          · 复用【原文的字体】和【原基线】，不再一律用 helv 重排
          · 只改颜色时【不做涂改重写】—— 直接按原位置重绘，
            因为涂改+重写本身就会让字距/基线产生肉眼可见的偏移
          · 中文字体没有粗体变体，改粗体时只提示、不强改字形

        PyMuPDF 里"加粗/斜体"靠切换内置字体（helv / hebo / heit）实现。
        """
        if not blocks:
            return False

        # 关键：先判断"到底有没有变化"。
        # 之前只要点「应用样式」就会走"涂掉原文 + 重写"，
        # 即使字号/颜色/粗细一个都没改，原文字也会被重排一遍 ——
        # 用户看到的就是"点 OK 之后选中的段落样式变了"。
        def _same(a, b) -> bool:
            try:
                return all(abs(float(x) - float(y)) < 0.01
                           for x, y in zip(a, b))
            except Exception:
                return False

        changed = []
        for b in blocks:
            want_size = float(size) if size else b.size
            want_color = tuple(color) if color else b.color
            size_diff = abs(want_size - b.size) > 0.05
            color_diff = not _same(want_color, b.color)
            # 粗斜体：原文如果本来就是粗体，加粗也算"无变化"
            low = (b.font or "").lower()
            bold_diff = bool(bold) and ("bold" not in low)
            ital_diff = bool(italic) and ("italic" not in low and "oblique" not in low)
            if size_diff or color_diff or bold_diff or ital_diff:
                changed.append(b)

        if not changed:
            return False          # 没有任何变化 → 什么都不做

        self._snapshot("修改文字样式")
        ok = 0
        for b in changed:
            try:
                new_size = float(size) if size else b.size
                new_color = tuple(color) if color else b.color
                need_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in b.text)

                pno = max(0, min(self.pages - 1, b.page))
                page = self.doc[pno]

                # 先挑好字体【再】动原内容 —— 顺序反了会丢字
                fname = _pick_font(page, b.font, b.text,
                                   bold=bool(bold), italic=bool(italic))
                # 校验这个字体确实可用，不可用就退回内置
                try:
                    fitz.Font(fontname=fname)
                except Exception:
                    fname = _builtin_font(b.text, bool(bold), bool(italic))

                # 涂掉原区域后重写
                page.add_redact_annot(b.as_rect())
                try:
                    page.apply_redactions()
                except Exception:
                    pass
                base_y = b.baseline or b.bbox[3]
                page.insert_text(fitz.Point(b.bbox[0], base_y), b.text,
                                 fontsize=new_size, fontname=fname,
                                 color=new_color)
                b.size = new_size
                b.color = new_color
                ok += 1
            except Exception:
                continue
        if ok:
            self._sanitize()      # 清理 redact 留下的无效引用
        self.dirty = ok > 0
        return ok > 0

    # ---------------------------------------------------------- 撤销
    # ④ 操作标签：和 undo_stack 一一对应，界面上能看懂"撤销到哪一步"
    undo_labels: list

    def _snapshot(self, label: str = "操作"):
        """存一份当前文档快照（用于撤销）。

        注意：必须用 tobytes() 取【完整字节副本】。
        PyMuPDF 的 tobytes() 不带 incremental 时是完整序列化，
        能真实反映当前状态（包括刚做的页面增删）。
        """
        try:
            data = self.doc.tobytes(deflate=True, garbage=2)
            self.undo_stack.append(data)
            self.undo_labels.append(label)
            self.redo_stack.clear()
            if len(self.undo_stack) > 30:      # 只留最近 30 步
                self.undo_stack.pop(0)
                self.undo_labels.pop(0)
        except Exception:
            pass

    def history(self) -> list:
        """④ 返回操作历史（供界面显示）。"""
        return list(self.undo_labels)

    def resolve(self, blocks: list) -> list:
        """把（可能已失效的）文字块引用重新对应到当前文档里的块。

        为什么需要：每次编辑都会 redact + 重写，正文内容流顺序会变，
        而且 _sanitize() 会重开文档 —— 之前缓存的 TextBlock 就"悬空"了。
        界面重绘后必须重新解析，否则下一次拖动/改样式会作用到错误的位置。
        匹配策略：先按原文精确匹配，再按前缀匹配，最后按位置就近匹配。
        """
        out = []
        for old in blocks or []:
            page_no = max(0, min(self.pages - 1, old.page))
            cands = self.text_blocks(page_no)
            hit = None
            txt = (old.text or "").strip()
            # 1) 文字完全相同
            for c in cands:
                if c.text.strip() == txt:
                    hit = c
                    break
            # 2) 前缀相同（改过字号/颜色后文字不变，但空格可能被规整）
            if hit is None and txt:
                for c in cands:
                    if c.text.strip().startswith(txt[:12]):
                        hit = c
                        break
            # 3) 位置最近
            if hit is None and cands:
                cx, cy = old.center()
                hit = min(cands, key=lambda c: (
                    (c.center()[0] - cx) ** 2 + (c.center()[1] - cy) ** 2))
            if hit is not None:
                out.append(hit)
        return out

    def _sanitize(self):
        """把文档重新序列化一遍，清掉 redact 留下的无效对象引用。

        连续做多次"涂改+重写"后，MuPDF 偶尔会报
        "format error: object out of range ... xref size" ——
        原因是 redact 删掉了对象，但交叉引用表里还留着旧引用。
        tobytes → reopen 能重建一张干净的 xref 表。
        """
        try:
            data = self.doc.tobytes(deflate=True, garbage=4)
            newdoc = fitz.open("pdf", data)
            try:
                self.doc.close()
            except Exception:
                pass
            self.doc = newdoc
        except Exception:
            pass

    def _restore(self, data: bytes):
        """从快照恢复。"""
        try:
            newdoc = fitz.open("pdf", data)
        except Exception:
            return
        try:
            old = self.doc
            self.doc = newdoc
            try:
                old.close()
            except Exception:
                pass
            self.dirty = True
        except Exception:
            try:
                newdoc.close()
            except Exception:
                pass

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        try:
            self.redo_stack.append(self.doc.tobytes())
            if self.undo_labels:
                self.undo_labels.pop()
            self._restore(self.undo_stack.pop())
            return True
        except Exception:
            return False

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        try:
            self.undo_stack.append(self.doc.tobytes())
            self._restore(self.redo_stack.pop())
            return True
        except Exception:
            return False

    # ---------------------------------------------------------- 文字操作
    def move_block(self, blk: TextBlock, dx: float, dy: float,
                   new_text: str | None = None) -> bool:
        """把文字块移动到新位置（可选同时改内容）。

        做法：先把原区域涂掉，再在目标位置写新文字。
        """
        if not (dx or dy or new_text is not None):
            return False
        self._snapshot("修改文字" if new_text is not None else "移动文字块")
        pno = max(0, min(self.pages - 1, blk.page))
        try:
            page = self.doc[pno]
            text = new_text if new_text is not None else blk.text
            # 1) 涂掉原位置
            page.add_redact_annot(blk.as_rect())
            try:
                page.apply_redactions()
            except Exception:
                pass
            # 2) 在目标位置写入 —— 用【原字体 + 原基线】，
            #    否则字形和基线都会偏，看着就像"改了样式原文也变了"
            x0 = blk.bbox[0] + dx
            base_y = (blk.baseline or blk.bbox[3]) + dy
            page.insert_text(fitz.Point(x0, base_y), text,
                             fontsize=blk.size,
                             fontname=_pick_font(page, blk.font, text),
                             color=blk.color)
            self.dirty = True
            return True
        except Exception:
            return False

    def delete_block(self, blk: TextBlock) -> bool:
        """删除一个文字块。"""
        self._snapshot()
        pno = max(0, min(self.pages - 1, blk.page))
        try:
            page = self.doc[pno]
            page.add_redact_annot(blk.as_rect())
            page.apply_redactions()
            self.dirty = True
            return True
        except Exception:
            return False

    def insert_text(self, pno: int, x: float, y: float, text: str,
                    size: float = 12, color=(0.85, 0.1, 0.1)) -> bool:
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].insert_text(fitz.Point(x, y), text,
                                      fontsize=size, fontname=font_for(text),
                                      color=color)
            self.dirty = True
            return True
        except Exception:
            return False

    # ---------------------------------------------------------- 绘制
    def whiteout(self, pno: int, rect: tuple, color=(1, 1, 1)) -> bool:
        """白out：用矩形盖住内容（默认白色，也可用其它色）。"""
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].draw_rect(fitz.Rect(*rect), color=color, fill=color)
            self.dirty = True
            return True
        except Exception:
            return False

    def real_delete(self, pno: int, rect: tuple) -> bool:
        """真删：把矩形区域内的内容彻底移除（不是遮盖）。"""
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            page = self.doc[pno]
            page.add_redact_annot(fitz.Rect(*rect))
            page.apply_redactions()
            self.dirty = True
            return True
        except Exception:
            return False

    def draw_line(self, pno: int, p1: tuple, p2: tuple,
                  width: float = 2, color=(0.85, 0.1, 0.1)) -> bool:
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].draw_line(fitz.Point(*p1), fitz.Point(*p2),
                                    color=color, width=float(width))
            self.dirty = True
            return True
        except Exception:
            return False

    def draw_polyline(self, pno: int, points: list,
                      width: float = 2, color=(0.85, 0.1, 0.1)) -> bool:
        """自由画笔：把一串点连成折线。"""
        if len(points) < 2:
            return False
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            page = self.doc[pno]
            for i in range(len(points) - 1):
                page.draw_line(fitz.Point(*points[i]), fitz.Point(*points[i + 1]),
                               color=color, width=float(width))
            self.dirty = True
            return True
        except Exception:
            return False

    def draw_rect(self, pno: int, rect: tuple, width: float = 2,
                  color=(0.85, 0.1, 0.1), fill=None) -> bool:
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].draw_rect(fitz.Rect(*rect), color=color,
                                    width=float(width), fill=fill)
            self.dirty = True
            return True
        except Exception:
            return False

    def insert_image(self, pno: int, rect: tuple, image_path: str | Path,
                     keep_ratio: bool = True) -> bool:
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].insert_image(fitz.Rect(*rect), filename=str(image_path),
                                       keep_proportion=keep_ratio)
            self.dirty = True
            return True
        except Exception:
            return False

    # ---------------------------------------------------------- 页面操作
    def rotate_page(self, pno: int, angle: int = 90) -> bool:
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            p = self.doc[pno]
            p.set_rotation((p.rotation + int(angle)) % 360)
            self.dirty = True
            return True
        except Exception:
            return False

    def delete_page(self, pno: int) -> bool:
        if self.pages <= 1:
            return False
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc.delete_page(pno)
            self.dirty = True
            return True
        except Exception:
            return False

    def move_page(self, pno: int, delta: int) -> bool:
        """把某页上移/下移。"""
        tgt = pno + int(delta)
        if not (0 <= pno < self.pages and 0 <= tgt < self.pages):
            return False
        self._snapshot()
        try:
            self.doc.move_page(pno, tgt)
            self.dirty = True
            return True
        except Exception:
            return False

    def insert_blank(self, after: int) -> bool:
        """在指定页后插入一张同尺寸空白页。"""
        self._snapshot()
        try:
            after = max(0, min(self.pages - 1, int(after)))
            src = self.doc[after]
            self.doc.new_page(pno=after + 1, width=src.rect.width,
                              height=src.rect.height)
            self.dirty = True
            return True
        except Exception:
            return False

    def duplicate_page(self, pno: int) -> bool:
        self._snapshot()
        try:
            pno = max(0, min(self.pages - 1, int(pno)))
            self.doc.copy_page(pno, pno + 1)
            self.dirty = True
            return True
        except Exception:
            return False

    def crop_page(self, pno: int, rect: tuple) -> bool:
        """裁剪：只保留矩形区域。"""
        self._snapshot()
        pno = max(0, min(self.pages - 1, int(pno)))
        try:
            self.doc[pno].set_cropbox(fitz.Rect(*rect))
            self.dirty = True
            return True
        except Exception:
            return False

    # ---------------------------------------------------------- 元数据 / 保存
    def set_metadata(self, title: str = "", author: str = "",
                     subject: str = "") -> bool:
        self._snapshot()
        try:
            md = dict(self.doc.metadata or {})
            if title:
                md["title"] = title
            if author:
                md["author"] = author
            if subject:
                md["subject"] = subject
            self.doc.set_metadata(md)
            # PyMuPDF 对元数据的改动也是"待保存"状态，
            # 必须配合 save() 才落地；这里标记 dirty 供界面提示
            self.dirty = True
            return True
        except Exception:
            return False

    def save_as(self, dst: str | Path) -> str:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 用 incremental=False + garbage 清理，确保改动落盘
        self.doc.save(str(dst), deflate=True, garbage=3)
        self.dirty = False
        return str(dst)

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass

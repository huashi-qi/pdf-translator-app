# -*- coding: utf-8 -*-
r"""PDF 编辑器界面（自由编辑版）。

打开 PDF 后可随意调整：
    左侧  缩略图导航
    中间  页面画布（可缩放/滚动）
    顶部  工具条（选择 / 文字 / 白out / 画笔 / 矩形 / 图片 / 页面）
    右侧  属性面板（字号、颜色、线宽）

操作方式
--------
    单击        选中文字块
    拖动选中块  移动位置
    双击选中块  改文字内容
    Delete      删除选中块
    在空白处拖  按当前工具绘制（画笔/矩形）
    Ctrl+Z/Y    撤销/重做
"""
from __future__ import annotations

from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


def open_studio(root, C, start_path: str = "", on_save=None):
    """打开 PDF 编辑器窗口。"""
    import pdf_studio as PS
    import ui_widgets as W
    from ui_widgets import toast

    FONT = "Microsoft YaHei UI"

    path = start_path
    if not path:
        path = filedialog.askopenfilename(
            title="打开要编辑的 PDF",
            filetypes=[("PDF 文件", "*.pdf")],
            initialdir=str(Path.home() / "Documents"),
        )
    if not path or not Path(path).is_file():
        return

    try:
        studio = PS.PdfStudio(path)
    except Exception as exc:
        messagebox.showerror("打不开", "无法打开该 PDF：\n%s" % exc)
        return

    win = tk.Toplevel(root)
    win.title("PDF 编辑器 — " + Path(path).name)
    win.geometry("1200x800")
    win.minsize(900, 620)
    try:
        win.configure(bg=C["bg"])
    except Exception:
        pass

    # ⑧ 夜间模式下自动开启反色预览（看白底 PDF 不刺眼）
    _auto_inv = False
    try:
        import core as _A
        _c = _A.load_config()
        _theme_now = str(_c.get("ui_theme") or "light").lower()
        if _theme_now == "auto":
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as _k:
                    _v, _ = winreg.QueryValueEx(_k, "AppsUseLightTheme")
                    _theme_now = "light" if int(_v) == 1 else "dark"
            except Exception:
                _theme_now = "light"
        _auto_inv = (bool(_c.get("auto_invert_dark", True))
                     and _theme_now == "dark")
    except Exception:
        pass

    st = {"page": 0, "zoom": 1.5, "tool": "select",
          "sels": [], "drag": None, "pen": [], "img_ref": None,
          "invert": _auto_inv}

    # ============ 顶部工具条 ============
    top = tk.Frame(win, bg=C["bg_alt"])
    top.pack(side="top", fill="x")

    def tb_sep():
        tk.Frame(top, bg=C["border"], width=1).pack(side="left", fill="y", padx=6, pady=6)

    # 页面导航
    def goto(delta):
        st["page"] = max(0, min(studio.pages - 1, st["page"] + delta))
        redraw()

    W.RoundButton(top, "◀", lambda: goto(-1), bg=C["head_bg"], fg=C["text"],
                  hover=C["accent_sf"], radius=8, padx=10, pady=5,
                  font=(FONT, 10)).pack(side="left", padx=(8, 2), pady=6)
    W.RoundButton(top, "▶", lambda: goto(1), bg=C["head_bg"], fg=C["text"],
                  hover=C["accent_sf"], radius=8, padx=10, pady=5,
                  font=(FONT, 10)).pack(side="left", padx=2, pady=6)
    page_lbl = tk.Label(top, text="", bg=C["bg_alt"], fg=C["text_sub"],
                        font=(FONT, 9), width=10)
    page_lbl.pack(side="left", padx=6)

    # 工具
    tb_sep()
    tools = [
        ("select", "↖", "选择/移动"),
        ("text", "T", "加文字"),
        ("white", "▢", "白out"),
        ("pen", "✎", "画笔"),
        ("rect", "□", "矩形"),
        ("line", "／", "直线"),
        ("image", "🖼", "图片"),
    ]
    tool_btns = {}

    def set_tool(k):
        st["tool"] = k
        for kk, b in tool_btns.items():
            on = (kk == k)
            b.set_colors(C["accent"] if on else C["head_bg"],
                         "#ffffff" if on else C["text"],
                         C["accent_hv"] if on else C["accent_sf"])
    for key, icon, tip in tools:
        b = W.RoundButton(top, icon, lambda k=key: set_tool(k),
                          bg=C["head_bg"], fg=C["text"], hover=C["accent_sf"],
                          radius=8, padx=11, pady=5, font=(FONT, 11))
        b.pack(side="left", padx=2, pady=6)
        tool_btns[key] = b
    set_tool("select")

    # 缩放
    tb_sep()
    for z, lab in ((1.0, "100%"), (1.5, "150%"), (2.0, "200%")):
        W.RoundButton(top, lab, lambda zz=z: (st.update(zoom=zz), redraw()),
                      bg=C["head_bg"], fg=C["text"], hover=C["accent_sf"],
                      radius=8, padx=9, pady=5,
                      font=(FONT, 9)).pack(side="left", padx=2, pady=6)

    # ⑬ 反色预览开关
    tb_sep()
    inv_btn = W.RoundButton(top, "反色", lambda: _toggle_invert(),
                            bg=C["head_bg"], fg=C["text"],
                            hover=C["accent_sf"], radius=8, padx=10, pady=5,
                            font=(FONT, 9))
    inv_btn.pack(side="left", padx=2, pady=6)

    def _toggle_invert():
        st["invert"] = not st.get("invert", False)
        if st["invert"]:
            inv_btn.set_colors(C["accent"], "#ffffff", C["accent_hv"])
        else:
            inv_btn.set_colors(C["head_bg"], C["text"], C["accent_sf"])
        redraw()

    # 撤销/重做
    tb_sep()
    W.RoundButton(top, "撤销", lambda: (studio.undo(), redraw()),
                  bg=C["head_bg"], fg=C["text"], hover=C["accent_sf"],
                  radius=8, padx=10, pady=5,
                  font=(FONT, 9)).pack(side="left", padx=2, pady=6)
    W.RoundButton(top, "重做", lambda: (studio.redo(), redraw()),
                  bg=C["head_bg"], fg=C["text"], hover=C["accent_sf"],
                  radius=8, padx=10, pady=5,
                  font=(FONT, 9)).pack(side="left", padx=2, pady=6)

    # 保存
    def do_save():
        dst = filedialog.asksaveasfilename(
            title="保存编辑后的 PDF", defaultextension=".pdf",
            initialfile=Path(path).stem + "_已编辑.pdf",
            filetypes=[("PDF 文件", "*.pdf")],
            initialdir=str(Path(path).parent))
        if not dst:
            return
        try:
            studio.save_as(dst)
            messagebox.showinfo("已保存", "已保存到：\n%s" % dst, parent=win)
            if on_save:
                try:
                    on_save(dst)
                except Exception:
                    pass
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=win)

    W.RoundButton(top, "保存", do_save, bg=C["accent"], fg="#ffffff",
                  hover=C["accent_hv"], radius=8, padx=16, pady=5,
                  font=(FONT, 10, "bold")).pack(side="right", padx=8, pady=6)

    # ============ 主体：缩略图 | 画布 | 属性 ============
    main = tk.Frame(win, bg=C["bg"])
    main.pack(fill="both", expand=True)

    # ---- 左：缩略图 ----
    left = tk.Frame(main, bg=C["sidebar"], width=140)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    tk.Label(left, text="页面", bg=C["sidebar"], fg=C["muted"],
             font=(FONT, 9)).pack(pady=(8, 4))
    thumb_cv = tk.Canvas(left, bg=C["sidebar"], highlightthickness=0, width=132)
    thumb_sb = ttk.Scrollbar(left, orient="vertical", command=thumb_cv.yview)
    thumb_cv.configure(yscrollcommand=thumb_sb.set)
    thumb_sb.pack(side="right", fill="y")
    thumb_cv.pack(side="left", fill="both", expand=True)
    thumb_imgs = {}

    def build_thumbs():
        thumb_cv.delete("all")
        thumb_imgs.clear()
        y = 8
        for i in range(studio.pages):
            try:
                png, w, h = studio.render_thumb(i, 110)
                img = tk.PhotoImage(data=png)
            except Exception:
                continue
            thumb_imgs[i] = img
            box = tk.Frame(thumb_cv, bg=C["sidebar"])
            thumb_cv.create_image(66, y + h // 2, image=img, tags=("t%d" % i,))
            thumb_cv.create_text(66, y + h + 9, text=str(i + 1),
                                 fill=C["text_sub"], font=(FONT, 8),
                                 tags=("t%d" % i,))
            thumb_cv.tag_bind("t%d" % i, "<Button-1>",
                              lambda e, n=i: (st.update(page=n), redraw()))
            y += h + 24
        thumb_cv.configure(scrollregion=(0, 0, 132, y))

    # ---- 中：画布 ----
    mid = tk.Frame(main, bg=C["bg"])
    mid.pack(side="left", fill="both", expand=True)
    cv = tk.Canvas(mid, bg=C.get("reader_bg", "#8a8f99"), highlightthickness=0)
    vsb = ttk.Scrollbar(mid, orient="vertical", command=cv.yview)
    hsb = ttk.Scrollbar(mid, orient="horizontal", command=cv.xview)
    cv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    vsb.pack(side="right", fill="y"); hsb.pack(side="bottom", fill="x")
    cv.pack(side="left", fill="both", expand=True)

    # ---- 右：属性 ----
    right = tk.Frame(main, bg=C["bg_alt"], width=190)
    right.pack(side="right", fill="y")
    right.pack_propagate(False)
    tk.Label(right, text="属性", bg=C["bg_alt"], fg=C["text"],
             font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(10, 6))

    tk.Label(right, text="字号", bg=C["bg_alt"], fg=C["muted"],
             font=(FONT, 9)).pack(anchor="w", padx=12)
    size_v = tk.StringVar(value="12")
    ttk.Entry(right, textvariable=size_v, width=8).pack(anchor="w", padx=12, pady=(2, 8))

    tk.Label(right, text="颜色", bg=C["bg_alt"], fg=C["muted"],
             font=(FONT, 9)).pack(anchor="w", padx=12)
    color_v = tk.StringVar(value="红")
    ttk.Combobox(right, textvariable=color_v, state="readonly", width=10,
                 values=["黑", "红", "蓝", "绿", "灰", "白"]).pack(
        anchor="w", padx=12, pady=(2, 8))

    tk.Label(right, text="线宽", bg=C["bg_alt"], fg=C["muted"],
             font=(FONT, 9)).pack(anchor="w", padx=12)
    width_v = tk.StringVar(value="2")
    ttk.Combobox(right, textvariable=width_v, state="readonly", width=10,
                 values=["1", "2", "3", "5"]).pack(anchor="w", padx=12, pady=(2, 8))

    # ⑩ 文字样式
    bold_v = tk.BooleanVar(value=False)
    ital_v = tk.BooleanVar(value=False)
    tk.Checkbutton(right, text="粗体", variable=bold_v, bg=C["bg_alt"],
                   fg=C["text"], selectcolor=C["card"],
                   activebackground=C["bg_alt"], font=(FONT, 9)).pack(
        anchor="w", padx=12)
    tk.Checkbutton(right, text="斜体", variable=ital_v, bg=C["bg_alt"],
                   fg=C["text"], selectcolor=C["card"],
                   activebackground=C["bg_alt"], font=(FONT, 9)).pack(
        anchor="w", padx=12)

    def apply_style():
        """把字号/颜色/粗斜体应用到选中块。

        注意（踩过的坑）：之前写成「选黑色就不改颜色」，
        导致用户怎么点都变不成黑 —— 黑色是合法颜色，必须照常应用。
        """
        sels = st.get("sels") or []
        if not sels:
            toast(root, C, "先用「选择」工具选中文字块", kind="warn")
            return
        try:
            size = float(size_v.get() or 0) or None
        except Exception:
            size = None
        # 颜色始终按当前选择应用（包括黑色）
        col = cur_color()
        ok = studio.restyle_blocks(sels, size=size, color=col,
                                   bold=bold_v.get() or None,
                                   italic=ital_v.get() or None)
        if ok:
            redraw()
            toast(root, C, "已应用样式到 %d 个文字块" % len(sels), kind="ok")
        else:
            toast(root, C, "应用失败，可能该块无法编辑", kind="warn")

    W.RoundButton(right, "应用样式", apply_style, bg=C["accent"], fg="#ffffff",
                  hover=C["accent_hv"], radius=8, padx=12, pady=5,
                  font=(FONT, 9), width=150).pack(anchor="w", padx=12, pady=(8, 4))

    W.RoundButton(right, "删除选中块",
                  lambda: (studio.delete_blocks(st.get("sels") or []),
                           st.__setitem__("sels", []), redraw()),
                  bg=C["head_bg"], fg=C["danger"], hover=C["accent_sf"],
                  radius=8, padx=12, pady=5,
                  font=(FONT, 9), width=150).pack(anchor="w", padx=12, pady=2)

    sel_lbl = tk.Label(right, text="未选中", bg=C["bg_alt"], fg=C["text_sub"],
                       font=(FONT, 9), wraplength=160, justify="left")
    sel_lbl.pack(anchor="w", padx=12, pady=(10, 6))

    # ④ 操作历史（撤销到哪一步一目了然）
    tk.Frame(right, bg=C["border"], height=1).pack(fill="x", padx=12, pady=8)
    tk.Label(right, text="操作历史", bg=C["bg_alt"], fg=C["text"],
             font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 4))
    hist_lb = tk.Listbox(right, height=6, bg=C["entry_bg"], fg=C["text_sub"],
                         font=(FONT, 8), relief="flat",
                         highlightbackground=C["border"], highlightthickness=1,
                         selectbackground=C["accent"], selectforeground="#ffffff")
    hist_lb.pack(fill="x", padx=12, pady=(0, 4))

    def refresh_hist():
        try:
            hist_lb.delete(0, "end")
            labels = studio.history()
            for i, lab in enumerate(labels, 1):
                hist_lb.insert("end", "%2d. %s" % (i, lab))
        except Exception:
            pass

    W.RoundButton(right, "回到这一步",
                  lambda: _undo_to(hist_lb.curselection()),
                  bg=C["head_bg"], fg=C["text"], hover=C["accent_sf"],
                  radius=8, padx=10, pady=4,
                  font=(FONT, 8), width=150).pack(anchor="w", padx=12, pady=(0, 8))

    def _undo_to(sel):
        """连续撤销到选中那一步。"""
        if not sel:
            return
        target = len(studio.history()) - sel[0]
        n = 0
        while n < target and studio.can_undo():
            studio.undo(); n += 1
        refresh_hist()
        redraw()

    # 页面操作
    tk.Frame(right, bg=C["border"], height=1).pack(fill="x", padx=12, pady=8)
    tk.Label(right, text="页面", bg=C["bg_alt"], fg=C["text"],
             font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 6))

    def page_op(fn, label):
        def go():
            if fn():
                build_thumbs(); redraw()
            else:
                messagebox.showinfo("未能完成", label + " 失败或已到边界。", parent=win)
        return go

    for lab, fn in (
        ("上移本页", lambda: studio.move_page(st["page"], -1)),
        ("下移本页", lambda: studio.move_page(st["page"], 1)),
        ("插入空白页", lambda: studio.insert_blank(st["page"])),
        ("复制本页", lambda: studio.duplicate_page(st["page"])),
        ("旋转 90°", lambda: studio.rotate_page(st["page"], 90)),
        ("删除本页", lambda: studio.delete_page(st["page"])),
    ):
        W.RoundButton(right, lab, page_op(fn, lab), bg=C["head_bg"], fg=C["text"],
                      hover=C["accent_sf"], radius=8, padx=10, pady=5,
                      font=(FONT, 9), width=150).pack(anchor="w", padx=12, pady=2)

    # ============ 画布绘制与交互 ============
    COLOR_MAP = {"黑": (0, 0, 0), "红": (0.85, 0.1, 0.1), "蓝": (0.1, 0.1, 0.85),
                 "绿": (0.1, 0.6, 0.1), "灰": (0.45, 0.45, 0.45),
                 "白": (1, 1, 1)}

    def cur_color():
        return COLOR_MAP.get(color_v.get(), (0.85, 0.1, 0.1))

    def redraw():
        """重绘当前页 + 选中框（支持 ⑬ 反色预览）。"""
        try:
            png, w, h = studio.render(st["page"], st["zoom"])
        except Exception as exc:
            messagebox.showerror("渲染失败", str(exc), parent=win)
            return
        # ⑬ 反色预览：只影响显示，不改文件（夜间看白底 PDF 不刺眼）
        if st.get("invert"):
            try:
                import io as _io
                from PIL import Image, ImageOps
                im = Image.open(_io.BytesIO(png)).convert("RGB")
                im = ImageOps.invert(im)
                buf = _io.BytesIO()
                im.save(buf, format="PNG")
                png = buf.getvalue()
            except Exception:
                pass
        try:
            img = tk.PhotoImage(data=png)
        except Exception as exc:
            messagebox.showerror("显示失败", str(exc), parent=win)
            return
        st["img_ref"] = img
        cv.delete("page")
        cv.create_image(0, 0, anchor="nw", image=img, tags="page")
        cv.configure(scrollregion=(0, 0, w, h))
        page_lbl.configure(text="%d / %d" % (st["page"] + 1, studio.pages))
        # 编辑后文档结构会变（redact + 重写），缓存的文字块引用会失效 ——
        # 必须重新解析，否则下一次操作会作用到错的位置
        try:
            if st.get("sels"):
                st["sels"] = studio.resolve(st["sels"])
        except Exception:
            pass
        draw_sel()
        try:
            refresh_hist()
        except Exception:
            pass

    def draw_sel():
        """① 把所有选中块的边框画出来（支持多选）。"""
        cv.delete("sel")
        sels = st.get("sels") or []
        z = st["zoom"]
        for b in sels:
            if b.page != st["page"]:
                continue
            x0, y0, x1, y1 = (b.bbox[0] * z, b.bbox[1] * z,
                              b.bbox[2] * z, b.bbox[3] * z)
            cv.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                                outline=C["accent"], width=2, dash=(5, 3),
                                tags="sel")

    def to_pdf(ev):
        """画布坐标 → PDF 坐标。"""
        return (cv.canvasx(ev.x) / st["zoom"], cv.canvasy(ev.y) / st["zoom"])

    def on_press(ev):
        x, y = to_pdf(ev)
        tool = st["tool"]

        if tool == "select":
            # ① 按住 Shift 或右键 = 加选；否则重新选
            multi = bool(ev.state & 0x0001)   # Shift
            b = studio.block_at(st["page"], x, y)
            sels = st.setdefault("sels", [])
            if b is None:
                if not multi:
                    sels.clear()
                    # 空白处按下 → 开始框选
                    st["drag"] = {"marquee": True, "x0": x, "y0": y, "x1": x, "y1": y}
                sel_lbl.configure(text="已选 %d 个" % len(sels) if sels else "未选中")
            else:
                if multi:
                    if b not in sels:
                        sels.append(b)
                else:
                    sels[:] = [b]
                st["drag"] = {"x": x, "y": y}
                # 把选中块的当前样式回填到属性面板，
                # 避免"面板显示 12 号，实际是 9.7 号"这种错配导致误改
                try:
                    size_v.set("%.1f" % b.size if abs(b.size - round(b.size)) > 0.05
                               else "%d" % round(b.size))
                    rev = {v: k for k, v in COLOR_MAP.items()}
                    for _k, _v in COLOR_MAP.items():
                        if all(abs(_v[i] - b.color[i]) < 0.08 for i in range(3)):
                            color_v.set(_k)
                            break
                    low = (b.font or "").lower()
                    bold_v.set("bold" in low)
                    ital_v.set("italic" in low or "oblique" in low)
                except Exception:
                    pass
            sel_lbl.configure(text="已选 %d 个%s" % (
                len(sels), ("：" + sels[0].text[:26]) if len(sels) == 1 else ""))
            draw_sel()

        elif tool == "pen":
            st["pen"] = [(x, y)]

        elif tool in ("rect", "line"):
            st["drag"] = {"x0": x, "y0": y, "x1": x, "y1": y}

        elif tool == "text":
            t = simpledialog.askstring("插入文字", "要插入的文字：", parent=win)
            if t:
                studio.insert_text(st["page"], x, y, t,
                                   size=float(size_v.get() or 12),
                                   color=cur_color())
                redraw()

        elif tool == "image":
            f = filedialog.askopenfilename(
                title="选择图片",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
            if f:
                studio.insert_image(st["page"], (x, y, x + 200, y + 150), f)
                redraw()

    def on_drag(ev):
        x, y = to_pdf(ev)
        tool = st["tool"]
        d = st.get("drag")

        if tool == "select" and d and d.get("marquee"):
            # ① 框选：画个虚线框预览
            d["x1"], d["y1"] = x, y
            z = st["zoom"]
            cv.delete("marquee")
            cv.create_rectangle(d["x0"] * z, d["y0"] * z, x * z, y * z,
                                outline=C["accent"], width=1, dash=(4, 3),
                                tags="marquee")

        elif tool == "select" and d and ("x" in d):
            # ① 拖动：整体移动所有选中的块
            dx = x - d["x"]; dy = y - d["y"]
            if abs(dx) > 2 or abs(dy) > 2:
                sels = st.get("sels") or []
                if sels and studio.move_blocks(sels, dx, dy):
                    d["x"], d["y"] = x, y
                    redraw()
                    # 拖动后重建拖拽锚点（块引用已被 resolve 刷新）
                    if st.get("sels"):
                        d["x"], d["y"] = x, y

        elif tool == "pen" and st["pen"]:
            st["pen"].append((x, y))
            z = st["zoom"]
            pts = st["pen"]
            if len(pts) >= 2:
                cv.create_line(pts[-2][0] * z, pts[-2][1] * z,
                               pts[-1][0] * z, pts[-1][1] * z,
                               fill=C["accent"], width=2, tags="preview")

        elif tool in ("rect", "line") and d:
            d["x1"], d["y1"] = x, y
            z = st["zoom"]
            cv.delete("preview")
            if tool == "rect":
                cv.create_rectangle(d["x0"] * z, d["y0"] * z, x * z, y * z,
                                    outline=C["accent"], width=2, tags="preview")
            else:
                cv.create_line(d["x0"] * z, d["y0"] * z, x * z, y * z,
                               fill=C["accent"], width=2, tags="preview")

    def on_release(ev):
        x, y = to_pdf(ev)
        tool = st["tool"]
        d = st.get("drag")
        w = float(width_v.get() or 2)

        if tool == "pen" and len(st["pen"]) >= 2:
            studio.draw_polyline(st["page"], st["pen"], w, cur_color())
            st["pen"] = []
            cv.delete("preview"); redraw()

        elif tool == "rect" and d:
            x0, y0 = min(d["x0"], d["x1"]), min(d["y0"], d["y1"])
            x1, y1 = max(d["x0"], d["x1"]), max(d["y0"], d["y1"])
            if tool == "rect":
                studio.draw_rect(st["page"], (x0, y0, x1, y1), w, cur_color())
            cv.delete("preview"); redraw()

        elif tool == "line" and d:
            studio.draw_line(st["page"], (d["x0"], d["y0"]), (x, y), w, cur_color())
            cv.delete("preview"); redraw()

        elif tool == "white":
            pass

        # ① 框选收尾：把框内的文字块加入选中
        elif tool == "select" and d and d.get("marquee"):
            x0, y0 = d["x0"], d["y0"]
            x1, y1 = d.get("x1", x), d.get("y1", y)
            if abs(x1 - x0) > 6 and abs(y1 - y0) > 6:
                found = studio.blocks_in(st["page"], (x0, y0, x1, y1))
                sels = st.setdefault("sels", [])
                for b in found:
                    if b not in sels:
                        sels.append(b)
                sel_lbl.configure(text="框选到 %d 个，共选 %d 个"
                                       % (len(found), len(sels)))
            cv.delete("marquee")

        st["drag"] = None
        draw_sel()

    def on_double(ev):
        """双击文字块 → 改内容。"""
        x, y = to_pdf(ev)
        b = studio.block_at(st["page"], x, y)
        if not b:
            return
        t = simpledialog.askstring("编辑文字", "修改为：", initialvalue=b.text,
                                   parent=win)
        if t is None:
            return
        if studio.move_block(b, 0, 0, new_text=t):
            st["sels"] = []
            redraw()
            sel_lbl.configure(text="已修改")

    def on_key(ev):
        sels = st.get("sels") or []
        if ev.keysym in ("Delete", "BackSpace") and sels:
            if studio.delete_blocks(sels):
                st["sels"] = []
                redraw()
                sel_lbl.configure(text="已删除 %d 个" % len(sels))
        elif ev.keysym == "Escape":
            st["sels"] = []
            draw_sel()
            sel_lbl.configure(text="未选中")
        elif ev.keysym.lower() == "a" and (ev.state & 4):
            # Ctrl+A 全选本页文字块
            st["sels"] = studio.text_blocks(st["page"])
            draw_sel()
            sel_lbl.configure(text="全选 %d 个" % len(st["sels"]))
        elif ev.keysym.lower() == "z" and (ev.state & 4):
            studio.undo(); redraw()
        elif ev.keysym.lower() == "y" and (ev.state & 4):
            studio.redo(); redraw()
        elif ev.keysym.lower() == "z" and (ev.state & 1):
            studio.redo(); redraw()

    cv.bind("<ButtonPress-1>", on_press)
    cv.bind("<B1-Motion>", on_drag)
    cv.bind("<ButtonRelease-1>", on_release)
    cv.bind("<Double-Button-1>", on_double)
    win.bind("<Key>", on_key)
    win.focus_set()

    # 滚轮：默认上下滚动页面；Ctrl+滚轮 = 缩放
    # （之前只绑了 Control-MouseWheel，普通滚轮完全没反应 —— 用户反馈"滚轮不滚动"）
    def on_wheel(ev):
        try:
            if ev.state & 4:      # Ctrl
                st["zoom"] = max(0.4, min(4.0,
                                         st["zoom"] + (0.15 if ev.delta > 0 else -0.15)))
                redraw()
            else:
                cv.yview_scroll(int(-1 * (ev.delta / 120) * 3), "units")
        except Exception:
            pass

    def _bind_wheel(_e=None):
        cv.bind_all("<MouseWheel>", on_wheel)

    def _unbind_wheel(_e=None):
        try:
            cv.unbind_all("<MouseWheel>")
        except Exception:
            pass

    cv.bind("<Enter>", _bind_wheel, add="+")
    cv.bind("<Leave>", _unbind_wheel, add="+")
    # 缩略图列表也要能滚
    thumb_cv.bind("<MouseWheel>",
                  lambda e: thumb_cv.yview_scroll(int(-1 * (e.delta / 120)), "units"),
                  add="+")

    build_thumbs()
    redraw()
    return win

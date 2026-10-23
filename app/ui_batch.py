# -*- coding: utf-8 -*-
"""批量确认窗口：勾选要翻的文件 + 完整的输出设置。

和单个文件的确认窗口保持一致 —— 输出版本、翻译范围、保存位置、
附加选项都在这里设，不用再弹第二个窗。
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk


def confirm_batch(parent, files, C, _f, flat_btn, toast, A):
    """拖入多个 PDF 时的确认窗口。

    返回 (要不要继续, 选中的文件列表)。
    取消或一个都没勾 → (False, [])
    """
    result = {"go": False, "files": []}
    files = [str(x) for x in files]
    cfg = A.load_config()

    dlg = tk.Toplevel(parent)
    dlg.title("确认批量翻译")
    dlg.geometry("780x680")
    dlg.minsize(700, 600)
    try:
        dlg.configure(bg=C["bg"])
    except Exception:
        pass
    dlg.transient(parent)

    # ★ 按钮栏必须先 pack（side=bottom），否则下面的 expand 区域会把它挤出去
    bar_holder = tk.Frame(dlg, bg=C["bg"])
    bar_holder.pack(side="bottom", fill="x")

    # ---------------- 顶部标题 ----------------
    head = tk.Frame(dlg, bg=C["bg"])
    head.pack(fill="x", padx=18, pady=(14, 4))
    tk.Label(head, text="拖入了 %d 个 PDF" % len(files),
             bg=C["bg"], fg=C["text"], font=_f(13, "bold"),
             anchor="w").pack(anchor="w")
    tk.Label(head, text="勾选要翻译的（双击切换）；没勾选的会跳过",
             bg=C["bg"], fg=C["muted"], font=_f(9),
             anchor="w").pack(anchor="w", pady=(2, 0))

    # ---------------- 文件列表 ----------------
    box = tk.Frame(dlg, bg=C["bg"])
    box.pack(fill="both", expand=True, padx=18, pady=(6, 6))

    # 右侧缩略图：点哪个文件就看哪个的首面，避免选错（和单文件窗口一致）
    thumb_holder = tk.Frame(box, bg=C["bg"], width=150)
    thumb_holder.pack(side="right", fill="y", padx=(10, 0))
    thumb_holder.pack_propagate(False)
    thumb_lbl = tk.Label(thumb_holder, bg=C["bg"], fg=C["muted"],
                         font=_f(8), text="", justify="center")
    thumb_lbl.pack(expand=True)
    _thumb_ref = {"img": None}

    def _show_thumb(path_str):
        try:
            import fitz
            with fitz.open(str(path_str)) as doc:
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.30, 0.30))
                png = pix.tobytes("png")
            if not png or len(png) > 4_000_000:
                raise ValueError("too large")
            img = tk.PhotoImage(data=png)
            while img.width() > 140:
                img = img.subsample(2, 2)
            _thumb_ref["img"] = img
            thumb_lbl.configure(image=img, text="")
        except Exception:
            _thumb_ref["img"] = None
            thumb_lbl.configure(image="", text="(无法预览)")

    # 列表放进自己的容器（缩略图占 box 右侧，滚动条要跟着列表走）
    list_box = tk.Frame(box, bg=C["bg"])
    list_box.pack(side="left", fill="both", expand=True)

    tw = ttk.Treeview(list_box, columns=("sel", "name", "pages", "est"),
                      show="headings", height=8)
    for col, txt, wd, anc in (("sel", "选", 44, "center"),
                              ("name", "文件", 380, "w"),
                              ("pages", "页数", 70, "center"),
                              ("est", "预计", 170, "w")):
        tw.heading(col, text=txt)
        tw.column(col, width=wd, anchor=anc,
                  stretch=(col in ("name", "est")))
    sb = ttk.Scrollbar(list_box, command=tw.yview)
    tw.configure(yscrollcommand=sb.set)
    # 列表先 pack（否则滚动条会先把右侧吃掉，列表变窄）
    tw.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    info = {}
    total_pages = 0
    for pth in files:
        pages = 0
        est = ""
        try:
            chk = A.check_source_pdf(pth)
            pages = int(chk.get("pages") or 0) if chk.get("ok") else 0
            if pages:
                est = A.estimate_workload(pages, cfg)
                total_pages += pages
        except Exception:
            pass
        iid = tw.insert("", "end", values=(
            "\u2611", Path(pth).name,
            str(pages) if pages else "?", est))
        info[iid] = pth

    def _toggle(iid=None):
        ids = [iid] if iid else tw.selection()
        for i in ids:
            if i not in info:
                continue
            cur = tw.item(i, "values")[0]
            tw.item(i, values=("\u2610" if cur == "\u2611" else "\u2611",
                               *tw.item(i, "values")[1:]))
        _update_sum()

    def _all():
        for i in info:
            tw.item(i, values=("\u2611", *tw.item(i, "values")[1:]))
        _update_sum()

    def _none():
        for i in info:
            tw.item(i, values=("\u2610", *tw.item(i, "values")[1:]))
        _update_sum()

    def _picked():
        out = []
        for i, pth in info.items():
            try:
                if tw.item(i, "values")[0] == "\u2611":
                    out.append(pth)
            except Exception:
                pass
        return out

    def _click(e):
        iid = tw.identify_row(e.y)
        col = tw.identify_column(e.x)
        if iid and col == "#1":
            _toggle(iid)
            return "break"

    tw.bind("<Button-1>", _click, add="+")
    tw.bind("<Double-1>", lambda e: _toggle(), add="+")
    tw.bind("<space>", lambda e: (_toggle(), "break")[1], add="+")


    def _on_select(_e=None):
        sel = tw.selection()
        if not sel:
            return
        pth = info.get(sel[0])
        if pth:
            _show_thumb(pth)

    tw.bind("<<TreeviewSelect>>", _on_select, add="+")

    # 默认选中第一个并显示缩略图
    try:
        _first = tw.get_children()
        if _first:
            tw.selection_set(_first[0])
            _show_thumb(info[_first[0]])
    except Exception:
        pass

    # ---------------- 汇总 ----------------
    # 断点续译提示：这一批里有上次没翻完的，提前说一声
    try:
        _resumable = []
        for _pth in files:
            _rec = A.get_progress(_pth)
            if _rec:
                _resumable.append(
                    (Path(_pth).name, len(_rec.get("done_pages") or []),
                     int(_rec.get("total") or 0)))
        if _resumable:
            _rf = tk.Frame(dlg, bg=C["accent_sf"])
            _rf.pack(fill="x", padx=18, pady=(0, 6))
            _txt = "，".join("%s (%d/%d 页)" % r for r in _resumable[:3])
            if len(_resumable) > 3:
                _txt += " 等 %d 个" % len(_resumable)
            tk.Label(_rf, text="⏸  发现上次未完成：" + _txt,
                     bg=C["accent_sf"], fg=C["accent"],
                     font=_f(9), anchor="w", justify="left",
                     wraplength=700).pack(anchor="w", padx=10, pady=6)
    except Exception:
        pass

    sum_row = tk.Frame(dlg, bg=C["bg"])
    sum_row.pack(fill="x", padx=18, pady=(0, 4))

    def _update_sum():
        n = len(_picked())
        try:
            sum_lbl.configure(
                text="已选 %d / %d 个" % (n, len(files)),
                fg=C["accent"] if n else C["muted"])
        except Exception:
            pass

    sum_lbl = tk.Label(sum_row, text="已选 %d / %d 个" % (len(files), len(files)),
                       bg=C["bg"], fg=C["accent"], font=_f(10, "bold"),
                       anchor="w")
    sum_lbl.pack(side="left")

    # ---------------- 输出设置 ----------------
    sep = tk.Frame(dlg, bg=C["border"], height=1)
    sep.pack(fill="x", padx=18, pady=(6, 8))

    form = tk.Frame(dlg, bg=C["bg"])
    form.pack(fill="x", padx=18, pady=(0, 6))

    tk.Label(form, text="输出设置（应用于这一批所有文件）",
             bg=C["bg"], fg=C["text"], font=_f(10, "bold"),
             anchor="w").pack(anchor="w", pady=(0, 6))

    def row(label, hint=""):
        f = tk.Frame(form, bg=C["bg"])
        f.pack(fill="x", pady=3)
        tk.Label(f, text=label, bg=C["bg"], fg=C["text"],
                 font=_f(10), width=10, anchor="w").pack(side="left")
        b = tk.Frame(f, bg=C["bg"])
        b.pack(side="left", fill="x", expand=True)
        if hint:
            tk.Label(b, text=hint, bg=C["bg"], fg=C["muted"],
                     font=_f(8), anchor="w").pack(anchor="w")
        return b

    # 输出版本
    mode_labels = ["中文版（仅译文）", "双语对照版", "两者都要"]
    mode_map = {"中文版（仅译文）": "mono",
                "双语对照版": "dual", "两者都要": "both"}
    rev_mode = {v: k for k, v in mode_map.items()}
    mode_v = tk.StringVar(value=rev_mode.get(
        str(cfg.get("output_mode") or "mono"), "中文版（仅译文）"))
    b = row("输出版本")
    ttk.Combobox(b, textvariable=mode_v, values=mode_labels,
                 state="readonly", width=22).pack(anchor="w")

    # 翻译范围
    pages_v = tk.StringVar(value=str(cfg.get("pages") or ""))
    b = row("翻译范围", "留空 = 全部；可填 1-10,15,20-")
    ttk.Entry(b, textvariable=pages_v, width=24).pack(anchor="w")

    # 保存位置
    dir_v = tk.StringVar(value=str(A.resolve_output_dir(cfg)))
    b = row("保存位置")
    e = ttk.Entry(b, textvariable=dir_v, width=40)
    e.pack(side="left", fill="x", expand=True)

    def browse():
        try:
            d = filedialog.askdirectory(title="选择保存位置",
                                        initialdir=dir_v.get())
            if d:
                dir_v.set(d)
        except Exception:
            pass
    flat_btn(b, "浏览", browse).pack(side="left", padx=(6, 0))

    # 附加选项
    opt = tk.Frame(form, bg=C["bg"])
    opt.pack(fill="x", pady=(8, 0))
    chk_style = dict(bg=C["bg"], fg=C["text"], selectcolor=C["card"],
                     activebackground=C["bg"], font=_f(9), anchor="w")
    skip_toc_v = tk.BooleanVar(value=bool(cfg.get("skip_toc", False)))
    tk.Checkbutton(opt, text="跳过目录页（避免条目被合并成一段）",
                   variable=skip_toc_v, **chk_style).pack(anchor="w")
    qc_v = tk.BooleanVar(value=bool(cfg.get("quality_check", True)))
    tk.Checkbutton(opt, text="翻译后做质量检查（漏翻/英文残留/数字丢失）",
                   variable=qc_v, **chk_style).pack(anchor="w")

    # ---------------- 按钮 ----------------
    def go():
        picked = _picked()
        if not picked:
            try:
                toast("先勾选要翻译的文件", "warn")
            except Exception:
                pass
            return
        # 保存设置（和单个文件的确认窗口一致）
        try:
            c = A.load_config()
            c["output_mode"] = mode_map.get(mode_v.get(), "mono")
            c["pages"] = pages_v.get().strip()
            c["output_dir"] = dir_v.get().strip()
            c["skip_toc"] = bool(skip_toc_v.get())
            c["quality_check"] = bool(qc_v.get())
            A.save_config(c)
        except Exception as exc:
            try:
                toast("保存设置失败：%s" % exc, "err")
            except Exception:
                pass
            return
        result["go"] = True
        result["files"] = picked
        dlg.destroy()

    def cancel():
        dlg.destroy()

    dlg.protocol("WM_DELETE_WINDOW", cancel)

    inner = tk.Frame(bar_holder, bg=C["bg"])
    inner.pack(fill="x", padx=18, pady=(8, 12))
    flat_btn(inner, "开始翻译", go, "primary").pack(side="right")
    flat_btn(inner, "取消", cancel).pack(side="right", padx=8)
    flat_btn(inner, "全选", _all).pack(side="left")
    flat_btn(inner, "全不选", _none).pack(side="left", padx=6)
    tk.Label(inner, text="设置会保存，下次沿用",
             bg=C["bg"], fg=C["muted"], font=_f(8)).pack(
        side="left", padx=10)

    _update_sum()

    try:
        dlg.grab_set()
    except Exception:
        pass
    try:
        parent.wait_window(dlg)
    except Exception:
        pass
    return result["go"], result["files"]

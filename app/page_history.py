# -*- coding: utf-8 -*-
"""历史记录页。

原本是 app_sidebar.main() 里的 471 行嵌套函数。
依赖 5 个共享变量 + 7 个辅助函数，全部改成参数传入；
函数体内部的写法不变，所以不需要改几百行代码。
"""
from __future__ import annotations

import os
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import ui_dialogs as D


def page_history(parent, C, root, widgets, items, mode,
                _f, flat_btn, header, log, set_status,
                start, toast, tk, ttk, messagebox,
                filedialog, simpledialog, os, Path, A):
    header(parent, "历史记录", "双击打开；右键更多操作")
    holder = tk.Frame(parent, bg=C["bg"])
    holder.pack(fill="both", expand=True, padx=20, pady=(14, 18))

    bar = tk.Frame(holder, bg=C["bg"])
    bar.pack(fill="x", pady=(0, 8))

    # ★ 分组查看：下拉筛选（之前只能设置分组，不能按分组查看）
    tk.Label(bar, text="分组", bg=C["bg"], fg=C["text_sub"],
             font=_f(9)).pack(side="left")
    grp_v = tk.StringVar(value="全部")
    grp_box = ttk.Combobox(bar, textvariable=grp_v, state="readonly",
                           width=14)
    grp_box.pack(side="left", padx=(6, 14))

    def refresh_groups():
        vals = ["全部", "未分组", "Word"] + (A.list_groups() or [])
        grp_box.configure(values=vals)
        if grp_v.get() not in vals:
            grp_v.set("全部")
    grp_box.bind("<<ComboboxSelected>>", lambda e: refresh())

    tk.Label(bar, text="搜索", bg=C["bg"], fg=C["text_sub"],
             font=_f(9)).pack(side="left")
    kw_v = tk.StringVar()
    kw_e = ttk.Entry(bar, textvariable=kw_v, width=16)
    kw_e.pack(side="left", padx=(6, 14))
    kw_e.bind("<KeyRelease>", lambda e: refresh())

    tk.Label(bar, text="打开方式", bg=C["bg"], fg=C["text_sub"],
             font=_f(9)).pack(side="left")
    ow = tk.StringVar(value=str(A.load_config().get("open_with") or "auto"))
    _ow_opts = ["系统默认", "WPS", "Microsoft Edge", "Microsoft Office",
                "打开所在文件夹"]
    ow_box = ttk.Combobox(bar, state="readonly", width=20, values=_ow_opts)
    lab2val = {"系统默认": "auto", "WPS": "wps", "Microsoft Edge": "edge",
               "Microsoft Office": "office", "打开所在文件夹": "folder"}
    val2lab = {v: k for k, v in lab2val.items()}
    ow_box.set(val2lab.get(ow.get(), "系统默认"))
    ow_box.pack(side="left", padx=(6, 14))

    def save_ow(_e=None):
        c = A.load_config()
        c["open_with"] = lab2val.get(ow_box.get(), "auto")
        A.save_config(c)
    ow_box.bind("<<ComboboxSelected>>", save_ow)

    tw = ttk.Treeview(holder, columns=("time", "file", "kind", "engine", "group"),
                      show="headings", height=12, selectmode="extended")
    for col, t, w in (("time", "时间", 130), ("file", "文件", 300),
                      ("kind", "产出", 80), ("engine", "引擎", 90),
                      ("group", "分组", 100)):
        tw.heading(col, text=t); tw.column(col, width=w, anchor="w")
    sb = ttk.Scrollbar(holder, command=tw.yview)
    tw.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tw.pack(side="left", fill="both", expand=True)
    widgets["history.hist"] = tw

    # ⑧ 空状态：叠在列表区域【里面】的提示（默认隐藏）
    empty_lbl = tk.Label(holder, text="", bg=C["card"], fg=C["muted"],
                         font=_f(10), justify="center")
    widgets["history.hist_empty"] = empty_lbl

    # ① 行悬停高亮
    try:
        tw.tag_configure("hover", background=C["accent_sf"])
        tw.tag_configure("odd", background=C["row_alt"])
    except Exception:
        pass

    def on_motion(ev):
        try:
            row = tw.identify_row(ev.y)
            if getattr(on_motion, "_last", None) == row:
                return
            on_motion._last = row
            for i in tw.get_children():
                tags = [t for t in tw.item(i, "tags") if t != "hover"]
                tw.item(i, tags=tuple(tags))
            if row:
                tags = [t for t in tw.item(row, "tags") if t != "hover"]
                tw.item(row, tags=tuple(tags) + ("hover",))
        except Exception:
            pass
    tw.bind("<Motion>", on_motion, add="+")

    # ---- 全选 / 多选增强 ----
    # 用户反馈"不支持 Ctrl+A 全选"。ttk.Treeview 默认没有这个快捷键，
    # 需要自己绑：Ctrl+A 全选、Ctrl+点击 加选、Shift+点击 连选。
    _last_click = {"iid": None}

    def _select_all(_e=None):
        kids = tw.get_children()
        if not kids:
            return "break"
        tw.selection_set(kids)          # 传元组 = 全部选中
        try:
            tw.focus(kids[0])
            tw.see(kids[0])
        except Exception:
            pass
        return "break"

    def _on_click(e):
        """Ctrl+点击 = 加选；Shift+点击 = 从上次点到这次全选。"""
        row = tw.identify_row(e.y)
        if not row:
            return
        sel = list(tw.selection())
        if e.state & 0x0004:            # Ctrl
            if row in sel:
                sel.remove(row)
            else:
                sel.append(row)
            tw.selection_set(sel)
            return "break"
        if e.state & 0x0001:            # Shift
            kids = list(tw.get_children())
            last = _last_click["iid"]
            if last in kids and row in kids:
                i, j = kids.index(last), kids.index(row)
                lo, hi = min(i, j), max(i, j)
                merged = list(dict.fromkeys(sel + kids[lo:hi + 1]))
                tw.selection_set(merged)
                return "break"
        _last_click["iid"] = row

    # Ctrl+A / Ctrl+a（大小写都绑）
    for _seq in ("<Control-a>", "<Control-A>"):
        tw.bind(_seq, _select_all, add="+")
    tw.bind("<ButtonPress-1>", _on_click, add="+")

    # 整页也能按 Ctrl+A（焦点在别处时）
    def _page_select_all(e):
        try:
            w = root.focus_get()
            # 只在焦点不在输入框时才接管，避免影响文本框里的全选
            if w is not None and w.winfo_class() in ("Entry", "TEntry", "Text"):
                return
            return _select_all()
        except Exception:
            return

    try:
        parent.bind("<Control-a>", _page_select_all, add="+")
        parent.bind("<Control-A>", _page_select_all, add="+")
    except Exception:
        pass
    tw.bind("<Leave>", lambda e: [tw.item(i, tags=tuple(
        t for t in tw.item(i, "tags") if t != "hover"))
        for i in tw.get_children()], add="+")

    def refresh(*_a):
        """按「分组 + 关键词」过滤后显示。"""
        try:
            refresh_groups()
        except Exception:
            pass
        want = grp_v.get()
        kw = kw_v.get().strip().lower()
        for i in tw.get_children():
            tw.delete(i)
        shown = 0
        for it in A.load_history():
            g = str(it.get("group") or "").strip()
            has_word = bool(it.get("word_files"))
            # 分组筛选
            if want == "未分组":
                if g:
                    continue
            elif want == "Word":
                if not has_word:
                    continue
            elif want != "全部":
                if g != want:
                    continue
            # 关键词筛选
            if kw:
                hay = " ".join([
                    str(it.get("source_name") or ""),
                    str(it.get("engine") or ""),
                    g, str(it.get("time") or ""),
                ]).lower()
                if kw not in hay:
                    continue
            kind = "Word" if it.get("mode") == "word" else (
                "PDF+Word" if has_word else "PDF")
            tags = ("odd",) if shown % 2 else ()
            tw.insert("", "end", values=(
                it.get("time", ""), it.get("source_name", ""), kind,
                A.ENGINE_LABEL.get(str(it.get("engine")), it.get("engine") or ""),
                g or "未分组"), tags=tags)
            shown += 1
        try:
            info_lbl.configure(text="共 %d 条" % shown)
        except Exception:
            pass
        # ⑧ 空状态：叠在列表【里面】的一行提示（不占额外空间）
        try:
            if shown == 0:
                tip = ("还没有记录 —— 把 PDF 拖进窗口，或去「翻译」页选文件"
                       if not kw_v.get().strip() else "没有匹配的记录")
                empty_lbl.configure(text="📭  " + tip)
                empty_lbl.place(relx=0.5, rely=0.45, anchor="center")
                empty_lbl.lift()
            else:
                empty_lbl.place_forget()
        except Exception:
            pass

    def open_sel(_e=None):
        sel = tw.selection()
        if not sel:
            messagebox.showinfo("请先选择", "先选中一条记录。")
            return
        vals = tw.item(sel[0], "values")
        for it in A.load_history():
            if it.get("time") == vals[0] and it.get("source_name") == vals[1]:
                files = (it.get("files") or []) + (it.get("word_files") or [])
                if files and Path(str(files[0])).exists():
                    A.open_file_with(Path(str(files[0])), A.load_config())
                    set_status("已打开")
                else:
                    messagebox.showinfo("文件不在", "产物已被移动或删除。")
                return
    tw.bind("<Double-1>", open_sel)

    # ⑩ 拖拽/右键把记录归到分组
    def _apply_group(gname):
        sel = tw.selection()
        if not sel:
            toast("先选中一条记录", "warn")
            return
        vals = tw.item(sel[0], "values")
        A.set_history_group(str(vals[0]), gname)
        refresh()
        toast("已归入「%s」" % (gname or "未分组"), "ok")

    def ask_group():
        sel = tw.selection()
        if not sel:
            toast("先选中一条记录", "warn")
            return
        g = simpledialog.askstring(
            "设置分组",
            "分组名（留空 = 未分组）：\n现有：" + "、".join(A.list_groups() or ["无"]),
            parent=root)
        if g is None:
            return
        _apply_group(g.strip())

    def _rows_to_items(sel):
        """把选中的行映射回历史记录（按时间+文件名匹配）。"""
        hist = A.load_history()
        out = []
        for iid in sel:
            v = tw.item(iid, "values")
            t, name = str(v[0]), str(v[1])
            for it in hist:
                if (str(it.get("time")) == t
                        and str(it.get("source_name")) == name
                        and it not in out):
                    out.append(it)
                    break
        return out

    def _files_of(items):
        """收集这些记录涉及的输出文件。"""
        out = []
        for it in items:
            for key in ("files", "word_files"):
                for f in (it.get(key) or []):
                    p = Path(str(f))
                    if p.is_file() and p not in out:
                        out.append(p)
        return out

    def del_sel():
        """删除选中的历史记录（对话框实现在 ui_dialogs）。"""
        sel = tw.selection()
        if not sel:
            toast("先选中要删除的记录（可按住 Ctrl 多选）", "warn")
            return
        items = _rows_to_items(sel)
        if not items:
            return
        files = _files_of(items)
        return D.delete_history_dialog(
            root, C, _f, flat_btn, toast, A,
            tw, items, files, refresh)

    # ===== 右键菜单（多功能）=====
    def sel_item():
        s = tw.selection()
        if not s:
            return None
        vals = tw.item(s[0], "values")
        for it in A.load_history():
            if (str(it.get("time")) == str(vals[0])
                    and str(it.get("source_name")) == str(vals[1])):
                return it
        return None

    def open_with(mode):
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        files = (it.get("files") or []) + (it.get("word_files") or [])
        if not files or not Path(str(files[0])).exists():
            toast("文件已被移动或删除", "warn"); return
        c = A.load_config(); c["open_with"] = mode; A.save_config(c)
        # Office 打开 PDF 要走"PDF 重排"，实测很慢（30 秒~1 分钟），
        # 期间 Word 看着像卡住。这里先给个提示，避免用户以为死机了。
        if mode == "office" and str(files[0]).lower().endswith(".pdf"):
            toast("Office 正在转换 PDF，约需 15~30 秒，请稍候（若失败会自动转 Word）",
                  kind="warn", seconds=5)
        try:
            A.open_file_with(Path(str(files[0])), c)
            toast("已打开", "ok")
        except Exception as exc:
            toast("打开失败：%s" % exc, "err")

    def open_folder():
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        d = str(it.get("out_dir") or "")
        if not d:
            files = (it.get("files") or []) + (it.get("word_files") or [])
            if files:
                d = str(Path(str(files[0])).parent)
        if d and Path(d).is_dir():
            try:
                os.startfile(d)
            except Exception as exc:
                toast("打开失败：%s" % exc, "err")
        else:
            toast("找不到输出目录", "warn")

    def copy_path():
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        files = (it.get("files") or []) + (it.get("word_files") or [])
        p = str(files[0]) if files else str(it.get("out_dir") or "")
        if not p:
            return
        try:
            root.clipboard_clear(); root.clipboard_append(p)
            toast("路径已复制", "ok")
        except Exception:
            pass

    def open_word():
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        ws = it.get("word_files") or []
        w = next((x for x in ws if Path(str(x)).exists()), None)
        if not w:
            toast("这条记录没有 Word 文件", "warn"); return
        try:
            os.startfile(str(w)); toast("已打开 Word", "ok")
        except Exception as exc:
            toast("打开失败：%s" % exc, "err")

    def retranslate():
        """只重翻指定页（自动开启忽略缓存）。"""
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        src = str(it.get("source") or "")
        if not src or not Path(src).is_file():
            toast("找不到原始 PDF", "warn"); return
        pages = simpledialog.askstring(
            "重翻指定页", "要重翻哪些页？（如 3,7,12）", parent=root)
        if not pages or not pages.strip():
            return
        c = A.load_config()
        c["pages"] = pages.strip()
        c["ignore_cache"] = True
        A.save_config(c)
        log("🔁 重翻指定页：%s" % pages.strip())
        start(src)

    def copy_to_clipall():
        """把关键词填入搜索框（按文件名过滤同类记录）。"""
        it = sel_item()
        if not it:
            toast("先选中一条记录", "warn"); return
        kw_v.set(str(it.get("source_name") or "")[:6])
        refresh()

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="用系统默认打开", command=lambda: open_with("auto"))
    menu.add_command(label="用 WPS 打开", command=lambda: open_with("wps"))
    menu.add_command(label="用 Edge 打开", command=lambda: open_with("edge"))
    menu.add_command(label="用 Microsoft Office 打开",
                     command=lambda: open_with("office"))
    menu.add_command(label="打开 Word 文件", command=open_word)
    menu.add_separator()
    menu.add_command(label="打开所在文件夹", command=open_folder)
    menu.add_command(label="复制文件路径", command=copy_path)
    menu.add_separator()
    menu.add_command(label="设置分组…", command=ask_group)
    menu.add_command(label="取消分组", command=lambda: _apply_group(""))
    menu.add_separator()
    menu.add_command(label="重翻指定页…", command=retranslate)
    menu.add_command(label="按此文件名筛选", command=copy_to_clipall)
    menu.add_separator()
    menu.add_command(label="删除这条记录", command=del_sel)

    def popup(ev):
        row = tw.identify_row(ev.y)
        if row:
            tw.selection_set(row)
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
    tw.bind("<Button-3>", popup)

    # 底部：状态 + 常用按钮
    info_lbl = tk.Label(holder, text="", bg=C["bg"], fg=C["muted"],
                        font=_f(9))
    info_lbl.pack(side="bottom", anchor="w")

    btns = tk.Frame(holder, bg=C["bg"])
    btns.pack(side="bottom", fill="x", pady=(8, 4))
    flat_btn(btns, "打开选中", open_sel, "primary").pack(side="left")
    flat_btn(btns, "设置分组", ask_group).pack(side="left", padx=(8, 0))
    flat_btn(btns, "删除选中", del_sel, "danger").pack(side="left", padx=(8, 0))
    flat_btn(btns, "刷新", refresh).pack(side="left", padx=(8, 0))

    def clear_all():
        if not messagebox.askyesno("清空全部",
                                   "清空【全部】历史记录？\n（不会删除文件）",
                                   parent=root):
            return
        old = A.load_history()
        try:
            A.HISTORY_FILE.write_text("[]", encoding="utf-8")
        except Exception:
            pass
        refresh()

        def _undo():
            try:
                A.HISTORY_FILE.write_text(
                    __import__("json").dumps(old, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:
                pass
            try:
                refresh()
            except Exception:
                pass
            toast("已恢复 %d 条历史记录" % len(old), "ok")

        toast("历史已清空（可撤销）", "ok", action=_undo)

    flat_btn(btns, "清空全部", clear_all, "danger").pack(side="right")
    tk.Label(btns, text="（右键记录有更多操作）", bg=C["bg"], fg=C["muted"],
             font=_f(8)).pack(side="left", padx=8)
    refresh()
    # 页面被缓存，重新进入时刷新历史列表（新建的记录才会出现）
    widgets["history.on_enter"] = refresh

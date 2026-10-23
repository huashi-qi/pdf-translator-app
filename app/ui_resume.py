# -*- coding: utf-8 -*-
"""启动续译窗口：列出上次没翻完的任务，让用户选择继续或放弃。

用户反馈「断点继续翻译没用」的根因：
  1. 原来只在 confirm_output 里弹一个小提示，关掉软件后就完全看不到
  2. 而且那个提示会把 pages 设成剩余页码，翻完只覆盖一小部分页
这里改成：程序启动 → 有未完成任务就弹这个窗口 → 明确选择继续/放弃。
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk


def _fmt_time(ts):
    """把时间戳转成「昨天 14:30」这种好读的形式。"""
    try:
        import datetime as dt
        if not ts:
            return ""
        t = dt.datetime.fromtimestamp(float(ts))
        now = dt.datetime.now()
        d = (now.date() - t.date()).days
        hm = t.strftime("%H:%M")
        if d == 0:
            return "今天 " + hm
        if d == 1:
            return "昨天 " + hm
        if d < 7:
            return "%d 天前" % d
        return t.strftime("%m-%d %H:%M")
    except Exception:
        return ""


def show_resume_dialog(parent, C, _f, flat_btn, toast, A, on_resume):
    """展示未完成任务。

    on_resume(task) 会在用户选择「继续」时被调用，参数是该任务的 dict。
    返回是否展示过窗口（有任务才展示）。
    """
    try:
        tasks = A.list_all_progress()
    except Exception:
        return False
    if not tasks:
        return False

    result = {"done": False}

    dlg = tk.Toplevel(parent)
    dlg.title("有未完成的翻译")
    dlg.geometry("700x520")
    dlg.minsize(620, 440)
    try:
        dlg.configure(bg=C["bg"])
    except Exception:
        pass
    dlg.transient(parent)

    # ★ 按钮栏必须先 pack（side=bottom），否则会被 expand 区域挤出窗口
    bar_holder = tk.Frame(dlg, bg=C["bg"])
    bar_holder.pack(side="bottom", fill="x")

    head = tk.Frame(dlg, bg=C["bg"])
    head.pack(fill="x", padx=18, pady=(14, 4))
    tk.Label(head, text="上次有 %d 个文件没翻完" % len(tasks),
             bg=C["bg"], fg=C["text"], font=_f(13, "bold"),
             anchor="w").pack(anchor="w")
    tk.Label(head, text="选中一个点「继续翻译」；不想接着翻就点「全部忽略」",
             bg=C["bg"], fg=C["muted"], font=_f(9),
             anchor="w").pack(anchor="w", pady=(2, 0))

    box = tk.Frame(dlg, bg=C["bg"])
    box.pack(fill="both", expand=True, padx=18, pady=(8, 8))

    tw = ttk.Treeview(box, columns=("name", "prog", "rest", "when"),
                      show="headings", height=10)
    for col, txt, wd, anc in (("name", "文件", 260, "w"),
                              ("prog", "进度", 90, "center"),
                              ("rest", "剩余页", 130, "w"),
                              ("when", "最后编辑", 110, "center")):
        tw.heading(col, text=txt)
        tw.column(col, width=wd, anchor=anc,
                  stretch=(col in ("name", "rest")))
    sb = ttk.Scrollbar(box, command=tw.yview)
    tw.configure(yscrollcommand=sb.set)
    tw.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    info = {}
    for t in tasks:
        pct = int(t["done"] / t["total"] * 100) if t["total"] else 0
        iid = tw.insert("", "end", values=(
            t["name"],
            "%d/%d (%d%%)" % (t["done"], t["total"], pct),
            t.get("rest") or "",
            _fmt_time(t.get("updated"))))
        info[iid] = t

    # 默认选中第一个
    kids = tw.get_children()
    if kids:
        tw.selection_set(kids[0])

    def _selected():
        sel = tw.selection()
        if not sel:
            return None
        return info.get(sel[0])

    def _resume_one():
        task = _selected()
        if not task:
            try:
                toast("先选中一个文件", "warn")
            except Exception:
                pass
            return
        dlg.destroy()
        try:
            on_resume(task)
        except Exception as exc:
            try:
                toast("继续翻译失败：%s" % exc, "err")
            except Exception:
                pass

    def _ignore_one():
        task = _selected()
        if not task:
            return
        try:
            A.clear_progress(task["path"])
        except Exception:
            pass
        iid = tw.selection()[0] if tw.selection() else None
        if iid:
            tw.delete(iid)
            info.pop(iid, None)
        try:
            toast("已忽略：%s" % task["name"], "ok")
        except Exception:
            pass
        if not tw.get_children():
            dlg.destroy()

    def _ignore_all():
        try:
            for t in list(info.values()):
                A.clear_progress(t["path"])
        except Exception:
            pass
        dlg.destroy()

    def _later():
        # 不删除记录，下次启动还会提示
        dlg.destroy()

    dlg.protocol("WM_DELETE_WINDOW", _later)

    inner = tk.Frame(bar_holder, bg=C["bg"])
    inner.pack(fill="x", padx=18, pady=(8, 12))
    flat_btn(inner, "继续翻译", _resume_one, "primary").pack(side="right")
    flat_btn(inner, "稍后再说", _later).pack(side="right", padx=8)
    flat_btn(inner, "忽略选中", _ignore_one).pack(side="left", padx=(0, 6))
    flat_btn(inner, "全部忽略", _ignore_all).pack(side="left")

    tw.bind("<Double-1>", lambda e: _resume_one(), add="+")

    try:
        dlg.grab_set()
    except Exception:
        pass
    try:
        parent.wait_window(dlg)
    except Exception:
        pass
    result["done"] = True
    return True

# -*- coding: utf-8 -*-
"""翻译主页面（拖放区 / 进度 / 队列 / 步骤耗时 / 术语命中 / 日志）。

原本是 app_sidebar.main() 里的 547 行嵌套函数。

注意：这个函数会【写入】 state / widgets / _viewing。
提取前已确认它只是下标赋值（state["x"] = ...），
没有 `state = ...` 这种重新绑定，所以传引用是安全的 ——
写进去的内容对主程序可见。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import ui_dialogs as D

def page_translate(parent, C, root, widgets, state, _viewing, dpi,
                   _f, _fill_translate_panels, _update_queue,
                   confirm_output, do_cancel, flat_btn, header, pick,
                   preview_latest, recent_folder, set_status, start,
                   toast, tk, ttk, messagebox, Path, A, D, PS,
                   APP_DIR, make_diagnostic):
    header(parent, "翻译", "选择 PDF 后翻译；可多选批量处理")

    card = tk.Frame(parent, bg=C["card"], highlightbackground=C["border"],
                    highlightthickness=1)
    card.pack(fill="both", expand=True, padx=20, pady=(14, 18))

    # 拖放/选择区
    drop = tk.Frame(card, bg=C["bg_alt"], highlightbackground=C["border"],
                    highlightthickness=1, cursor="hand2")
    drop.pack(fill="x", padx=16, pady=(16, 12))
    dl = tk.Label(drop, text="⬇   把 PDF 拖到这里，或点下面的「选择 PDF」",
                  bg=C["bg_alt"], fg=C["accent"], font=_f(10),
                  pady=20, cursor="hand2")
    dl.pack(fill="x")
    for w in (drop, dl):
        w.bind("<Button-1>", lambda e: pick())

    row = tk.Frame(card, bg=C["card"])
    row.pack(fill="x", padx=16)
    b1 = flat_btn(row, "📂  选择 PDF", lambda: pick(), "primary")
    b1.pack(side="left")
    widgets["translate.pick_btn"] = b1
    b2 = flat_btn(row, "🕘  上次的文件夹", lambda: recent_folder())
    b2.pack(side="left", padx=(8, 0))
    # ⑪ 翻译结果预览：不用打开外部程序，直接看译文首页
    b3 = flat_btn(row, "👁  预览译文", lambda: preview_latest())
    b3.pack(side="left", padx=(8, 0))
    # 终止翻译
    b4 = flat_btn(row, "⛔  终止翻译", lambda: do_cancel(), "danger")
    b4.pack(side="left", padx=(8, 0))
    widgets["translate.cancel_btn"] = b4

    # ---- 进度区 ----
    tk.Frame(card, bg=C["border"], height=1).pack(fill="x", padx=16, pady=(12, 0))
    pf = tk.Frame(card, bg=C["card"])
    pf.pack(fill="x", padx=16, pady=(10, 0))
    # 进度条：显式给高度，否则某些 DPI 下细得看不见
    pb = ttk.Progressbar(pf, mode="determinate", maximum=100.0,
                         length=200, style="Thick.Horizontal.TProgressbar")
    pb.pack(side="left", fill="x", expand=True)
    widgets["translate.prog"] = pb
    # ⑪ 百分比 + 页数都显示
    pl = tk.Label(pf, text="0%", bg=C["card"], fg=C["text"],
                  font=_f(10, "bold"), width=6, anchor="e")
    pl.pack(side="left", padx=(8, 0))
    widgets["translate.pct"] = pl
    pg_lbl = tk.Label(pf, text="", bg=C["card"], fg=C["accent"],
                      font=_f(9), width=12, anchor="w")
    pg_lbl.pack(side="left", padx=(4, 0))
    widgets["translate.pages"] = pg_lbl
    sl = tk.Label(pf, text="", bg=C["card"], fg=C["muted"],
                  font=_f(9), width=18, anchor="w")
    sl.pack(side="left", padx=(6, 0))
    widgets["translate.stage"] = sl

    # 剩余时间 / 已用时间
    eta_row = tk.Frame(card, bg=C["card"])
    eta_row.pack(fill="x", padx=16, pady=(4, 0))
    eta_lbl = tk.Label(eta_row, text="", bg=C["card"], fg=C["text_sub"],
                       font=_f(9), anchor="w")
    eta_lbl.pack(side="left")
    widgets["translate.eta"] = eta_lbl

    # ② 批量队列面板 + ⑥ 可拖拽调整顺序
    qf = tk.Frame(card, bg=C["card"])
    qf.pack(fill="x", padx=16, pady=(12, 0))
    qh = tk.Frame(qf, bg=C["card"])
    qh.pack(fill="x")
    tk.Label(qh, text="批量队列", bg=C["card"], fg=C["muted"],
             font=_f(9), anchor="w").pack(side="left")
    q_tip = tk.Label(qh, text="（拖动可调整顺序）", bg=C["card"],
                     fg=C["muted"], font=_f(8))
    q_tip.pack(side="left", padx=6)
    # 用 Treeview 而不是 Listbox：
    # 需要显示「状态 / 页数 / 耗时」多列，还能点选回看当次数据。
    q_box = tk.Frame(qf, bg=C["card"])
    q_box.pack(fill="x", pady=(2, 0))
    q_lb = ttk.Treeview(q_box, columns=("state", "name", "info"),
                        show="headings", height=4,
                        selectmode="extended")
    for col, txt, wd, anc in (("state", "状态", 78, "center"),
                              ("name", "文件", 260, "w"),
                              ("info", "结果", 160, "w")):
        q_lb.heading(col, text=txt)
        q_lb.column(col, width=wd, anchor=anc, stretch=(col == "name"))
    q_sb = ttk.Scrollbar(q_box, command=q_lb.yview)
    q_lb.configure(yscrollcommand=q_sb.set)
    q_sb.pack(side="right", fill="y")
    q_lb.pack(side="left", fill="x", expand=True)
    widgets["translate.queue"] = q_lb

    # 状态标签颜色（_QS_NAMES 定义在 main() 作用域，见文件上方）
    try:
        for _k, _fg in (("wait", C["muted"]), ("run", C["accent"]),
                        ("ok", C["ok"]), ("fail", C["danger"]),
                        ("skip", C["muted"])):
            q_lb.tag_configure(_k, foreground=_fg)
    except Exception:
        pass

    # ===== 点选队列项 → 回看那一次的翻译数据 =====
    # 关键：如果用户点的是【已完成】的历史项，就暂停实时刷新
    # （_viewing 非空 → _live_tick 不覆盖面板）；
    # 点回「进行中」那一项时恢复实时。
    def _on_queue_select(_e=None):
        try:
            sel = q_lb.selection()
            if not sel:
                return
            vals = q_lb.item(sel[0], "values")
            name = str(vals[1]) if len(vals) > 1 else ""
            state_txt = str(vals[0]) if vals else ""
            if not name or "拖 PDF" in name:
                return

            # 点的是「进行中 / 等待」→ 恢复实时刷新
            if ("进行中" in state_txt) or ("等待" in state_txt):
                _viewing["name"] = ""
                _viewing["path"] = ""
                try:
                    set_status("实时进度", C["accent"])
                except Exception:
                    pass
                return

            # 点的是历史项 → 锁定面板
            results = state.get("results") or {}
            hit = None
            for pth, rec in results.items():
                if Path(pth).name == name:
                    hit = (pth, rec)
                    break
            if not hit:
                return
            pth, rec = hit

            _viewing["name"] = name
            _viewing["path"] = pth

            tm = rec.get("timing") or {}
            _fill_translate_panels(tm)

            # 汇总标签改成「历史」口吻
            try:
                sm = widgets.get("translate.steps_sum")
                if sm is not None:
                    if rec.get("ok"):
                        tt = float(tm.get("total") or 0)
                        sm.configure(
                            text="历史记录 · 共 %.1f 秒 · %d 个阶段"
                                 % (tt, len(tm.get("stages") or [])),
                            fg=C["text_sub"])
                    else:
                        sm.configure(text="（这个文件翻译失败了）",
                                     fg=C["danger"])
            except Exception:
                pass

            try:
                if rec.get("ok"):
                    set_status("正在查看：%s（已完成）" % name, C["ok"])
                else:
                    set_status("正在查看：%s（失败）" % name, C["danger"])
            except Exception:
                pass
        except Exception:
            pass

    q_lb.bind("<<TreeviewSelect>>", _on_queue_select, add="+")

    # 双击 → 打开这个文件的译文
    def _on_queue_double(_e=None):
        try:
            sel = q_lb.selection()
            if not sel:
                return
            name = q_lb.item(sel[0], "values")[1]
            results = state.get("results") or {}
            for pth, rec in results.items():
                if Path(pth).name == name and rec.get("ok"):
                    of = rec.get("out_file") or ""
                    if of and Path(of).exists():
                        A.open_file_with(Path(of), A.load_config())
                        return
            toast("这个文件还没有可打开的译文", "warn")
        except Exception:
            pass

    q_lb.bind("<Double-1>", _on_queue_double, add="+")

    # ⑥ 拖拽排序：按下记录位置，松开时把该行移到新位置
    q_drag = {"idx": None}

    def q_press(e):
        _row = q_lb.identify_row(e.y)
        q_drag["idx"] = _row if _row else None
        try:
            if q_drag["idx"]:
                q_lb.selection_set(q_drag["idx"])
        except Exception:
            pass

    def q_motion(e):
        if q_drag["idx"] is None:
            return
        tgt = q_lb.identify_row(e.y)
        if not tgt or tgt == q_drag["idx"]:
            return
        batch = state.get("batch") or []
        if not (0 <= q_drag["idx"] < len(batch) and 0 <= tgt < len(batch)):
            return
        item = batch.pop(q_drag["idx"])
        batch.insert(tgt, item)
        state["batch"] = batch
        q_drag["idx"] = tgt
        _update_queue()
        try:
            q_lb.selection_set(tgt)
        except Exception:
            pass

    def q_release(_e=None):
        q_drag["idx"] = None
        _update_queue()

    q_lb.bind("<ButtonPress-1>", q_press)
    q_lb.bind("<B1-Motion>", q_motion)
    q_lb.bind("<ButtonRelease-1>", q_release)

    # ===== 队列单项操作：移出 / 置顶 / 清空 =====
    def _q_selected_name():
        """当前选中行的文件名（Treeview 用 iid，这里统一换成名字匹配）。"""
        try:
            sel = q_lb.selection()
            if not sel:
                return None
            return str(q_lb.item(sel[0], "values")[1])
        except Exception:
            return None

    def _q_batch_index(name):
        """文件名 → batch 下标（找不到返回 None）。"""
        if not name:
            return None
        batch = state.get("batch") or []
        for i, f in enumerate(batch):
            try:
                if Path(f).name == name:
                    return i
            except Exception:
                continue
        return None

    def _q_remove():
        name = _q_selected_name()
        if not name:
            toast("先在队列里选中一项", "warn")
            return
        bi = _q_batch_index(name)
        if bi is None:
            toast("这一项不能移除（已完成的记录请用「清空队列」）", "warn")
            return
        batch = list(state.get("batch") or [])
        batch.pop(bi)
        state["batch"] = batch
        _update_queue()
        toast("已移出队列：%s" % name, "ok")

    def _q_top():
        name = _q_selected_name()
        if not name:
            toast("先在队列里选中一项", "warn")
            return
        bi = _q_batch_index(name)
        if bi is None:
            toast("这一项不能提前", "warn")
            return
        batch = list(state.get("batch") or [])
        item = batch.pop(bi)
        batch.insert(0, item)
        state["batch"] = batch
        _update_queue()
        toast("已置顶：%s" % name, "ok")

    def _q_clear():
        n = len(state.get("batch") or [])
        if n == 0 and not (state.get("results") or {}):
            toast("队列已经是空的", "warn")
            return
        msg = "清空队列？"
        if n:
            msg += "\\n\\n有 %d 个文件还没翻译。" % n
        if state.get("results"):
            msg += "\\n已完成的 %d 条记录也会一起清掉。" % len(state["results"])
        if not messagebox.askyesno("清空队列", msg):
            return
        state["batch"] = []
        state["results"] = {}
        state["batch_ok"] = 0
        state["batch_fail"] = []
        _update_queue()
        toast("队列已清空", "ok")

    q_menu = tk.Menu(root, tearoff=0, bg=C["card"], fg=C["text"],
                     activebackground=C["accent"],
                     activeforeground="#ffffff",
                     borderwidth=0, relief="flat")
    q_menu.add_command(label="移出队列", command=_q_remove)
    q_menu.add_command(label="提前翻译", command=_q_top)
    q_menu.add_separator()
    q_menu.add_command(label="清空队列", command=_q_clear)

    def _q_show_menu(e):
        # 右键时先选中鼠标下的那一项，符合直觉
        try:
            i = q_lb.identify_row(e.y)
            if i:
                q_lb.selection_set(i)
        except Exception:
            pass
        try:
            q_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                q_menu.grab_release()
            except Exception:
                pass
        return "break"

    q_lb.bind("<Button-3>", _q_show_menu, add="+")
    q_lb.bind("<Delete>", lambda e: (_q_remove(), "break")[1], add="+")
    q_lb.bind("<BackSpace>", lambda e: (_q_remove(), "break")[1], add="+")

    # 标题旁加个提示
    try:
        q_tip.configure(text="（拖动排序 · 右键移除）")
    except Exception:
        pass

    # ---- 三块内容放进可拖拽的 PanedWindow，用户能自由调整高度 ----
    # 注意：tk.PanedWindow 不支持 highlightthickness / handlesize
    # （那是 ttk 的选项），传了会报 "unknown option"。
    pane = tk.PanedWindow(card, orient="vertical", bg=C["card"],
                          sashwidth=8, sashrelief="flat", bd=0,
                          sashpad=1, opaqueresize=True)
    pane.pack(fill="both", expand=True, padx=16, pady=(6, 8))
    widgets["translate.pane"] = pane

    # ===== 步骤耗时 =====
    # 关键：标题和表格必须放进【同一个容器】再塞进 pane，
    # 否则标题会留在外面，看起来"标题跑错位置"。
    sp_wrap = tk.Frame(pane, bg=C["card"])
    sp_head = tk.Frame(sp_wrap, bg=C["card"])
    sp_head.pack(fill="x", pady=(2, 3))
    tk.Label(sp_head, text="⏱  步骤耗时", bg=C["card"], fg=C["text"],
             font=_f(10, "bold"), anchor="w").pack(side="left")
    sp_sum = tk.Label(sp_head, text="（翻译完成后显示）", bg=C["card"],
                      fg=C["muted"], font=_f(8))
    sp_sum.pack(side="left", padx=8)
    widgets["translate.steps_sum"] = sp_sum
    tk.Label(sp_head, text="↕ 拖动分隔条调高度", bg=C["card"],
             fg=C["border"], font=_f(7)).pack(side="right")
    sp_tw_box = tk.Frame(sp_wrap, bg=C["card"])
    sp_tw_box.pack(fill="both", expand=True)
    sp_tw = ttk.Treeview(sp_tw_box, columns=("stage", "secs", "pct", "bar"),
                         show="headings", height=4)
    for col, t, wd, anc in (("stage", "阶段", 200, "w"), ("secs", "耗时", 80, "e"),
                            ("pct", "占比", 65, "e"), ("bar", "分布", 210, "w")):
        sp_tw.heading(col, text=t); sp_tw.column(col, width=wd, anchor=anc)
    sp_sb = ttk.Scrollbar(sp_tw_box, command=sp_tw.yview)
    sp_tw.configure(yscrollcommand=sp_sb.set)
    sp_sb.pack(side="right", fill="y")
    sp_tw.pack(side="left", fill="both", expand=True)
    widgets["translate.steps"] = sp_tw
    pane.add(sp_wrap, minsize=int(80 * dpi / 96.0))

    # ===== 术语表命中 =====
    gl_wrap = tk.Frame(pane, bg=C["card"])
    gl_head = tk.Frame(gl_wrap, bg=C["card"])
    gl_head.pack(fill="x", pady=(2, 3))
    tk.Label(gl_head, text="📖  术语表命中", bg=C["card"], fg=C["text"],
             font=_f(10, "bold"), anchor="w").pack(side="left")
    gl_sum = tk.Label(gl_head, text="（翻译完成后显示）", bg=C["card"],
                      fg=C["muted"], font=_f(8))
    gl_sum.pack(side="left", padx=8)
    widgets["translate.gloss_sum"] = gl_sum

    gl_tw_box = tk.Frame(gl_wrap, bg=C["card"])
    gl_tw_box.pack(fill="both", expand=True)
    gl_tw = ttk.Treeview(gl_tw_box, columns=("term", "cn", "cnt"),
                         show="headings", height=4)
    for col, t, wd in (("term", "英文术语", 220), ("cn", "中文", 200),
                       ("cnt", "命中次数", 90)):
        gl_tw.heading(col, text=t); gl_tw.column(col, width=wd, anchor="w")
    gl_sb = ttk.Scrollbar(gl_tw_box, command=gl_tw.yview)
    gl_tw.configure(yscrollcommand=gl_sb.set)
    gl_sb.pack(side="right", fill="y")
    gl_tw.pack(side="left", fill="both", expand=True)
    widgets["translate.gloss"] = gl_tw
    pane.add(gl_wrap, minsize=int(75 * dpi / 96.0))

    # 日志（也放进 PanedWindow，高度可拖）
    logbox = tk.Frame(pane, bg=C["card"])
    lg_head = tk.Frame(logbox, bg=C["card"])
    lg_head.pack(fill="x", pady=(2, 3))
    tk.Label(lg_head, text="📄  运行日志", bg=C["card"], fg=C["text"],
             font=_f(10, "bold"), anchor="w").pack(side="left")
    wrap = tk.Frame(logbox, bg=C["card"])
    wrap.pack(fill="both", expand=True)
    txt = tk.Text(wrap, wrap="word", bg=C["log_bg"], fg=C["log_fg"],
                  insertbackground=C["log_fg"], font=("Consolas", 9),
                  relief="flat", padx=8, pady=6, height=6)
    sb = ttk.Scrollbar(wrap, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); txt.pack(side="left", fill="both", expand=True)
    widgets["translate.log"] = txt
    pane.add(logbox, minsize=int(70 * dpi / 96.0))

    # ④ 日志右键菜单（实现在 ui_dialogs.attach_log_menu）
    # make_diagnostic 定义在后面，用 lambda 延迟绑定，避免 NameError
    D.attach_log_menu(txt, root, C, _f, toast, A, APP_DIR,
                      lambda: make_diagnostic())

    # 标题右侧加个提示，让用户知道能右键
    tk.Label(lg_head, text="右键可复制/保存", bg=C["card"], fg=C["muted"],
             font=_f(8)).pack(side="right")

    if A._UI_STATE.get("log"):
        try:
            txt.insert("1.0", A._UI_STATE["log"]); txt.see("end")
        except Exception:
            pass

    # ⑥ 最近打开的 PDF（快速重新翻译/编辑）
    rec_f = tk.Frame(card, bg=C["card"])
    rec_f.pack(fill="x", padx=16, pady=(10, 0))
    tk.Label(rec_f, text="最近打开", bg=C["card"], fg=C["muted"],
             font=_f(9), anchor="w").pack(anchor="w")
    rec_lb = tk.Listbox(rec_f, height=2, bg=C["entry_bg"], fg=C["text_sub"],
                        font=_f(9), relief="flat",
                        highlightbackground=C["border"], highlightthickness=1,
                        selectbackground=C["accent"],
                        selectforeground="#ffffff")
    rec_lb.pack(fill="x", pady=(2, 0))
    widgets["translate.recent"] = rec_lb
    _rec_files: list = []

    def refresh_recent():
        rec_lb.delete(0, "end")
        _rec_files.clear()
        seen = set()
        for it in A.load_history():
            p = str(it.get("source") or "")
            if p and Path(p).is_file() and p not in seen:
                seen.add(p)
                _rec_files.append(p)
                rec_lb.insert("end", Path(p).name)
            if len(_rec_files) >= 8:
                break
        if not _rec_files:
            rec_lb.insert("end", "（还没有记录）")

    def open_recent(_e=None):
        sel = rec_lb.curselection()
        if not sel or sel[0] >= len(_rec_files):
            return
        p = _rec_files[sel[0]]
        if messagebox.askyesno("打开", "翻译这个文件？\n\n%s" % Path(p).name):
            if confirm_output(p):
                start(p)

    rec_lb.bind("<Double-1>", open_recent)
    refresh_recent()

    # ④ 恢复上次未完成的批量队列
    def _restore_queue():
        try:
            saved = A.load_queue()
        except Exception:
            return
        left = [it["path"] for it in saved
                if it.get("path") and Path(it["path"]).is_file()]
        if not left:
            try:
                A.clear_queue()
            except Exception:
                pass
            return
        if messagebox.askyesno(
                "继续上次的批量任务",
                "检测到上次有 %d 个文件没翻完：\n\n%s\n\n现在继续吗？"
                % (len(left),
                   "\n".join("· " + Path(p).name for p in left[:6])
                   + ("\n…" if len(left) > 6 else ""))):
            state["batch"] = left
            _update_queue()
            toast("已恢复 %d 个待翻译文件" % len(left), "ok")
        else:
            try:
                A.clear_queue()
            except Exception:
                pass

    root.after(600, _restore_queue)

    # PanedWindow 初始高度：步骤 ~30% / 术语 ~26% / 日志占剩余
    # （第一次布局完成后设一次，之后用户拖动就由他控制）
    _pane_init = {"done": False, "id": None}

    def _init_panes(_e=None):
        if _pane_init["done"]:
            return
        try:
            h = pane.winfo_height()
            if h < 120:
                return
            pane.sash_place(0, 0, int(h * 0.30))
            pane.sash_place(1, 0, int(h * 0.56))
            _pane_init["done"] = True
            if _pane_init["id"]:
                pane.unbind("<Configure>", _pane_init["id"])
        except Exception:
            pass
    try:
        _pane_init["id"] = pane.bind("<Configure>", _init_panes, add="+")
    except Exception:
        pass

    # 首次建好就填一次队列（否则是空列表，看着像坏了）
    try:
        _update_queue()
    except Exception:
        pass



    # 页面被缓存，重新进入时刷新"最近打开" + 队列
    def _on_enter_translate():
        try:
            refresh_recent()
        except Exception:
            pass
        try:
            _update_queue()
        except Exception:
            pass
    widgets["translate.on_enter"] = _on_enter_translate

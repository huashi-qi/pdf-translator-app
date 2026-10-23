# -*- coding: utf-8 -*-
"""工具窗口（术语表管理 / 从 PDF 学词汇）。

这两个窗口原本嵌在 app_sidebar.main() 里，各占 200~300 行，
只依赖主题 C、根窗口 root，以及几个通用辅助函数。
把它们作为参数传进来，就不再需要闭包，main() 也小了一大截。
"""
from __future__ import annotations

import csv as _csv
import io as _io
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# core 是纯后端（不依赖 Tk），直接用它的路径/配置工具
import core as A


def open_glossary(root, C, _f, flat_btn, toast, APP_DIR):
    """术语表管理：搜索 + 增删改 + 导入导出。"""
    import csv as _csv
    import io as _io
    gpath = APP_DIR / "glossary.csv"

    win = tk.Toplevel(root)
    win.title("术语表管理")
    win.geometry("780x620")
    win.minsize(640, 460)
    try:
        win.configure(bg=C["bg"])
    except Exception:
        pass

    top = tk.Frame(win, bg=C["bg"])
    top.pack(fill="x", padx=12, pady=10)
    tk.Label(top, text="搜索", bg=C["bg"], fg=C["text"],
             font=_f(10)).pack(side="left")
    q_v = tk.StringVar()
    q_e = ttk.Entry(top, textvariable=q_v, width=22)
    q_e.pack(side="left", padx=(6, 10))
    info = tk.Label(top, text="", bg=C["bg"], fg=C["muted"], font=_f(9))
    info.pack(side="left")

    body = tk.Frame(win, bg=C["bg"])
    body.pack(fill="both", expand=True, padx=12)
    tv = ttk.Treeview(body, columns=("s", "t", "d"), show="headings",
                      selectmode="extended")
    tv.heading("s", text="英文（原文）")
    tv.heading("t", text="中文（译文）")
    tv.heading("d", text="领域")
    tv.column("s", width=300, anchor="w")
    tv.column("t", width=220, anchor="w")
    tv.column("d", width=90, anchor="w")
    sb = ttk.Scrollbar(body, command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tv.pack(side="left", fill="both", expand=True)
    try:
        tv.tag_configure("odd", background=C["row_alt"])
    except Exception:
        pass

    rows: list = []

    def load():
        rows.clear()
        try:
            with gpath.open(encoding="utf-8-sig", newline="") as fh:
                for r in _csv.DictReader(fh):
                    s = (r.get("source") or "").strip()
                    t = (r.get("target") or "").strip()
                    if s and t:
                        rows.append([s, t, (r.get("domain") or "").strip()])
        except Exception:
            pass
        refresh()

    def refresh(*_a):
        q = q_v.get().strip().lower()
        for i in tv.get_children():
            tv.delete(i)
        n = 0
        want_dom = dom_map2.get(domf_v.get(), "")
        only_uncat = (domf_v.get() == "未分类")
        for idx, (s, t, _d) in enumerate(rows):
            if q and q not in s.lower() and q not in t.lower():
                continue
            if only_uncat:
                if _d:
                    continue
            elif want_dom and _d != want_dom:
                continue
            tv.insert("", "end", iid="g%d" % idx, values=(s, t, _d),
                      tags=("odd",) if n % 2 else ())
            n += 1
        info.configure(text="共 %d 条%s" % (
            len(rows), "，匹配 %d 条" % n if q else ""))

    # ⑤ 按领域筛选（比批量改领域更直观）
    dom_labels2 = ["全部", "电源", "模拟", "数字", "器件", "封装", "射频", "未分类"]
    dom_map2 = {"电源": "power", "模拟": "analog", "数字": "digital",
                "器件": "device", "封装": "package", "射频": "rf"}
    f2 = tk.Frame(win, bg=C["bg"])
    f2.pack(fill="x", padx=12, pady=(0, 6))
    tk.Label(f2, text="领域", bg=C["bg"], fg=C["text_sub"],
             font=_f(9)).pack(side="left")
    domf_v = tk.StringVar(value="全部")
    domf_box = ttk.Combobox(f2, textvariable=domf_v, state="readonly",
                            width=10, values=dom_labels2)
    domf_box.pack(side="left", padx=(6, 14))
    domf_box.bind("<<ComboboxSelected>>", lambda e: refresh())

    q_v.trace_add("write", lambda *a: refresh())

    def save():
        try:
            buf = _io.StringIO()
            w = _csv.writer(buf, lineterminator="\n")
            w.writerow(["source", "target", "tgt_lng", "domain"])
            for s, t, d in rows:
                w.writerow([s, t, "zh_cn", d])
            gpath.write_text(buf.getvalue(), encoding="utf-8-sig")
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=win)
            return False

    # 编辑区
    ef = tk.Frame(win, bg=C["bg"])
    ef.pack(fill="x", padx=12, pady=10)
    tk.Label(ef, text="英文", bg=C["bg"], fg=C["text"]).grid(row=0, column=0)
    s_v = tk.StringVar()
    ttk.Entry(ef, textvariable=s_v, width=28).grid(row=0, column=1, padx=6)
    tk.Label(ef, text="中文", bg=C["bg"], fg=C["text"]).grid(row=0, column=2)
    t_v = tk.StringVar()
    ttk.Entry(ef, textvariable=t_v, width=24).grid(row=0, column=3, padx=6)

    def add():
        s, t = s_v.get().strip(), t_v.get().strip()
        if not s or not t:
            toast("英文和中文都要填", "warn")
            return
        for i, (a, _b, _d) in enumerate(rows):
            if a.lower() == s.lower():
                rows[i] = [s, t, rows[i][2]]
                break
        else:
            rows.append([s, t, ""])
        if save():
            refresh()
            toast("已添加/更新", "ok")
        s_v.set(""); t_v.set("")

    def pick(_e=None):
        sel = tv.selection()
        if sel:
            idx = int(sel[0][1:])
            if 0 <= idx < len(rows):
                s_v.set(rows[idx][0]); t_v.set(rows[idx][1])
    tv.bind("<<TreeviewSelect>>", pick)

    def delsel():
        sel = tv.selection()
        if not sel:
            toast("先选中要删的术语", "warn")
            return
        for i in sorted((int(x[1:]) for x in sel), reverse=True):
            if 0 <= i < len(rows):
                rows.pop(i)
        if save():
            refresh()
            toast("已删除", "ok")

    # ⑤ 批量操作
    def batch_domain():
        """把选中的多条术语统一改到某个领域。"""
        sel = tv.selection()
        if not sel:
            toast("先选中要改的术语（可多选）", "warn")
            return
        doms = ["", "power", "analog", "digital", "device", "package", "rf"]
        g = simpledialog.askstring(
            "批量设置领域",
            "领域代码（留空 = 不限）：\n" +
            "  power 电源 / analog 模拟 / digital 数字\n" +
            "  device 器件 / package 封装 / rf 射频",
            parent=win)
        if g is None:
            return
        g = g.strip()
        for i in (int(x[1:]) for x in sel):
            if 0 <= i < len(rows):
                rows[i][2] = g
        if save():
            refresh()
            toast("已更新 %d 条" % len(sel), "ok")

    def batch_delete():
        sel = tv.selection()
        if not sel:
            toast("先选中要删的术语", "warn")
            return
        if not messagebox.askyesno("批量删除",
                                   "删除选中的 %d 条术语？" % len(sel), parent=win):
            return
        # ② 撤销用：记下删掉的行及其原位置
        removed = []
        for i in sorted((int(x[1:]) for x in sel), reverse=True):
            if 0 <= i < len(rows):
                removed.append((i, list(rows[i])))
        for i, _row in removed:
            if 0 <= i < len(rows):
                rows.pop(i)
        if save():
            refresh()

            def _undo():
                # 按原下标从小到大插回去，位置才对得上
                for i, row in sorted(removed):
                    rows.insert(min(i, len(rows)), row)
                if save():
                    refresh()
                    toast("已恢复 %d 条术语" % len(removed), "ok")

            toast("已删除 %d 条（可撤销）" % len(sel), "ok", action=_undo)

    def batch_replace():
        """批量查找替换（比如统一某个译法）。"""
        sel = tv.selection()
        if not sel:
            toast("先选中要替换的术语", "warn")
            return
        old = simpledialog.askstring("批量替换", "把中文里的哪部分替换掉？",
                                     parent=win)
        if not old:
            return
        new = simpledialog.askstring("批量替换", "替换为：", parent=win)
        if new is None:
            return
        n = 0
        for i in (int(x[1:]) for x in sel):
            if 0 <= i < len(rows) and old in rows[i][1]:
                rows[i][1] = rows[i][1].replace(old, new)
                n += 1
        if save():
            refresh()
            toast("已替换 %d 条中的文字" % n, "ok")

    bar = tk.Frame(win, bg=C["bg"])
    bar.pack(fill="x", padx=12, pady=(0, 6))
    flat_btn(bar, "添加/更新", add, "primary").pack(side="left")
    flat_btn(bar, "删除选中", delsel, "danger").pack(side="left", padx=6)

    bar2 = tk.Frame(win, bg=C["bg"])
    bar2.pack(fill="x", padx=12, pady=(0, 12))
    tk.Label(bar2, text="批量：", bg=C["bg"], fg=C["muted"],
             font=_f(9)).pack(side="left")
    flat_btn(bar2, "改领域", batch_domain).pack(side="left", padx=(4, 0))
    flat_btn(bar2, "替换文字", batch_replace).pack(side="left", padx=6)
    flat_btn(bar2, "批量删除", batch_delete, "danger").pack(side="left", padx=6)

    def imp():
        f = filedialog.askopenfilename(
            title="导入术语表 CSV", filetypes=[("CSV", "*.csv")],
            initialdir=str(APP_DIR))
        if not f:
            return
        try:
            n = 0
            with open(f, encoding="utf-8-sig", newline="") as fh:
                for r in _csv.DictReader(fh):
                    s = (r.get("source") or "").strip()
                    t = (r.get("target") or "").strip()
                    if s and t:
                        rows.append([s, t, ""]); n += 1
            if save():
                refresh()
            toast("已导入 %d 条" % n, "ok")
        except Exception as exc:
            toast("导入失败：%s" % exc, "err")

    def exp():
        f = filedialog.asksaveasfilename(
            title="导出术语表", defaultextension=".csv",
            initialfile="glossary_export.csv",
            initialdir=str(APP_DIR))
        if not f:
            return
        try:
            with open(f, "w", encoding="utf-8-sig", newline="") as fh:
                w = _csv.writer(fh, lineterminator="\n")
                w.writerow(["source", "target", "tgt_lng", "domain"])
                for s, t, d in rows:
                    w.writerow([s, t, "zh_cn", d])
            toast("已导出 %d 条" % len(rows), "ok")
        except Exception as exc:
            toast("导出失败：%s" % exc, "err")

    flat_btn(bar, "导入 CSV", imp).pack(side="right")
    flat_btn(bar, "导出 CSV", exp).pack(side="right", padx=6)
    load()


def open_learner(root, C, _f, flat_btn, toast, APP_DIR, log):
    """从 PDF 学习术语（免费引擎也能给中文建议）。"""
    try:
        import learn_check
    except Exception as exc:
        toast("模块缺失：%s" % exc, "err")
        return
    f = filedialog.askopenfilename(
        title="选择要学习的 PDF", filetypes=[("PDF 文件", "*.pdf")],
        initialdir=A._existing_dir(None, Path.home() / "Documents", APP_DIR))
    if not f:
        return

    win = tk.Toplevel(root)
    win.title("术语自动学习")
    win.geometry("880x620")
    try:
        win.configure(bg=C["bg"])
    except Exception:
        pass
    top = tk.Frame(win, bg=C["bg"])
    top.pack(fill="x", padx=12, pady=10)
    info = tk.Label(top, text="正在分析…", bg=C["bg"], fg=C["text_sub"],
                    font=_f(9))
    info.pack(side="left")

    body = tk.Frame(win, bg=C["bg"])
    body.pack(fill="both", expand=True, padx=12)
    tv = ttk.Treeview(body, columns=("term", "cnt", "cn", "ctx"),
                      show="headings", height=14)
    for c, t, w in (("term", "候选术语", 180), ("cnt", "次数", 55),
                    ("cn", "中文（可编辑）", 170), ("ctx", "上下文", 380)):
        tv.heading(c, text=t); tv.column(c, width=w, anchor="w")
    sb = ttk.Scrollbar(body, command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tv.pack(side="left", fill="both", expand=True)

    added: list = []

    def work():
        existing = set()
        try:
            with (APP_DIR / "glossary.csv").open(encoding="utf-8-sig", newline="") as fh:
                import csv as _c
                for r in _c.DictReader(fh):
                    existing.add((r.get("source") or "").strip())
        except Exception:
            pass
        # 找配套译文，用于推断中文
        tpdf = None
        try:
            stem = Path(f).stem
            od = A.resolve_output_dir(A.load_config())
            best = None
            for cand in od.rglob("*.pdf"):
                if cand.stem.startswith(stem) and "zh" in cand.name.lower():
                    if best is None or cand.stat().st_mtime > best.stat().st_mtime:
                        best = cand
            tpdf = best
        except Exception:
            pass
        cs = learn_check.learn_from_pdf(f, existing, top=80, translated_pdf=tpdf)

        def show():
            n_cn = 0
            for c in cs:
                if c.get("suggestion"):
                    n_cn += 1
                tv.insert("", "end", values=(c["term"], c["count"],
                                             c.get("suggestion") or "",
                                             c.get("context", "")))
            msg = "提取到 %d 个候选" % len(cs)
            if n_cn:
                msg += "，其中 %d 个已自动填好中文" % n_cn
            msg += "（参考译文：%s）" % (Path(tpdf).name[:22] if tpdf else "无")
            info.configure(text=msg)
        try:
            win.after(0, show)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()

    ef = tk.Frame(win, bg=C["bg"])
    ef.pack(fill="x", padx=12, pady=8)
    tk.Label(ef, text="术语", bg=C["bg"], fg=C["text"]).grid(row=0, column=0)
    s_v = tk.StringVar()
    ttk.Entry(ef, textvariable=s_v, width=24).grid(row=0, column=1, padx=6)
    tk.Label(ef, text="中文", bg=C["bg"], fg=C["text"]).grid(row=0, column=2)
    t_v = tk.StringVar()
    ttk.Entry(ef, textvariable=t_v, width=20).grid(row=0, column=3, padx=6)

    def pick(_e=None):
        sel = tv.selection()
        if sel:
            v = tv.item(sel[0], "values")
            s_v.set(v[0]); t_v.set(v[2] if len(v) > 2 else "")
    tv.bind("<<TreeviewSelect>>", pick)

    def auto_sug():
        """⑫ 未填中文的用免费引擎翻一下作参考。"""
        sel = tv.selection() or tv.get_children()[:10]
        info.configure(text="正在自动翻译 %d 个…" % len(sel))

        def job():
            n = 0
            for iid in sel:
                v = tv.item(iid, "values")
                term = str(v[0]) if v else ""
                cur_cn = str(v[2]) if len(v) > 2 else ""
                if not term or cur_cn:
                    continue
                sug = learn_check.suggest_translation(term)
                if sug:
                    def upd(i=iid, x=sug):
                        try:
                            o = tv.item(i, "values")
                            tv.item(i, values=(o[0], o[1], x, o[3] if len(o) > 3 else ""))
                        except Exception:
                            pass
                    win.after(0, upd)
                    n += 1
            win.after(0, lambda: info.configure(text="已填入 %d 个建议，请核对" % n))
        threading.Thread(target=job, daemon=True).start()

    def add_one():
        s, t = s_v.get().strip(), t_v.get().strip()
        if not s or not t:
            toast("术语和中文都要填", "warn")
            return
        # ⑫ 冲突提示
        try:
            import csv as _c
            with (APP_DIR / "glossary.csv").open(encoding="utf-8-sig", newline="") as fh:
                for r in _c.DictReader(fh):
                    if (r.get("source") or "").strip().lower() == s.lower():
                        old = (r.get("target") or "").strip()
                        if old != t and not messagebox.askyesno(
                                "术语冲突",
                                "「%s」已有译法：%s\n要覆盖为「%s」吗？" % (s, old, t),
                                parent=win):
                            return
                        break
        except Exception:
            pass
        added.append((s, t))
        for i in tv.get_children():
            if str(tv.item(i, "values")[0]) == s:
                tv.delete(i); break
        s_v.set(""); t_v.set("")
        info.configure(text="已准备 %d 条，点「保存到术语表」写入" % len(added))

    def save_all():
        if not added:
            win.destroy(); return
        try:
            import csv as _c
            rows = []
            with (APP_DIR / "glossary.csv").open(encoding="utf-8-sig", newline="") as fh:
                for r in _c.DictReader(fh):
                    s = (r.get("source") or "").strip()
                    t = (r.get("target") or "").strip()
                    d = (r.get("domain") or "").strip()
                    if s and t:
                        rows.append((s, t, d))
            ex = {s.lower() for s, _t, _d in rows}
            n = 0
            for s, t in added:
                if s.lower() not in ex:
                    rows.append((s, t, ""));
                    ex.add(s.lower()); n += 1
            import io as _io
            buf = _io.StringIO()
            w = _c.writer(buf, lineterminator="\n")
            w.writerow(["source", "target", "tgt_lng", "domain"])
            for s, t, d in rows:
                w.writerow([s, t, "zh_cn", d])
            (APP_DIR / "glossary.csv").write_text(buf.getvalue(), encoding="utf-8-sig")
            toast("已新增 %d 条术语（共 %d 条）" % (n, len(rows)), "ok")
        except Exception as exc:
            toast("保存失败：%s" % exc, "err")
        win.destroy()

    bar = tk.Frame(win, bg=C["bg"])
    bar.pack(fill="x", padx=12, pady=(0, 12))
    flat_btn(bar, "加入术语表", add_one, "primary").pack(side="left")
    flat_btn(bar, "自动翻译建议", auto_sug).pack(side="left", padx=6)
    flat_btn(bar, "保存到术语表", save_all, "primary").pack(side="right")
    win.protocol("WM_DELETE_WINDOW", save_all)


def delete_history_dialog(root, C, _f, flat_btn, toast, A,
                           tw, items, files, refresh):
    """删除历史记录对话框 —— 可选同时删掉输出文件，支持撤销。

    参数说明（原先是闭包变量，提取后必须显式传）：
      tw      历史表格（用来判断选中项，可选）
      items   要删除的历史记录（list[dict]）
      files   这些记录对应的输出文件（list[Path]）
      refresh 删除后刷新列表的回调
    """

    dlg = tk.Toplevel(root)
    dlg.title("删除历史记录")
    dlg.geometry("500x420")
    dlg.minsize(460, 380)
    dlg.transient(root)
    try:
        dlg.configure(bg=C["bg"])
    except Exception:
        pass

    # ★ 底部按钮栏必须【先 pack】——
    # 之前是先 pack 内容（expand=True）再 pack 按钮栏，
    # 内容把剩余空间吃光，按钮栏被挤出对话框外，
    # 用户就看不到"删除/取消"两个键（用户反馈"没有确定按键"）。
    bar = tk.Frame(dlg, bg=C["bg_alt"])
    bar.pack(side="bottom", fill="x")
    dlg._bar = bar

    f = tk.Frame(dlg, bg=C["bg"])
    f.pack(side="top", fill="both", expand=True, padx=16, pady=(14, 8))

    tk.Label(f, text="将删除 %d 条历史记录" % len(items),
             bg=C["bg"], fg=C["text"],
             font=_f(10, "bold")).pack(anchor="w")
    names = [str(it.get("source_name") or "") for it in items]
    tk.Label(f, text="\n".join("· " + n for n in names[:5])
             + ("\n…还有 %d 条" % (len(names) - 5) if len(names) > 5 else ""),
             bg=C["bg"], fg=C["muted"], justify="left",
             font=_f(9)).pack(anchor="w", pady=(6, 0))

    tk.Label(f, text="这些记录对应 %d 个输出文件：" % len(files),
             bg=C["bg"], fg=C["text_sub"],
             font=_f(9)).pack(anchor="w", pady=(12, 2))
    fnames = "\n".join("· " + p.name for p in files[:4])
    if len(files) > 4:
        fnames += "\n…还有 %d 个" % (len(files) - 4)
    tk.Label(f, text=fnames or "（没有可删的文件）",
             bg=C["bg"], fg=C["muted"], justify="left",
             font=_f(8)).pack(anchor="w")

    # 默认【勾选】—— 用户点"删除"通常就是想连文件一起清掉。
    # 不勾选时文件会留着（下面有明确提示）。
    del_files = tk.BooleanVar(value=bool(files))
    tk.Checkbutton(f, text="同时删除这些输出文件（不可恢复）",
                   variable=del_files, bg=C["bg"], fg=C["text"],
                   selectcolor=C["card"], activebackground=C["bg"],
                   font=_f(9)).pack(anchor="w", pady=(10, 0))

    def do_delete():
        removed_files, failed = 0, []
        # ② 撤销用：记下删前的完整历史 + 文件内容
        old_hist = A.load_history()
        # 只有「不删文件」时才能真正撤销；
        # 删了文件就把文件内容先读进内存（通常几十 KB，可接受）
        file_backup = []
        if del_files.get():
            for p in files:
                try:
                    file_backup.append((p, p.read_bytes()))
                except Exception:
                    pass          # 读不出来就放弃这条的撤销能力
            for p in files:
                try:
                    p.unlink()
                    removed_files += 1
                except Exception as exc:
                    failed.append("%s（%s）" % (p.name, exc))
        # 从历史里去掉这些条
        remain = [x for x in A.load_history() if x not in items]
        try:
            A.HISTORY_FILE.write_text(
                __import__("json").dumps(remain, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:
            toast("写历史失败：%s" % exc, "err")
        dlg.destroy()
        refresh()
        msg = "已删除 %d 条记录" % len(items)
        if removed_files:
            msg += "，连同 %d 个文件" % removed_files

        # ② 撤销：恢复历史 + 把文件写回去
        def _undo():
            try:
                A.HISTORY_FILE.write_text(
                    __import__("json").dumps(old_hist,
                                             ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:
                pass
            restored = 0
            for p, blob in file_backup:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(blob)
                    restored += 1
                except Exception:
                    pass
            try:
                refresh()
            except Exception:
                pass
            tail = ("，%d 个文件已还原" % restored) if restored else ""
            toast("已撤销删除%s" % tail, "ok")

        can_undo = bool(file_backup) or not del_files.get()
        if can_undo:
            toast(msg + "（可撤销）", "ok", action=_undo)
        else:
            toast(msg, "ok")
        if failed:
            messagebox.showwarning("部分文件删不掉",
                                   "以下文件被占用或权限不足：\n" +
                                   "\n".join(failed[:6]), parent=root)

    flat_btn(bar, "确认删除", do_delete, "danger").pack(
        side="right", padx=(8, 12), pady=8)
    flat_btn(bar, "取消", dlg.destroy).pack(side="right", pady=8)
    tk.Label(bar, text="删除后不可恢复", bg=C["bg_alt"], fg=C["muted"],
             font=_f(8)).pack(side="left", padx=12)
    try:
        dlg.grab_set()
    except Exception:
        pass


def attach_log_menu(txt, root, C, _f, toast, A, APP_DIR, make_diagnostic):
    """给日志 Text 挂上右键菜单（复制 / 保存 / 打开文件夹 / 诊断 / 清空）。

    参数说明（原先是闭包变量）：
      txt             日志 Text 控件
      make_diagnostic 生成诊断报告的回调（可为 None）
    """
    def _log_text() -> str:
        try:
            return txt.get("1.0", "end").rstrip()
        except Exception:
            return ""

    def _log_copy_all():
        t = _log_text()
        if not t:
            toast("日志是空的", "warn")
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(t)
            toast("已复制 %d 行日志" % len(t.splitlines()), "ok")
        except Exception as exc:
            toast("复制失败：%s" % exc, "err")

    def _log_copy_sel():
        try:
            sel = txt.get("sel.first", "sel.last")
        except Exception:
            sel = ""
        if not sel:
            toast("先选中要复制的文字", "warn")
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(sel)
            toast("已复制选中内容", "ok")
        except Exception as exc:
            toast("复制失败：%s" % exc, "err")

    def _log_save():
        t = _log_text()
        if not t:
            toast("日志是空的", "warn")
            return
        import datetime as _dt
        default = "翻译日志-%s.txt" % _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        f = filedialog.asksaveasfilename(
            title="保存日志", defaultextension=".txt",
            initialfile=default,
            initialdir=A._existing_dir(None, Path.home() / "Documents", APP_DIR),
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not f:
            return
        try:
            Path(f).write_text(t, encoding="utf-8")
            toast("日志已保存：%s" % Path(f).name, "ok")
        except Exception as exc:
            toast("保存失败：%s" % exc, "err")

    def _log_open_folder():
        try:
            A.open_file_with(APP_DIR, A.load_config(), mode="folder")
        except Exception as exc:
            toast("打开失败：%s" % exc, "err")

    def _log_diag():
        try:
            if callable(make_diagnostic):
                make_diagnostic()
        except Exception:
            pass

    def _log_clear():
        if not messagebox.askyesno("清空日志", "清空当前显示的日志？", parent=root):
            return
        try:
            txt.delete("1.0", "end")
            toast("日志已清空", "ok")
        except Exception:
            pass

    log_menu = tk.Menu(root, tearoff=0, bg=C["card"], fg=C["text"],
                       activebackground=C["accent"],
                       activeforeground="#ffffff",
                       borderwidth=0, relief="flat")
    log_menu.add_command(label="复制全部", command=_log_copy_all)
    log_menu.add_command(label="复制选中", command=_log_copy_sel)
    log_menu.add_separator()
    log_menu.add_command(label="保存为文本文件…", command=_log_save)
    log_menu.add_command(label="打开程序文件夹", command=_log_open_folder)
    log_menu.add_separator()
    log_menu.add_command(label="生成诊断报告…", command=_log_diag)
    log_menu.add_command(label="清空日志", command=_log_clear)

    def _show_log_menu(e):
        try:
            log_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                log_menu.grab_release()
            except Exception:
                pass
        return "break"

    txt.bind("<Button-3>", _show_log_menu, add="+")

    def _log_select_all(_e=None):
        try:
            txt.tag_add("sel", "1.0", "end-1c")
            txt.mark_set("insert", "1.0")
        except Exception:
            pass
        return "break"

    txt.bind("<Control-a>", _log_select_all, add="+")
    txt.bind("<Control-A>", _log_select_all, add="+")
    return log_menu

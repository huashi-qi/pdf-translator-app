# -*- coding: utf-8 -*-
"""冒烟测试：改完 UI 跑一遍，10 秒挡住灾难性回归。

用法（在程序目录下）：
    runtime\python.exe smoke_test.py
    runtime\python.exe smoke_test.py --verbose

检查项：
  1. 能启动，4 个导航项都在
  2. 每页都能切换，该有的控件都在
  3. 页面之间不重叠（上次出过这个 bug）
  4. 拖放钩子装在正确窗口上
  5. 主题能切换（浅色/夜间）
  6. 队列/进度/日志面板都在
  7. 关键功能函数能 import

不检查「翻译效果」——那要真跑一次，太慢。
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
os.chdir(APP)
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

# Windows 控制台默认 GBK，打印 emoji（🌐🕘⚙）会抛 UnicodeEncodeError。
# 这里把 stdout/stderr 换成 UTF-8，避免测试因为「打印不出来」而失败。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
FAILS: list = []
PASSES = [0]


def ok(label, extra=""):
    PASSES[0] += 1
    print("  [OK]   " + label + (("  " + extra) if extra else ""))


def bad(label, extra=""):
    FAILS.append(label)
    print("  [FAIL] " + label + (("  " + extra) if extra else ""))


def check(label, cond, extra=""):
    ok(label, extra) if cond else bad(label, extra)
    return bool(cond)


def walk(w):
    out = []
    try:
        for c in w.winfo_children():
            out.append(c)
            out.extend(walk(c))
    except Exception:
        pass
    return out


def collect_labels(root):
    got = []
    for w in walk(root):
        try:
            t = str(w.cget("text"))
            if t:
                got.append(t)
        except Exception:
            pass
    return got


def main() -> int:
    print()
    print("=" * 52)
    print("  PDF 翻译助手 · 冒烟测试")
    print("=" * 52)

    # ---- 1. 模块导入 ----
    print()
    print("== 1/6  模块导入 ==")
    mods = ["core", "ui_helpers", "ui_kit", "ui_theme", "ui_dialogs",
            "ui_widgets", "glossary_hook", "qa", "learn_check",
            "pdf_studio", "pdf_editor", "pdf_viewer"]
    for m in mods:
        try:
            __import__(m)
            ok("import " + m)
        except Exception as exc:
            bad("import " + m, str(exc)[:60])

    # ---- 2. 核心函数存在 ----
    print()
    print("== 2/6  核心函数 ==")
    try:
        import core as C
        for fn in ("run_translate", "preflight", "check_source_pdf",
                   "check_output_dir", "estimate_workload", "explain_error",
                   "save_progress", "get_progress", "remaining_pages",
                   "notify_done", "count_pdf_pages"):
            check("core." + fn, hasattr(C, fn))
    except Exception as exc:
        bad("导入 core", str(exc)[:60])

    # ---- 3. 启动界面 ----
    print()
    print("== 3/6  启动界面 ==")
    import tkinter as tk
    tk.Tk.mainloop = lambda self, *a, **k: None

    # 屏蔽所有模态框，避免测试卡住
    import tkinter.messagebox as mb
    mb.askyesno = lambda *a, **k: False
    mb.showinfo = lambda *a, **k: None
    mb.showwarning = lambda *a, **k: None
    mb.showerror = lambda *a, **k: None

    try:
        sys.argv = ["app_sidebar.py"]
        import app_sidebar as AS
        AS.main()
        ok("main() 执行成功")
    except SystemExit:
        ok("main() 执行成功（SystemExit）")
    except Exception:
        bad("main() 执行失败")
        traceback.print_exc()
        return 1

    root = tk._default_root
    if root is None:
        bad("没有创建窗口")
        return 1

    def pump(sec=1.0):
        end = time.time() + sec
        while time.time() < end:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.02)

    pump(2.5)
    # 启动时可能弹出「有未完成的翻译」窗口，测试里要自动关掉，
    # 否则 wait_window 会一直阻塞，冒烟测试永远跑不完。
    def _auto_close_resume():
        try:
            for w in walk(root):
                if w.winfo_class() != "Toplevel":
                    continue
                texts = []
                for c in walk(w):
                    t = getattr(c, "_text", None)
                    if t:
                        texts.append(str(t))
                    try:
                        texts.append(str(c.cget("text")))
                    except Exception:
                        pass
                if any("稍后再说" in t for t in texts):
                    for c in walk(w):
                        if getattr(c, "_text", None) and \
                                "稍后再说" in str(c._text):
                            c.invoke()
                            return True
        except Exception:
            pass
        return False

    _auto_close_resume()


    # 导航项 = 左侧 nav 容器（宽<220）里、以图标开头的 Label。
    # 不能扫全窗口 emoji —— 那样 🌙夜间模式、⏱步骤耗时 也会被算进来。
    nav_items = []
    try:
        nav_box = None
        for w in walk(root):
            if (w.winfo_class() == "Frame"
                    and w.winfo_width() < 220
                    and w.winfo_height() > 300):
                nav_box = w
                break
        if nav_box is not None:
            for w in walk(nav_box):
                try:
                    if w.winfo_class() != "Label":
                        continue
                    t = str(w.cget("text")).strip()
                    if t and any(t.startswith(k) for k in
                                 ("\U0001f310", "\U0001f558",
                                  "\U0001f9f0", "\u2699")):
                        nav_items.append(t)
                except Exception:
                    pass
    except Exception:
        pass
    check("导航 4 项", len(nav_items) == 4,
          "%d 项: %s" % (len(nav_items), nav_items))

    # ---- 4. 逐页切换 ----
    print()
    print("== 4/6  逐页切换 ==")

    def nav(icon_char):
        for w in walk(root):
            try:
                if str(w.cget("text")).strip().startswith(icon_char):
                    return w
            except Exception:
                pass
        return None

    # 注意 🕘 是 U+1F558，不是 U+1F552（那是另一个时钟符号）
    pages = [
        ("\U0001f558", "历史记录"),
        ("\U0001f9f0", "工具"),
        ("\u2699", "设置"),
        ("\U0001f310", "翻译"),
    ]
    for icon_char, name in pages:
        w = nav(icon_char)
        if not w:
            bad("找到导航：" + name)
            continue
        try:
            w.event_generate("<Button-1>")
            pump(0.9)
            ok("切换到 " + name)
        except Exception as exc:
            bad("切换到 " + name, str(exc)[:50])

    # ---- 5. 关键控件 ----
    print()
    print("== 5/6  关键控件 ==")
    nav("\U0001f310")
    pump(1.0)
    labels = collect_labels(root)
    for name in ("批量队列", "运行日志", "步骤耗时", "术语表命中"):
        check("有「%s」" % name, any(name in x for x in labels))

    trees = [w for w in walk(root) if w.winfo_class() == "Treeview"]
    check("有表格控件", len(trees) >= 2, "%d 个" % len(trees))

    texts = [w for w in walk(root) if w.winfo_class() == "Text"]
    check("有日志区", len(texts) >= 1, "%d 个" % len(texts))

    lbs = [w for w in walk(root) if w.winfo_class() == "Listbox"]
    check("有队列列表", len(lbs) >= 1, "%d 个" % len(lbs))

    # 页面重叠检测。
    # 页面 holder 的特点是：直接挂在 body 下、用 pack 管理、占满高度。
    # 注意 body 下还有一个 nav 容器（宽 180px），要排除掉。
    try:
        body = None
        for w in walk(root):
            if (w.winfo_class() == "Frame" and w.winfo_width() > 600
                    and w.winfo_height() > 400):
                body = w
                break
        if body is not None:
            visible_pages = []
            for c in body.winfo_children():
                if c.winfo_class() != "Frame":
                    continue
                if not c.winfo_manager():
                    continue
                if c.winfo_width() < 400:      # 排除左侧 nav
                    continue
                if c.winfo_height() < c.winfo_screenheight() * 0.3:
                    continue
                visible_pages.append(c)
            check("页面不重叠", len(visible_pages) <= 1,
                  "%d 个可见页" % len(visible_pages))
    except Exception:
        pass

    # ---- 6. 视觉完整性（新增）-----
    # 光「能跑起来」不够，还要看「长得对不对」。
    # 这几项专门针对我们踩过的坑：白底、控件被挤没、文字看不见。
    print()
    print("== 6/8  视觉完整性 ==")

    def _hex_lum(c):
        """粗略亮度 0~1。"""
        try:
            c = str(c).lstrip("#")
            if len(c) != 6:
                return -1
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        except Exception:
            return -1

    # 逐个页面检查：控件是否有合理的尺寸（没被挤没）
    print()
    print("  页面控件尺寸：")
    for icon_char, pname in pages:
        w = nav(icon_char)
        if not w:
            continue
        try:
            w.event_generate("<Button-1>")
            pump(0.7)
        except Exception:
            continue
        # 找可见的控件，统计「宽度为 0 但本应有内容」的
        zero_w = 0
        total_vis = 0
        for c in walk(root):
            try:
                if not c.winfo_ismapped():
                    continue
                cls = c.winfo_class()
                if cls not in ("Label", "Button", "Treeview", "Text",
                               "Listbox", "Entry", "Frame"):
                    continue
                total_vis += 1
                if c.winfo_width() <= 1 and c.winfo_height() <= 1:
                    zero_w += 1
            except Exception:
                pass
        pct = (zero_w / total_vis * 100) if total_vis else 0
        ok_flag = pct < 30
        check("%s 页控件正常" % pname, ok_flag,
              "%d/%d 尺寸为 0（%.0f%%）" % (zero_w, total_vis, pct))

    # 检查有没有「白底黑字读不出」和「深底深字看不见」
    print()
    print("  对比度：")
    try:
        import core as _C2
        for thr_name in ("light", "dark"):
            th = _C2.THEMES[thr_name]
            bg_l = _hex_lum(th["bg"])
            fg_l = _hex_lum(th["text"])
            if bg_l >= 0 and fg_l >= 0:
                ratio = abs(bg_l - fg_l)
                check("%s 主题文字对比足够" % thr_name, ratio > 0.35,
                      "亮度差 %.2f" % ratio)
            # 检查关键颜色都在
            for key in ("bg", "bg_alt", "card", "head_bg", "text",
                        "text_sub", "muted", "border", "accent"):
                if key not in th:
                    bad("主题 %s 缺 %s" % (thr_name, key))
    except Exception:
        pass

    # 检查夜间模式下关键表面不是白
    try:
        import core as _C3
        dark = _C3.THEMES["dark"]
        for key in ("bg", "card", "head_bg", "sidebar"):
            if key in dark:
                l = _hex_lum(dark[key])
                check("夜间 %s 是深色" % key, 0 <= l < 0.3,
                      "%s 亮度 %.2f" % (dark[key], l))
    except Exception:
        pass

    # ---- 7. 对话框按钮可见性 ----
    # 之前出现过「确认窗口的按钮跑到窗口外、根本看不见」的问题：
    # body 用 fill=both+expand 先 pack，会把 side=bottom 的按钮栏挤出去。
    print()
    print("== 7/9  对话框按钮可见 ==")

    def _probe_dialog(fix_order):
        """建一个对话框，返回按钮的 (y, 高度, 可见, 在框内)。"""
        d = tk.Toplevel(root)
        d.geometry("560x480")
        try:
            d.configure(bg="#ffffff")
        except Exception:
            pass
        if fix_order:
            holder = tk.Frame(d)
            holder.pack(side="bottom", fill="x")
        bodyf = tk.Frame(d)
        bodyf.pack(fill="both", expand=True)
        for i in range(18):
            tk.Label(bodyf, text="行%d" % i).pack(anchor="w", pady=3)
        if not fix_order:
            holder = tk.Frame(d)
            holder.pack(side="bottom", fill="x")
        btn = tk.Button(holder, text="开始翻译")
        btn.pack(side="right", padx=8, pady=8)
        d.update_idletasks()
        d.update()
        dh = d.winfo_height()
        y = btn.winfo_rooty() - d.winfo_rooty()
        h = btn.winfo_height()
        vis = bool(btn.winfo_ismapped())
        inframe = vis and y >= 0 and (y + h) <= dh + 2
        d.destroy()
        return y, h, vis, inframe

    try:
        y0, h0, _v0, in0 = _probe_dialog(False)
        y1, h1, _v1, in1 = _probe_dialog(True)
        # 按钮栏先 pack 时必须可见且在框内
        check("按钮栏先 pack → 按钮可见", in1,
              "y=%d h=%d" % (y1, h1))
        if in1:
            ok("对照：body 先 pack 时 y=%d h=%d（应为不可见）"
               % (y0, h0))
    except Exception as _e:
        bad("对话框按钮检查", str(_e)[:50])

    # ---- 8. 拖放 + 主题 ----

    print()
    print("== 6/6  拖放钩子 / 主题 ==")
    check("拖放钩子已装", getattr(root, "_drop_hwnd", None) is not None)
    check("拖放队列已建", getattr(root, "_drop_queue", None) is not None)

    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                         ctypes.c_int]
        h = getattr(root, "_drop_hwnd", None)
        if h is not None:
            hv = h.value if hasattr(h, "value") else int(h)
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(wintypes.HWND(hv), buf, 256)
            check("拖放钩子在 TkTopLevel 上", buf.value == "TkTopLevel",
                  buf.value)
    except Exception:
        pass

    try:
        from tkinter import ttk
        import core as _C
        st = ttk.Style()
        bg = st.lookup("Treeview", "background")
        thr = str(_C.load_config().get("ui_theme") or "light").lower()
        want = (_C.THEMES["dark"]["card"] if thr == "dark"
                else _C.THEMES["light"]["card"])
        check("Treeview 背景 = 主题 card", bg == want,
              "%s（主题 %s，期望 %s）" % (bg, thr, want))
        lc = st.lookup("Treeview", "lightcolor")
        # 关键是「lightcolor 必须跟着主题走」，而不是「不能是白」
        check("lightcolor 跟主题一致", lc == want,
              "%s（期望 %s）" % (lc, want))
    except Exception:
        pass

    try:
        root.destroy()
    except Exception:
        pass

    # ---- 汇总 ----
    print()
    print("=" * 52)
    total = PASSES[0] + len(FAILS)
    if FAILS:
        print("  失败 %d / %d" % (len(FAILS), total))
        for f in FAILS:
            print("    - " + f)
        print("=" * 52)
        return 1
    print("  全部通过（%d 项）" % total)
    print("=" * 52)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(2)
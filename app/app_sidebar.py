# -*- coding: utf-8 -*-
r"""侧边栏版界面 —— 独立入口（新版）。

用法：双击「启动-侧边栏版.vbs」，或运行：
    runtimepython.exe app_sidebar.py

与经典版的关系
--------------
两者共用同一套后端：
    · 翻译：调用 core.py 的 run_translate / build_worker_args
    · 术语表：glossary_hook
    · 历史：core.py 的 load_history / add_history
    · 工具：pdf_studio（自由编辑器）/ pdf_editor / pdf_viewer / qa / learn_check
只是把「顶部工具栏 + 标签页」换成「左侧导航 + 右侧内容区」。

经典版完全不受影响，随时可以回去用。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# 后端能力全在 core.py（无 UI 依赖，可单测）
import core as A  # noqa: E402
import ui_helpers as H  # noqa: E402
import ui_kit as K  # noqa: E402
import ui_dialogs as D  # noqa: E402
import ui_theme as T  # noqa: E402
import page_settings as PS  # noqa: E402
import page_history as PH  # noqa: E402
import page_translate as PT  # noqa: E402


def main() -> int:
    A.enable_dpi_awareness()
    dpi = A.get_real_dpi()

    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title("PDF 翻译助手 · 侧边栏版")
    # ⑦ 记住上次的窗口大小和位置
    _ui_cfg = A.load_config()
    _geom = str(_ui_cfg.get("window_geometry") or "")
    root.geometry(_geom if _geom else "1040x700")
    root.minsize(880, 600)

    def _save_geometry(_e=None):
        try:
            c = A.load_config()
            c["window_geometry"] = root.geometry()
            A.save_config(c)
        except Exception:
            pass

    try:
        root.bind("<Configure>", lambda e: (
            _save_geometry() if e.widget is root else None))
    except Exception:
        pass

    # ④ 尝试让窗口有圆角（Windows 11 支持；失败就忽略）
    # ⑧ 关闭时最小化到托盘（而不是退出）
    def _setup_tray():
        """用 Windows Shell 通知区图标实现托盘。

        用 ctypes 直接调 Shell_NotifyIcon，不引入 pystray 之类的新依赖。
        双击托盘图标恢复窗口；右键菜单可退出。
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32

            NIM_ADD, NIM_DELETE = 0, 2
            NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
            WM_APP = 0x8000
            WM_TRAYICON = WM_APP + 20
            WM_LBUTTONDBLCLK = 0x0203
            WM_RBUTTONUP = 0x0205
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            LR_DEFAULTSIZE = 0x0040

            class NOTIFYICONDATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                ]

            hwnd = wintypes.HWND(int(root.frame(), 16)
                                 if str(root.frame()).startswith("0x")
                                 else int(root.frame()))
            ico_path = APP_DIR / "app.ico"
            hicon = 0
            if ico_path.exists():
                hicon = user32.LoadImageW(None, str(ico_path), IMAGE_ICON,
                                          0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = hicon
            nid.szTip = "PDF 翻译助手"
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                return False

            def _remove():
                try:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                except Exception:
                    pass

            root._tray_remove = _remove
            return True
        except Exception:
            return False

    try:
        root.after(200, lambda: H.round_window(root))
    except Exception:
        pass
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass

    # 配色（支持「跟随系统」）
    _cfg0 = A.load_config()
    # ⑨⑩ 界面缩放与字号
    try:
        _scale = float(_cfg0.get("ui_scale") or 1.0)
        _scale = max(0.8, min(1.6, _scale))
    except Exception:
        _scale = 1.0
    try:
        _fs = int(_cfg0.get("ui_font_size") or 10)
        _fs = max(8, min(14, _fs))
    except Exception:
        _fs = 10
    # tk scaling 控制整体缩放：在 DPI 基础上再乘用户系数
    try:
        root.tk.call("tk", "scaling", (dpi / 72.0) * _scale)
    except Exception:
        pass

    mode = str(_cfg0.get("ui_theme") or "light").lower()
    if mode == "auto":
        mode = "dark" if H.system_prefers_dark() else "light"
    if mode not in A.THEMES:
        mode = "light"
    C = dict(A.THEMES[mode])

    FONT = "Microsoft YaHei UI"
    # ⑩ 字号基准值。界面上的字号都写成 _f(9) 这种形式，
    # 实际大小 = 基准 + (用户设置 - 10)，这样"字号"设置才真的全局生效。
    # （之前 61 处控件把字号写死成 font=(FONT, 9)，改设置根本没反应）
    _FS = _fs

    def _f(size, *styles):
        """按用户字号设置换算出实际字号。

        size 是"设计基准字号"（界面按 10 号设计）。
        用户把字号设成 13，就是所有文字统一 +3。
        """
        try:
            delta = int(_FS) - 10
        except Exception:
            delta = 0
        real = max(6, int(size) + delta)
        return (FONT, real) + tuple(styles) if styles else (FONT, real)

    def _apply_style(theme: dict) -> None:
        return T.apply_style(theme, dpi=dpi, font_family=FONT, font_size=_FS)

    # 已经做过主题化的控件 id（用 id() 而非 widget 对象，避免持有引用）
    # 为什么不每次重新走一遍：每个控件要 configure 5~9 次，
    # 而每次 configure 都是一次 Tcl 往返（实测整页 400ms 几乎全花在这）。
    # 页面是缓存的，同一批控件主题化一次就够了。
    _themed_ids: set = set()

    def _theme_native_widgets(w, force: bool = False) -> None:
        return T.theme_native_widgets(w, C, _themed_ids, force=force)

    def _restyle_tree(w, mapping: dict) -> int:
        """遍历控件树，把旧主题色原地替换为新主题色。

        为什么这么做而不是重建界面：
        侧边栏版的界面代码是一个上千行的大函数，重新缩进成子函数
        改动面太大、容易出错。而"换色"只需按主题色的对应关系替换，
        风险小、还能保留所有状态（选中项、滚动位置、滚动条等）。
        """
        n = 0
        try:
            kids = w.winfo_children()
        except Exception:
            return 0
        for c in kids:
            for opt in ("bg", "background", "fg", "foreground",
                        "fieldbackground", "insertbackground",
                        "selectbackground", "activebackground",
                        "highlightbackground", "highlightcolor", "troughcolor"):
                try:
                    cur = str(c.cget(opt))
                except Exception:
                    continue
                new = mapping.get(cur.lower())
                if new:
                    try:
                        c.configure(**{opt: new})
                        n += 1
                    except Exception:
                        pass
            n += _restyle_tree(c, mapping)
        return n

    def _restyle_canvas_items(w, mapping: dict) -> int:
        """Canvas 上的图元也要换色（圆角按钮就是 Canvas 画的）。"""
        n = 0
        try:
            kids = w.winfo_children()
        except Exception:
            return 0
        for c in kids:
            if isinstance(c, tk.Canvas):
                try:
                    for item in c.find_all():
                        typ = c.type(item)
                        for opt in ("fill", "outline"):
                            try:
                                cur = str(c.itemcget(item, opt))
                            except Exception:
                                continue
                            new = mapping.get(cur.lower())
                            if new:
                                c.itemconfigure(item, **{opt: new})
                                n += 1
                except Exception:
                    pass
            n += _restyle_canvas_items(c, mapping)
        return n

    # ---- 需要一个可重新赋值的地方存"当前主题/字号/缩放" ----
    # 之前这些是 main() 里的局部变量，切换后从不更新，
    # 导致"第二次切回白天"被判定成"没变化"而什么都不做。
    ui_state = {"theme": "", "scale": 1.0, "fs": 10}

    def _apply_scaling(scale: float) -> None:
        """重新应用界面缩放。

        关键：tk 的 scaling 改了之后，【已经创建】的控件不会自动变，
        必须让 Tk 重新计算 —— 靠销毁重建界面来生效。
        """
        try:
            root.tk.call("tk", "scaling", (dpi / 72.0) * float(scale))
        except Exception:
            pass

    # ================= 水波过渡动画 =================
    # 主题切换原来是"啪"地一下重建，很生硬。这里做成：
    #   1) 先给旧界面拍张照
    #   2) 后台重建出新界面
    #   3) 再给新界面拍张照
    #   4) 用一条正弦波把旧图"冲"成新图 —— 像海水漫过去
    # Tk 不支持控件透明度，所以只能用"截图 + 合成"来实现真正的渐变。
    def _rebuild(keep_page: bool = True):
        """真正重建整个界面 —— 主题 / 字号 / 缩放 / 预设 全都靠它生效。

        为什么必须是"销毁 + 重建"而不是"原地换色"：
          · 字号：写死在 62 处控件的 font= 里，换色换不到
          · 缩放：tk scaling 只对新控件生效
          · 预设：下拉框/输入框的初值来自配置，不重建就一直显示旧值
        重建期间会先隐藏旧界面，避免看到闪烁的中间态。
        """
        old_page = cur.get("key") if keep_page else "translate"

        # 主题切换 = 直接重建，不做动画。
        # 之前的圆形扩散动画始终会露出中间态（窗口重排时无法完全遮盖），
        # 与其做个能看出瑕疵的动画，不如干脆利落地瞬间切换。
        try:
            root.config(cursor="watch")
            root.update_idletasks()
        except Exception:
            pass

        cfg = A.load_config()
        m = H.resolve_theme(cfg, A.THEMES)

        # 缩放与字号
        # 注意：_FS 是 main() 的局部变量，必须用 nonlocal。
        # 用 global 会新建一个模块级变量，闭包里的 _FS 永远不变 ——
        # 字号改了没反应就是这个原因。
        nonlocal _FS
        try:
            sc = max(0.8, min(1.6, float(cfg.get("ui_scale") or 1.0)))
        except Exception:
            sc = 1.0
        try:
            fs = max(8, min(14, int(cfg.get("ui_font_size") or 10)))
        except Exception:
            fs = 10
        _FS = fs
        ui_state["theme"] = m
        ui_state["scale"] = sc
        ui_state["fs"] = fs
        _apply_scaling(sc)

        # 换主题
        C.clear()
        C.update(dict(A.THEMES[m]))
        _apply_style(C)
        try:
            root.configure(bg=C["bg"])
        except Exception:
            pass

        # ---- 销毁旧界面（只留 root）----
        for ch in list(root.winfo_children()):
            try:
                ch.destroy()
            except Exception:
                pass
        try:
            page_cache.clear()
        except Exception:
            pass
        try:
            page_built.clear()
        except Exception:
            pass
        widgets.clear()
        items.clear()

        # ---- 重建骨架与所有页面 ----
        _build_shell()
        _register_pages()
        _build_footer()

        # 回到切换前的页面（若存在），否则翻译页
        target = old_page if old_page in items else "translate"
        show(target)

        try:
            root.config(cursor="")
            root.update_idletasks()
        except Exception:
            pass

    _apply_style(C)
    try:
        root.configure(bg=C["bg"])
    except Exception:
        pass

    # ================= 骨架（可重复构建 —— 重建界面时复用）=================
    NAV_W = 180
    ui_ref = {"nav": None, "body": None}

    def _build_shell():
        """建导航栏 + 内容区。重建界面时会被再次调用。"""
        shell = tk.Frame(root, bg=C["bg"])
        shell.pack(fill="both", expand=True)
        nav = tk.Frame(shell, bg=C["sidebar"], width=NAV_W)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        body = tk.Frame(shell, bg=C["bg"])
        body.pack(side="left", fill="both", expand=True)
        ui_ref["nav"] = nav
        ui_ref["body"] = body

        tk.Label(nav, text="PDF 翻译助手", bg=C["sidebar"], fg=C["text"],
                 font=_f(11, "bold"), anchor="w").pack(
            fill="x", padx=16, pady=(16, 0))
        tk.Label(nav, text="侧边栏版", bg=C["sidebar"], fg=C["muted"],
                 font=_f(8), anchor="w").pack(fill="x", padx=16, pady=(2, 10))
        tk.Frame(nav, bg=C["border"], height=1).pack(fill="x", padx=12, pady=(0, 8))
        return nav, body

    nav, body = _build_shell()

    # ================= 共享状态 =================
    ui_q: "queue.Queue" = queue.Queue()
    state = {"running": False, "last_dir": "", "t_start": 0.0}

    # 用户正在队列里回看的那个文件。
    # 非空时暂停「步骤耗时」的实时刷新 —— 否则会把他正在看的历史数据
    # 每 400ms 覆盖成当前正在翻译的那个文件。
    _viewing = {"name": "", "path": ""}
    # 控件注册表：key 带页面前缀（translate.prog / footer.status …）
    # 为什么加前缀：之前 show() 里会 widgets.clear()，
    # 用户一旦切到别的页面，翻译页的 prog/log 引用就被清掉，
    # 结果翻译时进度条和日志都不再更新 —— 这是"看不见进度"的根因。
    widgets: dict = {}
    _cur_ns = {"k": ""}         # 当前正在构建哪个页面
    _NS_NAMES = {"translate", "history", "tools", "settings", "footer"}

    def _key_ns(page_key: str) -> str:
        """页面 key → 控件命名空间。"""
        try:
            return page_key if page_key in _NS_NAMES else "translate"
        except Exception:
            return "translate"

    # 暴露给测试/调试用（正常运行时不会被外部改动）
    A._UI_STATE["ui_q"] = ui_q

    # 注册诊断函数：日志右键菜单和错误对话框都要用，
    # 必须在启动时就注册（懒加载的设置页可能还没建）。
    try:
        A._UI_STATE["make_diagnostic"] = lambda: PS.build_diagnostic(
            widgets, messagebox, A)
    except Exception:
        pass
    A._UI_STATE["widgets"] = widgets

    def log(msg):
        ui_q.put(("log", str(msg)))

    def set_status(text, color=None):
        ui_q.put(("status", str(text), color or C["ok"]))

    # 圆角按钮 / 卡片 / Toast 等自绘控件
    try:
        import ui_widgets as _W
    except Exception:
        _W = None

    # ================= 导航 =================
    items: dict = {}
    cur = {"key": None}

    def mk_item(key, icon, label, builder):
        nav = ui_ref["nav"]
        row = tk.Frame(nav, bg=C["sidebar"], cursor="hand2")
        row.pack(fill="x", padx=8, pady=1)
        bar = tk.Frame(row, bg=C["sidebar"], width=3)
        bar.pack(side="left", fill="y")
        lbl = tk.Label(row, text="%s   %s" % (icon, label), bg=C["sidebar"],
                       fg=C["text"], anchor="w", padx=9, pady=9, font=_f(10))
        lbl.pack(side="left", fill="x", expand=True)

        def enter(_e=None):
            if cur["key"] != key:
                row.configure(bg=C["bg_alt"]); lbl.configure(bg=C["bg_alt"])
        def leave(_e=None):
            if cur["key"] != key:
                row.configure(bg=C["sidebar"]); lbl.configure(bg=C["sidebar"])
        def click(_e=None):
            show(key, animate=True)
        for w in (row, lbl):
            w.bind("<Enter>", enter, add="+")
            w.bind("<Leave>", leave, add="+")
            w.bind("<Button-1>", click, add="+")
        items[key] = {"row": row, "lbl": lbl, "bar": bar, "build": builder}

    # ===== 页面缓存 =====
    # 之前每次切页都"销毁全部控件 + 重建"，导致明显卡顿：
    #   · 历史记录页重建要读文件、建 Treeview（几十毫秒）
    #   · 设置页重建要读配置
    #   · 术语表页重建要解析 1100 行 CSV
    # 而切换动画又强制 relayout，卡顿被放大。
    # 改成：每页只建一次，之后用 pack_forget/pack 切换（Tk 里这是瞬间的）。
    page_cache: dict = {}      # key -> Frame（已建好的页面）
    page_built: dict = {}      # key -> bool

    def _make_holder(key):
        """为一个页面创建（或复用）容器。

        用 pack / pack_forget 切换。这是最稳的方式：
          · 每个页面只建一次（page_built 缓存）
          · 切页只 pack_forget 旧页 + pack 新页
        试过 place + tkraise、place + place_forget 提速，都出现了
        「页面重叠 / 显示错乱」的问题，所以保持 pack 方式。
        """
        h = tk.Frame(ui_ref["body"], bg=C["bg"])
        page_cache[key] = h
        return h

    def show(key, animate=False):
        """切换页面。

        animate 默认 False —— 切页必须跟手，动画只是锦上添花，
        之前无条件播动画反而让每次点击都有延迟感。
        """
        if key not in items:
            return
        cur["key"] = key

        # 更新导航高亮：只改动【状态发生变化】的那两项。
        # 原来每次都遍历 4 项 × 3 个 configure = 12 次 Tk 往返，
        # 而切页时真正变化的只有「旧选中」和「新选中」两个。
        prev = cur.get("prev_key")
        if prev != key:
            for k in (prev, key):
                if not k:
                    continue
                it = items.get(k)
                if not it:
                    continue
                on = (k == key)
                bg = C["sidebar_on"] if on else C["sidebar"]
                fg = C["accent"] if on else C["text"]
                font = _f(10, "bold" if on else "normal")
                bar_bg = C["accent"] if on else C["sidebar"]
                try:
                    it["row"].configure(bg=bg)
                    it["lbl"].configure(bg=bg, fg=fg, font=font)
                    it["bar"].configure(bg=bar_bg)
                except Exception:
                    pass
            cur["prev_key"] = key

        # 隐藏当前显示的那一页（pack_forget：只摘掉这一页，不重建）
        old = cur.get("visible")
        if old and old != key:
            h_old = page_cache.get(old)
            try:
                if h_old is not None and h_old.winfo_exists() and h_old.winfo_manager():
                    h_old.pack_forget()
            except Exception:
                pass

        # 首次进入该页才构建
        holder = page_cache.get(key)
        if holder is None or not holder.winfo_exists():
            holder = _make_holder(key)

        _just_built = False
        if not page_built.get(key):
            try:
                # 注意：这里【不能】widgets.clear() —— 那会把其它页面
                # （尤其是翻译页的 prog/log）的控件引用一起清掉，
                # 导致翻译时进度条和日志都不再更新。
                _cur_ns["k"] = key
                items[key]["build"](holder)
                page_built[key] = True
                _just_built = True
            except Exception as exc:
                try:
                    for ch in list(holder.winfo_children()):
                        ch.destroy()
                except Exception:
                    pass
                tk.Label(holder, text="页面加载失败：%s" % exc, bg=C["bg"],
                         fg=C["danger"]).pack(padx=20, pady=20)
                traceback.print_exc()

        # 显示目标页
        try:
            holder.pack(fill="both", expand=True)
            cur["visible"] = key
        except Exception:
            pass

        # 只在【刚建好页面】时补一遍原生控件主题化。
        # 缓存页面的控件主题早已设置过，再走一遍纯属浪费 Tk 往返。
        if _just_built:
            try:
                _theme_native_widgets(holder)
            except Exception:
                pass

        # 注：切页动画已移除 —— 之前试过滑入效果，
        # 既拖慢点击响应，又曾引发显示错乱，故不再使用。

        # 切到某页时刷新它自己的动态内容（缓存页面需要手动刷新）
        try:
            fn = widgets.get(_key_ns(key) + ".on_enter")
            if callable(fn):
                fn()
        except Exception:
            pass

    def _invalidate(key):
        """让某页下次进入时重建（数据变了时用）。"""
        try:
            h = page_cache.pop(key, None)
            page_built.pop(key, None)
            if h is not None and h.winfo_exists():
                h.destroy()
        except Exception:
            pass

    def toast(text, kind="info", action=None, action_text="撤销"):
        """⑤ Toast 提示（不打断操作，几秒自动消失）。

        传 action 时右侧出现「撤销」按钮 —— 删除类操作可以反悔。
        """
        try:
            if _W is not None:
                _W.toast(root, C, text, kind=kind,
                         action=action, action_text=action_text)
        except Exception:
            pass

    def header(parent, title, subtitle=""):
        return K.make_header(parent, title, subtitle, C, _f)

    def card(parent, radius=12):
        return K.make_card(parent, C, _f, _W, radius=radius)

    def loading(parent, text="加载中…"):
        return K.make_loading(parent, text, C, _f)

    def empty_state(parent, icon, title, hint="", action=None,
                    action_text=""):
        return K.make_empty_state(parent, icon, title, C, _f, _W,
                                 hint=hint, action=action,
                                 action_text=action_text)

    def flat_btn(parent, text, cmd, kind="normal"):
        return K.make_flat_btn(parent, text, cmd, C, _f, _W, kind=kind)

    # 队列行的「状态 → 显示文字」映射。
    # 必须定义在 main() 作用域（不能放 page_translate 里），
    # 因为 _update_queue 是另一个函数，访问不到 page_translate 的局部变量。
    _QS = {
        "wait":  ("○ 等待", "muted"),
        "run":   ("▶ 进行中", "accent"),
        "ok":    ("✓ 完成", "ok"),
        "fail":  ("✗ 失败", "danger"),
        "skip":  ("— 跳过", "muted"),
    }

    # ================= 页面：翻译 =================
    def page_translate(parent):
        """翻译主页面（实现在 page_translate.py）。"""
        return PT.page_translate(
            parent, C, root, widgets, state, _viewing, dpi,
            _f, _fill_translate_panels, _update_queue,
            confirm_output, do_cancel, flat_btn, header, pick,
            preview_latest, recent_folder, set_status, start,
            toast, tk, ttk, messagebox, Path, A, D, PS,
            APP_DIR,
            lambda: A._UI_STATE.get("make_diagnostic", lambda: None)())


    # ================= 页面：历史 =================
    def page_history(parent):
        """历史记录页（实现在 page_history.py）。"""
        return PH.page_history(
            parent, C, root, widgets, items, mode,
            _f, flat_btn, header, log, set_status,
            start, toast, tk, ttk, messagebox,
            filedialog, simpledialog, os, Path, A)

    # 注：「步骤耗时」独立导航页已移除 —— 它合并进了翻译主界面的面板，
    # 而且现在是实时刷新的，不再需要单独一页来回切。

    def _fill_gloss_panel(hits: dict, n_terms: int = 0) -> None:
        """把术语命中填到「术语表命中」面板。"""
        try:
            gt = widgets.get("translate.gloss")
            if gt is not None:
                for i in gt.get_children():
                    gt.delete(i)
                # 反查表：注意 term_hits 的 key 是【中文】，
                # 而词库是 {英文: 中文}，所以要建"中文 → 英文"的反向表。
                # （之前直接 pairs.get(中文) 永远查不到，结果英文列全空、
                #   中文挤在英文那一列 —— 用户反馈的就是这个问题。）
                if _fill_gloss_panel._rev is None:
                    try:
                        import glossary_hook as _gh
                        _gh.load_glossary()
                        fwd = _gh._lookup() or {}
                        rev = {}
                        for en, cn in fwd.items():
                            if en and cn:
                                rev.setdefault(str(cn), str(en))
                        _fill_gloss_panel._rev = rev
                    except Exception:
                        _fill_gloss_panel._rev = {}
                rev = _fill_gloss_panel._rev
                for term, cnt in sorted(hits.items(),
                                        key=lambda x: -(x[1] or 0))[:30]:
                    t = str(term)
                    # term 是中文 → 查英文；若 term 本身就是英文（老数据）则反过来
                    if t in rev:
                        cn_txt, en_txt = t, rev[t]
                    else:
                        cn_txt, en_txt = "", t
                    gt.insert("", "end", values=(
                        en_txt, cn_txt, int(cnt)))
            gm = widgets.get("translate.gloss_sum")
            if gm is not None:
                if hits:
                    total_hit = sum(int(v or 0) for v in hits.values())
                    gm.configure(
                        text="命中 %d 个术语 · 共 %d 处（词库 %d 条）"
                             % (len(hits), total_hit, int(n_terms or 0)),
                        fg=C["ok"])
                else:
                    gm.configure(text="本次未命中（可能领域不同）", fg=C["muted"])
        except Exception:
            pass
    _fill_gloss_panel._rev = None

    def _fill_translate_panels(timing: dict) -> None:
        """把阶段耗时 + 术语命中填到翻译主界面的两个面板。

        之前这两块信息是打在「运行日志」里的纯文本，
        既和「步骤耗时」页重复，又污染日志 —— 现在改成结构化面板。
        """
        # ---- 步骤耗时 ----
        try:
            tw = widgets.get("translate.steps")
            if tw is not None:
                for i in tw.get_children():
                    tw.delete(i)
                stages = timing.get("stages") or []
                total = float(timing.get("total") or 0.0)
                for r in sorted(stages, key=lambda x: -(x.get("seconds") or 0)):
                    secs = float(r.get("seconds") or 0)
                    pct = float(r.get("percent") or 0)
                    tw.insert("", "end", values=(
                        A.stage_cn(str(r.get("name") or "?")),
                        "%.1f 秒" % secs, "%.1f%%" % pct,
                        "█" * max(1, int(pct / 2.5))))
            sm = widgets.get("translate.steps_sum")
            if sm is not None:
                tt = float(timing.get("total") or 0.0)
                n = len(timing.get("stages") or [])
                sm.configure(text="共 %.1f 秒 · %d 个阶段" % (tt, n)
                             if n else "（无数据）",
                             fg=C["text_sub"])
        except Exception:
            pass

        # ---- 术语表命中 ----
        # 术语表：复用统一的面板填充函数
        _fill_gloss_panel(timing.get("term_hits") or {},
                          int(timing.get("term_total") or 0))

    # ================= 页面：工具 =================
    def page_tools(parent):
        header(parent, "工具", "PDF 处理与检查，都不改动原文件")

        # 关键：工具卡片较多，必须可滚动。
        # 之前直接 pack 到固定高度的区域里，后面的卡片（术语表管理等）
        # 被裁在可视区外，用户根本看不到、也点不到。
        wrap = tk.Frame(parent, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=20, pady=(14, 18))
        cvs = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cvs.yview)
        cvs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)
        grid = tk.Frame(cvs, bg=C["bg"])
        wid = cvs.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.bind("<Configure>", lambda e: cvs.itemconfigure(wid, width=e.width))

        # 鼠标滚轮
        def _wheel(e):
            try:
                cvs.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        cvs.bind("<Enter>", lambda e: cvs.bind_all("<MouseWheel>", _wheel))
        cvs.bind("<Leave>", lambda e: cvs.unbind_all("<MouseWheel>"))

        tools = [
            ("✏️", "PDF 编辑器", "自由编辑：改文字 / 移动 / 白out / 画笔 / 图片 / 页面管理",
             _open_studio),
            ("📖", "术语表管理", "增删改术语、导入导出、搜索（内置 1100 条）", _open_glossary),
            ("🎓", "从 PDF 学术语", "自动提取高频专业词，免费引擎也给中文建议", _open_learner),
            ("🔧", "PDF 工具", "批量处理：合并 / 拆分 / 旋转 / 压缩 / 页码水印", _open_tools),
            ("🔍", "检查工具", "术语表自检 / 扫描检测 / 段落对照", _open_inspect),
            ("📄", "快速查看", "只读浏览 + 加标注（轻量）", _open_viewer),
        ]
        # 单列布局（用户偏好：一列更清楚），配合滚动条
        for icon, title, desc, cmd in tools:
            card = tk.Frame(grid, bg=C["card"], highlightbackground=C["border"],
                            highlightthickness=1, cursor="hand2")
            card.pack(fill="x", pady=5, padx=2)
            inner = tk.Frame(card, bg=C["card"])
            inner.pack(fill="x", padx=16, pady=13)
            tk.Label(inner, text=icon, bg=C["card"], fg=C["accent"],
                     font=_f(16)).pack(side="left", padx=(0, 12))
            tx = tk.Frame(inner, bg=C["card"])
            tx.pack(side="left", fill="x", expand=True)
            tk.Label(tx, text=title, bg=C["card"], fg=C["text"],
                     font=_f(11, "bold"), anchor="w").pack(anchor="w")
            tk.Label(tx, text=desc, bg=C["card"], fg=C["muted"],
                     font=_f(9), anchor="w", wraplength=460,
                     justify="left").pack(anchor="w")
            tk.Label(inner, text="›", bg=C["card"], fg=C["muted"],
                     font=_f(18)).pack(side="right")

            def bind_all(w, c=cmd):
                w.bind("<Button-1>", lambda e: c(), add="+")
                for ch in w.winfo_children():
                    bind_all(ch, c)
            bind_all(card)

    # ================= 页面：设置 =================
    def page_settings(parent):
        """设置页（实现在 page_settings.py）。"""
        return PS.page_settings(
            parent, C, root, widgets, _f, flat_btn, header,
            toast, show, _invalidate, _rebuild,
            A_open_settings, messagebox, ttk, tk,
            APP_DIR, A)

    # ================= 底部：状态 + 主题（可重建）=================
    def toggle_theme():
        """切换主题 —— 真正重建界面，立即生效，不重启进程。"""
        c = A.load_config()
        cur_t = H.resolve_theme(c, A.THEMES)
        c["ui_theme"] = "dark" if cur_t == "light" else "light"
        A.save_config(c)
        root.after(10, lambda: _rebuild())

    def _build_footer():
        nav = ui_ref["nav"]
        tk.Frame(nav, bg=C["border"], height=1).pack(
            side="bottom", fill="x", padx=12, pady=(8, 0))
        foot = tk.Frame(nav, bg=C["sidebar"])
        foot.pack(side="bottom", fill="x", pady=8)
        st_lbl = tk.Label(foot, text="就绪", bg=C["sidebar"], fg=C["ok"],
                          font=_f(9), anchor="w",
                          wraplength=NAV_W - 26, justify="left")
        st_lbl.pack(fill="x", padx=14)
        widgets["footer.status"] = st_lbl

        # ⑩ 环境信息：引擎 / 并发 / 主题，一眼看到当前配置
        try:
            _c = A.load_config()
            _eng = A.ENGINE_LABEL.get(str(_c.get("engine") or "bing"),
                                      str(_c.get("engine") or "bing"))
            _th = _c.get("threads") or 20
            _thm = "夜间" if ui_state.get("theme") == "dark" else "白天"
            env_txt = "引擎 %s · 并发 %s · %s" % (_eng[:14], _th, _thm)
        except Exception:
            env_txt = ""
        if env_txt:
            env_lbl = tk.Label(foot, text=env_txt, bg=C["sidebar"],
                               fg=C["muted"], font=_f(8), anchor="w",
                               wraplength=NAV_W - 26, justify="left")
            env_lbl.pack(fill="x", padx=14, pady=(2, 0))

        tb = tk.Label(foot, text="", bg=C["sidebar"], fg=C["accent"],
                      font=_f(9), anchor="w", padx=14, pady=7,
                      cursor="hand2")
        tb.pack(fill="x", pady=(8, 0))
        tb.bind("<Enter>", lambda e: tb.configure(bg=C["sidebar_on"]), add="+")
        tb.bind("<Leave>", lambda e: tb.configure(bg=C["sidebar"]), add="+")
        tb.bind("<Button-1>", lambda e: toggle_theme(), add="+")
        widgets["footer.theme_btn"] = tb
        _refresh_theme_button(foot)

    def _refresh_theme_button(_foot=None):
        """按当前主题更新按钮文字。"""
        tb = widgets.get("footer.theme_btn")
        if tb is None:
            return
        try:
            is_dark = (ui_state.get("theme") == "dark")
            tb.configure(text=("☀  白天模式" if is_dark else "🌙  夜间模式"))
        except Exception:
            pass

    _build_footer()

    # 暴露重建函数：设置页要用，自动化测试也要用
    A._UI_STATE["rebuild"] = _rebuild
    A._UI_STATE["ui_state"] = ui_state

    # ================= 动作 =================
    def pick():
        if state["running"]:
            messagebox.showinfo("请稍候", "正在翻译中。")
            return
        last = state.get("last_dir") or ""
        paths = filedialog.askopenfilenames(
            title="选择要翻译的 PDF（可多选）",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            initialdir=A._existing_dir(last, Path.home() / "Documents", APP_DIR))
        if not paths:
            return
        files = [str(x) for x in paths]
        state["last_dir"] = str(Path(files[0]).parent)
        # 单个文件：先弹确认窗口（输出版本/类型/页数/位置）
        if len(files) == 1:
            if confirm_output(files[0]):
                start(files[0])
        else:
            if not messagebox.askyesno("批量翻译",
                                       "共 %d 个文件，依次翻译？\n\n"
                                       "（批量使用当前设置，不再逐个确认）" % len(files)):
                return
            if confirm_output(files[0]):
                state["batch"] = files[1:]
                start(files[0])

    def _ask_open_result(pdf_path: str) -> None:
        """翻译完成后询问是否打开结果（含打开方式选择）。"""
        try:
            p = Path(str(pdf_path))
        except Exception:
            return
        if not p.exists():
            toast("译文已生成，但找不到文件：%s" % p.name, "warn")
            return

        dlg = tk.Toplevel(root)
        dlg.title("翻译完成")
        dlg.geometry("460x260")
        dlg.transient(root)
        try:
            dlg.configure(bg=C["bg"])
        except Exception:
            pass

        # ★ 先把按钮栏放进去（pack side=bottom 必须先于 expand 的区域），
        # 否则 body 会把整个空间吃掉，按钮被挤到窗口外面。
        # 之前先 pack body(fill=both, expand=True)，→ 「开始翻译」看不见。
        _bar_holder = tk.Frame(dlg, bg=C["bg"])
        _bar_holder.pack(side="bottom", fill="x")

        body = tk.Frame(dlg, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(body, text="✅ 翻译完成", bg=C["bg"], fg=C["ok"],
                 font=_f(12, "bold"), anchor="w").pack(anchor="w")
        tk.Label(body, text=p.name, bg=C["bg"], fg=C["accent"],
                 font=_f(10), anchor="w", wraplength=410,
                 justify="left").pack(anchor="w", pady=(4, 10))

        # 打开方式
        tk.Label(body, text="用什么打开", bg=C["bg"], fg=C["text_sub"],
                 font=_f(9), anchor="w").pack(anchor="w")
        opts = ["系统默认", "Microsoft Edge", "Microsoft Office",
                "WPS", "不打开"]
        mode_map = {"系统默认": "auto", "Microsoft Edge": "edge",
                    "Microsoft Office": "office", "WPS": "wps",
                    "不打开": "none"}
        mv = tk.StringVar(value="系统默认")
        cb = ttk.Combobox(body, textvariable=mv, values=opts,
                          state="readonly", width=22)
        cb.pack(anchor="w", pady=(2, 10))

        def do_open():
            m = mode_map.get(mv.get(), "auto")
            dlg.destroy()
            if m == "none":
                return
            try:
                c = A.load_config()
                c["open_with"] = m
                A.save_config(c)
                cur_mode = m
                if m == "office" and str(p).lower().endswith(".pdf"):
                    toast("Office 正在转换 PDF，约需 15~30 秒…",
                          kind="warn", seconds=4)
                A.open_file_with(p, c)
            except Exception as exc:
                toast("打开失败：%s" % exc, "err")

        def do_folder():
            try:
                os.startfile(str(p.parent))
            except Exception as exc:
                toast("打不开文件夹：%s" % exc, "err")
            dlg.destroy()

        bar = _bar_holder
        bar.configure(bg=C["bg"])
        _inner = tk.Frame(bar, bg=C["bg"])
        _inner.pack(fill="x", padx=18, pady=(8, 12))
        bar = _inner
        flat_btn(bar, "打开", do_open, "primary").pack(side="right")
        flat_btn(bar, "打开文件夹", do_folder).pack(side="right", padx=8)
        flat_btn(bar, "关闭", dlg.destroy).pack(side="right")

        dlg.bind("<Return>", lambda e: do_open())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        try:
            dlg.grab_set()
            dlg.focus_force()
        except Exception:
            pass

    def _update_queue():
        """刷新队列面板。

        现在队列是「本次批量翻译的完整记录」：
          · 已完成的文件继续留在列表里（带 ✓ 和耗时）
          · 点选任意一项，可以把它的步骤耗时 / 术语命中调出来回看
          · 失败的行标红，方便定位
        所有结果都存在 state["results"] 里，不用重新翻译。
        """
        w = widgets.get("translate.queue")
        if w is None:
            return
        try:
            cur_p = state.get("cur_file") or ""
            todo = state.get("batch") or []
            results = state.get("results") or {}

            # 记住当前选中，刷新后恢复（否则每次刷新都跳走）
            keep = None
            try:
                sel = w.selection()
                if sel:
                    keep = w.item(sel[0], "values")[1]
            except Exception:
                pass

            for i in w.get_children():
                w.delete(i)

            def _info_of(path_str):
                """从结果里拼一行摘要：耗时 / 页数。"""
                rec = results.get(path_str) or {}
                if not rec:
                    return ""
                tm = rec.get("timing") or {}
                bits = []
                tot = float(tm.get("total") or 0)
                if tot > 0:
                    bits.append("%.0f 秒" % tot)
                pg = int(tm.get("total_pages") or 0)
                if pg > 0:
                    bits.append("%d 页" % pg)
                th = len(tm.get("term_hits") or {})
                if th > 0:
                    bits.append("术语 %d" % th)
                if rec.get("error"):
                    bits.append(str(rec["error"])[:24])
                return " · ".join(bits)

            if cur_p:
                nm = Path(cur_p).name
                if state.get("running"):
                    w.insert("", "end", values=("▶ 进行中", nm, "翻译中…"),
                             tags=("run",))
                else:
                    rec = results.get(cur_p) or {}
                    st = "ok" if rec.get("ok") else ("fail" if rec else "wait")
                    w.insert("", "end",
                             values=(_QS[st][0], nm, _info_of(cur_p)),
                             tags=(st,))

            for i, f in enumerate(todo, 1):
                w.insert("", "end",
                         values=("○ 等待", Path(f).name, "排队中"),
                         tags=("wait",))

            # 本轮已完成的（不在 batch 里了，但 results 有记录）
            done_paths = []
            for pth, rec in results.items():
                if pth == cur_p:
                    continue
                if pth in todo:
                    continue
                done_paths.append(pth)
            for pth in done_paths:
                rec = results[pth]
                st = "ok" if rec.get("ok") else "fail"
                w.insert("", "end",
                         values=(_QS[st][0], Path(pth).name, _info_of(pth)),
                         tags=(st,))

            if not w.get_children():
                w.insert("", "end",
                         values=("", "（还没有任务，拖 PDF 进来试试）", ""),
                         tags=(),)

            # 恢复选中
            if keep:
                for i in w.get_children():
                    try:
                        if w.item(i, "values")[1] == keep:
                            w.selection_set(i)
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ④ 队列落盘：关掉软件也不丢任务
        try:
            A.save_queue([{"path": p, "name": Path(p).name}
                          for p in (state.get("batch") or [])])
            if not state.get("batch") and not state.get("running"):
                A.clear_queue()
        except Exception:
            pass

    # 暴露给自动化测试 / 调试用（必须放在 _update_queue 定义之后，
    # 否则拿到的是还没定义的那个名字，调用会抛 NameError）
    try:
        A._UI_STATE["update_queue"] = _update_queue
        A._UI_STATE["state"] = state
        A._UI_STATE["fill_panels"] = _fill_translate_panels
    except Exception:
        pass

    def do_cancel():
        """终止正在进行的翻译。"""
        if not A.is_translating():
            toast("当前没有在翻译", "warn")
            return
        if not messagebox.askyesno("终止翻译",
                                   "确定要终止当前的翻译吗？\n\n"
                                   "已翻完的页会记入断点，下次可以接着翻。"):
            return
        if A.cancel_translation():
            # ⑤ 中断时把当前进度落盘（completed=False），下次能续译
            try:
                _cur = state.get("cur_file") or ""
                _tot = int(state.get("_total_pages") or 0)
                _done = int(state.get("_last_done") or 0)
                if _cur and _tot > 0 and 0 < _done < _tot:
                    A.save_progress(_cur, list(range(1, _done + 1)), _tot,
                                    completed=False)
            except Exception:
                pass
            toast("已终止（下次可续译）", "ok")
            set_status("已终止", C["warn"])
            state["running"] = False
            state["batch"] = []
        else:
            toast("终止失败，请手动关闭", "err")

    def confirm_output(pdf_path: str) -> bool:
        """翻译前的确认窗口：输出版本 / 翻译范围 / 保存位置 / 附加选项。"""
        cfg = A.load_config()
        dlg = tk.Toplevel(root)
        dlg.title("确认翻译设置")
        # 内容（缩略图 + 续译提示 + 5 组设置）实测需要 ~540px，
        # 原来 480 会把最下面的选项裁掉，看起来像"设置不全"。
        dlg.geometry("600x680")
        dlg.minsize(560, 600)
        try:
            dlg.configure(bg=C["bg"])
        except Exception:
            pass
        dlg.transient(root)

        # ★ 按钮栏必须先 pack（side=bottom），否则下面的 expand 区域
        # 会把整个空间吃掉，按钮被挤到窗口外的下面看不见。
        _cbar = tk.Frame(dlg, bg=C["bg"])
        _cbar.pack(side="bottom", fill="x")

        body = tk.Frame(dlg, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(body, text="要翻译的文件", bg=C["bg"], fg=C["muted"],
                 font=_f(9), anchor="w").pack(anchor="w")
        tk.Label(body, text=Path(pdf_path).name, bg=C["bg"], fg=C["accent"],
                 font=_f(11, "bold"), anchor="w").pack(anchor="w", pady=(2, 0))

        # ⑥ 预估工作量 + 首页缩略图：确认「确实是这份文件」
        try:
            _chk = A.check_source_pdf(pdf_path)
            _pg = int(_chk.get("pages") or 0) if _chk.get("ok") else 0
            if _pg:
                info_row = tk.Frame(body, bg=C["bg"])
                info_row.pack(fill="x", pady=(0, 6))
                tk.Label(info_row, text=A.estimate_workload(_pg, cfg),
                         bg=C["bg"], fg=C["muted"], font=_f(9),
                         anchor="w").pack(side="left")
                # 缩略图：很多文件名很像，看一眼首页才不会拖错
                try:
                    import fitz as _fitz
                    with _fitz.open(str(pdf_path)) as _doc:
                        _pix = _doc[0].get_pixmap(matrix=_fitz.Matrix(0.18, 0.18))
                        _png = _pix.tobytes("png")
                    if _png and len(_png) < 3_000_000:
                        _thumb = tk.PhotoImage(data=_png)
                        _tf = tk.Frame(info_row, bg=C["bg"])
                        _tf.pack(side="right")
                        tk.Label(_tf, image=_thumb, bg=C["border"],
                                 borderwidth=1, relief="solid").pack()
                        # 必须保留引用，否则 Tk 会把图回收掉
                        dlg._thumb_ref = _thumb
                except Exception:
                    pass
        except Exception:
            pass

        # ⑤ 断点续译：上次没翻完就提示
        _resume = {"rec": None}
        try:
            _resume["rec"] = A.get_progress(pdf_path)
        except Exception:
            pass
        if _resume["rec"]:
            rec = _resume["rec"]
            done_n = len(rec.get("done_pages") or [])
            tot_n = int(rec.get("total") or 0)
            rf = tk.Frame(body, bg=C["accent_sf"])
            rf.pack(fill="x", pady=(0, 10))
            tk.Label(rf, text="⏸  发现上次未完成",
                     bg=C["accent_sf"], fg=C["accent"],
                     font=_f(9, "bold"), anchor="w").pack(
                anchor="w", padx=10, pady=(6, 0))
            tk.Label(rf,
                     text="已翻完 %d 页 / 共 %d 页，可以只补剩下没翻的。"
                          % (done_n, tot_n or done_n),
                     bg=C["accent_sf"], fg=C["text_sub"],
                     font=_f(8), anchor="w").pack(anchor="w", padx=10, pady=(0, 6))
        else:
            rf = None

        def row(label, hint=""):
            f = tk.Frame(body, bg=C["bg"])
            f.pack(fill="x", pady=4)
            tk.Label(f, text=label, bg=C["bg"], fg=C["text"],
                     font=_f(10), width=10, anchor="w").pack(side="left")
            box = tk.Frame(f, bg=C["bg"])
            box.pack(side="left", fill="x", expand=True)
            if hint:
                tk.Label(box, text=hint, bg=C["bg"], fg=C["muted"],
                         font=_f(8), anchor="w").pack(anchor="w")
            return box

        # 输出版本
        mode_labels = ["中文版（仅译文）", "双语对照版", "两者都要"]
        mode_map = {"中文版（仅译文）": "mono", "双语对照版": "dual", "两者都要": "both"}
        rev_mode = {v: k for k, v in mode_map.items()}
        mode_v = tk.StringVar(value=rev_mode.get(
            str(cfg.get("output_mode") or "mono"), "中文版（仅译文）"))
        box = row("输出版本")
        ttk.Combobox(box, textvariable=mode_v, values=mode_labels,
                     state="readonly", width=22).pack(anchor="w")

        # 翻译范围
        pages_v = tk.StringVar(value=str(cfg.get("pages") or ""))
        box = row("翻译范围", "留空 = 全部；可填 1-10,15,20-")
        ttk.Entry(box, textvariable=pages_v, width=24).pack(anchor="w")

        # 保存位置
        dir_v = tk.StringVar(value=str(A.resolve_output_dir(cfg)))
        box = row("保存位置")
        e = ttk.Entry(box, textvariable=dir_v, width=34)
        e.pack(side="left", fill="x", expand=True)

        # 附加选项（经典版有、侧边栏之前漏掉的）
        opt = tk.Frame(body, bg=C["bg"])
        opt.pack(fill="x", pady=(6, 0))
        chk = dict(bg=C["bg"], fg=C["text"], selectcolor=C["card"],
                   activebackground=C["bg"], font=_f(9), anchor="w")
        skip_toc_v = tk.BooleanVar(value=bool(cfg.get("skip_toc", False)))
        tk.Checkbutton(opt, text="跳过目录页（避免条目被合并成一段）",
                       variable=skip_toc_v, **chk).pack(anchor="w")
        qc_v = tk.BooleanVar(value=bool(cfg.get("quality_check", True)))
        tk.Checkbutton(opt, text="翻译后做质量检查（漏翻/英文残留/数字丢失）",
                       variable=qc_v, **chk).pack(anchor="w")


        def browse():
            d = filedialog.askdirectory(title="选择保存位置", initialdir=dir_v.get())
            if d:
                dir_v.set(d)
        flat_btn(box, "浏览", browse).pack(side="left", padx=(6, 0))

        result = {"go": False}

        def go():
            c = A.load_config()
            c["output_mode"] = mode_map.get(mode_v.get(), "mono")
            pages_spec = pages_v.get().strip()

            # ⑤ 断点续译：上次没翻完，问一下要不要只补剩下的
            rec = _resume.get("rec")
            if rec and not pages_spec:
                done = rec.get("done_pages") or []
                total = int(rec.get("total") or 0)
                rest = A.remaining_pages(total, done) if total else ""
                if rest and messagebox.askyesno(
                        "断点续译",
                        "上次已翻完 %d / %d 页。\n\n"
                        "只补剩下的第 %s 页吗？\n\n"
                        "选「否」将从第 1 页重新开始（更慢，但输出更完整）。"
                        % (len(done), total, rest),
                        parent=dlg):
                    pages_spec = rest
                    toast("将从断点继续（剩余 %s）" % rest, "ok")
                else:
                    # 用户选择从头翻 → 清掉旧断点，避免下次又提示
                    try:
                        A.clear_progress(pdf_path)
                    except Exception:
                        pass

            c["pages"] = pages_spec
            c["output_dir"] = dir_v.get().strip()
            c["skip_toc"] = bool(skip_toc_v.get())
            c["quality_check"] = bool(qc_v.get())
            A.save_config(c)
            result["go"] = True
            dlg.destroy()

        def cancel():
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", cancel)
        _cbar_inner = tk.Frame(_cbar, bg=C["bg"])
        _cbar_inner.pack(fill="x", padx=18, pady=(8, 12))
        bar = _cbar_inner
        flat_btn(bar, "开始翻译", go, "primary").pack(side="right")
        flat_btn(bar, "取消", cancel).pack(side="right", padx=8)
        tk.Label(bar, text="确认后开始翻译", bg=C["bg"], fg=C["muted"],
                 font=_f(8)).pack(side="left")

        try:
            dlg.grab_set()
        except Exception:
            pass
        root.wait_window(dlg)
        return result["go"]

    def preview_latest():
        """⑪ 在软件内预览最近一次翻译的结果。"""
        try:
            import pdf_viewer
        except Exception as exc:
            toast("预览模块缺失：%s" % exc, "err")
            return
        # 找最近的历史记录里的产物
        target = ""
        for it in A.load_history():
            for key in ("files", "word_files"):
                for f in (it.get(key) or []):
                    if str(f).lower().endswith(".pdf") and Path(str(f)).exists():
                        target = str(f)
                        break
                if target:
                    break
            if target:
                break
        if not target:
            toast("还没有可预览的译文，先翻译一个文件吧", "warn")
            return
        _preview_window(pdf_viewer, target)

    def _preview_window(pv, path):
        """轻量预览窗：只读翻页，不改动文件。"""
        try:
            doc = pv.PdfDoc(path)
        except Exception as exc:
            toast("打不开：%s" % exc, "err")
            return
        win = tk.Toplevel(root)
        win.title("预览 — " + Path(path).name)
        win.geometry("900x720")
        try:
            win.configure(bg=C["bg"])
        except Exception:
            pass

        top = tk.Frame(win, bg=C["bg"])
        top.pack(fill="x", padx=10, pady=8)
        stt = {"p": 0, "z": 1.4}

        cv = tk.Canvas(win, bg=C.get("reader_bg", "#8a8f99"),
                       highlightthickness=0)
        vs = ttk.Scrollbar(win, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        cv.pack(fill="both", expand=True, padx=10)
        ref = {"img": None}
        lbl = tk.Label(top, text="", bg=C["bg"], fg=C["text_sub"], font=_f(9))

        def redraw():
            try:
                png, w, h, _ = doc.render(stt["p"], stt["z"])
                img = tk.PhotoImage(data=png)
            except Exception as exc:
                toast("渲染失败：%s" % exc, "err")
                return
            ref["img"] = img
            cv.delete("all")
            cv.create_image(0, 0, anchor="nw", image=img)
            cv.configure(scrollregion=(0, 0, w, h))
            lbl.configure(text="%d / %d" % (stt["p"] + 1, doc.total))

        def go(d):
            stt["p"] = max(0, min(doc.total - 1, stt["p"] + d))
            redraw()

        flat_btn(top, "◀", lambda: go(-1)).pack(side="left")
        flat_btn(top, "▶", lambda: go(1)).pack(side="left", padx=4)
        lbl.pack(side="left", padx=10)
        flat_btn(top, "关闭", win.destroy).pack(side="right")
        redraw()

    def recent_folder():
        d = ""
        for it in A.load_history():
            cand = str(it.get("out_dir") or "")
            if cand and Path(cand).is_dir():
                d = cand; break
        if not d:
            d = str(A.resolve_output_dir(A.load_config()))
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            os.startfile(d)
            set_status("已打开目录")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def _finish_batch():
        """整批翻译结束：列出每个文件的结果，可以勾选打开。"""
        ok = int(state.get("batch_ok") or 0)
        fails = state.get("batch_fail") or []
        results = state.get("results") or {}
        state["batch_started"] = False

        # 系统通知：切到别的窗口时也能知道翻完了
        try:
            if fails:
                A.notify_done("翻译完成（部分失败）",
                              "成功 %d 个，失败 %d 个" % (ok, len(fails)),
                              ok=False)
            elif ok > 0:
                A.notify_done("翻译完成",
                              "成功 %d 个文件" % ok, ok=True)
        except Exception:
            pass

        # 单个文件：不弹结果表，不打扰
        if ok <= 1 and not fails:
            return
        if not results:
            return

        # ---- 结果窗口：勾选后打开 ----
        try:
            dlg = tk.Toplevel(root)
            dlg.title("批量翻译完成")
            dlg.geometry("640x460")
            dlg.minsize(560, 380)
            dlg.transient(root)
            try:
                dlg.configure(bg=C["bg"])
            except Exception:
                pass

            # 按钮栏先 pack，保证可见
            bar = tk.Frame(dlg, bg=C["bg_alt"])
            bar.pack(side="bottom", fill="x")

            head = tk.Frame(dlg, bg=C["bg"])
            head.pack(fill="x", padx=16, pady=(14, 6))
            title = "成功 %d 个" % ok
            if fails:
                title += "，失败 %d 个" % len(fails)
            tk.Label(head, text=title, bg=C["bg"], fg=C["text"],
                     font=_f(12, "bold"), anchor="w").pack(anchor="w")
            tk.Label(head, text="勾选想打开的文件（双击某一项可单独打开）",
                     bg=C["bg"], fg=C["muted"], font=_f(9),
                     anchor="w").pack(anchor="w", pady=(2, 0))

            # 列表：复选框用 Treeview 模拟（Treeview 不支持原生 checkbox）
            box = tk.Frame(dlg, bg=C["bg"])
            box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
            tw = ttk.Treeview(box, columns=("sel", "name", "state", "info"),
                              show="headings", height=12)
            for col, txt, wd, anc in (("sel", "选", 40, "center"),
                                      ("name", "文件", 250, "w"),
                                      ("state", "结果", 70, "center"),
                                      ("info", "摘要", 200, "w")):
                tw.heading(col, text=txt)
                tw.column(col, width=wd, anchor=anc,
                          stretch=(col in ("name", "info")))
            sb = ttk.Scrollbar(box, command=tw.yview)
            tw.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            tw.pack(side="left", fill="both", expand=True)

            try:
                tw.tag_configure("ok", foreground=C["ok"])
                tw.tag_configure("fail", foreground=C["danger"])
            except Exception:
                pass

            picked = {}       # iid -> 路径
            for pth, rec in results.items():
                is_ok = bool(rec.get("ok"))
                tm = rec.get("timing") or {}
                bits = []
                tot = float(tm.get("total") or 0)
                if tot > 0:
                    bits.append("%.0f 秒" % tot)
                th = len(tm.get("term_hits") or {})
                if th:
                    bits.append("术语 %d" % th)
                if not is_ok:
                    bits.append(str(rec.get("error") or "失败")[:20])
                iid = tw.insert("", "end", values=(
                    "☐" if is_ok else "",
                    Path(pth).name,
                    "✓" if is_ok else "✗",
                    " · ".join(bits)),
                    tags=("ok" if is_ok else "fail",))
                if is_ok:
                    picked[iid] = pth

            def _toggle(_e=None):
                sel = tw.selection()
                for iid in sel:
                    if iid not in picked:
                        continue
                    cur = tw.item(iid, "values")[0]
                    tw.item(iid, values=("☐" if cur == "☑" else "☑",
                                         *tw.item(iid, "values")[1:]))

            def _all():
                for iid in picked:
                    tw.item(iid, values=("☑", *tw.item(iid, "values")[1:]))

            def _none():
                for iid in picked:
                    tw.item(iid, values=("☐", *tw.item(iid, "values")[1:]))

            def _selected_paths():
                out = []
                for iid, pth in picked.items():
                    try:
                        if tw.item(iid, "values")[0] == "☑":
                            out.append((iid, pth))
                    except Exception:
                        pass
                return out

            def _open_selected():
                sel = _selected_paths()
                if not sel:
                    toast("先勾选要打开的文件", "warn")
                    return
                opened = 0
                for _iid, pth in sel:
                    rec = results.get(pth) or {}
                    of = rec.get("out_file") or ""
                    try:
                        if of and Path(of).exists():
                            A.open_file_with(Path(of), A.load_config())
                            opened += 1
                    except Exception:
                        pass
                dlg.destroy()
                toast("已打开 %d 个文件" % opened, "ok")

            def _open_folder():
                sel = _selected_paths()
                target = None
                if sel:
                    rec = results.get(sel[0][1]) or {}
                    of = rec.get("out_file") or ""
                    if of:
                        target = Path(of).parent
                if target is None:
                    target = A.resolve_output_dir(A.load_config())
                try:
                    A.open_file_with(Path(target), A.load_config(),
                                     mode="folder")
                except Exception as exc:
                    toast("打开失败：%s" % exc, "err")

            def _dbl(_e=None):
                sel = tw.selection()
                if not sel:
                    return
                iid = sel[0]
                if iid in picked:
                    rec = results.get(picked[iid]) or {}
                    of = rec.get("out_file") or ""
                    if of and Path(of).exists():
                        A.open_file_with(Path(of), A.load_config())

            tw.bind("<Button-1>", lambda e: None)
            tw.bind("<Double-1>", _dbl, add="+")
            tw.bind("<space>", lambda e: (_toggle(), "break")[1], add="+")

            flat_btn(bar, "打开选中", _open_selected, "primary").pack(
                side="right", padx=(8, 12), pady=8)
            flat_btn(bar, "打开文件夹", _open_folder).pack(side="right",
                                                         pady=8)
            flat_btn(bar, "全选", _all).pack(side="left", padx=12, pady=8)
            flat_btn(bar, "全不选", _none).pack(side="left", pady=8)
            flat_btn(bar, "关闭", dlg.destroy).pack(side="right",
                                                   padx=8, pady=8)

            # 点一下行就切换勾选，最符合直觉
            def _click_toggle(e):
                iid = tw.identify_row(e.y)
                col = tw.identify_column(e.x)
                if iid and iid in picked and col == "#1":
                    cur = tw.item(iid, "values")[0]
                    tw.item(iid, values=("☐" if cur == "☑" else "☑",
                                         *tw.item(iid, "values")[1:]))
                    return "break"
            tw.bind("<Button-1>", _click_toggle, add="+")

        except Exception:
            pass

    def start(path: str):
        cfg = A.load_config()
        pdf = Path(path)
        out_dir = A.resolve_output_dir(cfg)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存位置不可用", str(exc)); return

        state["running"] = True
        state["cur_file"] = path
        state["t_start"] = time.time()
        # ⑦ 批量统计：只在「本轮第一个文件」时初始化
        if not state.get("batch_started"):
            state["batch_ok"] = 0
            state["batch_fail"] = []
            state["results"] = {}      # 本轮每个文件的结果，供队列点选回看
            state["batch_started"] = True

        # 新一轮翻译开始 → 解除「正在回看历史」的锁定，
        # 否则实时进度面板不会更新
        try:
            _viewing["name"] = ""
            _viewing["path"] = ""
        except Exception:
            pass

        # ⑤ 记下总页数（供断点续译换算已完成页）
        try:
            state["_total_pages"] = A.count_pdf_pages(pdf)
            state["_prog_save_at"] = 0.0
        except Exception:
            state["_total_pages"] = 0
        # 新一轮翻译：清空上一轮的阶段记录
        try:
            stage_done.clear()
            stage_live["name"] = None
            stage_live["start"] = 0.0
        except Exception:
            pass
        _update_queue()
        ui_q.put(("prog", 0.0, "准备中"))

        def on_prog(v, stage):
            # core.py 的 on_progress 只给 (值, 阶段)，进度事件里的
            # stage_current / stage_total（页数）从 A._PROG_EXTRA 读
            ui_q.put(("prog", v, stage, getattr(A, "_PROG_EXTRA", None)))

        def on_stage(name):
            ui_q.put(("stage", name))

        def done(ok, out_file, err, timing=None):
            ui_q.put(("done", ok, out_file, err, timing or {}))

        def work():
            try:
                A.run_translate(pdf, cfg, out_dir,
                                lambda s: ui_q.put(("log", str(s))),
                                on_prog, done, on_stage)
            except Exception as exc:
                ui_q.put(("log", "❌ " + str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ================= 事件泵 =================
    _my_gen = A._BUILD_GEN["n"]
    stage_live = {"name": None, "start": 0.0}

    def pump():
        if _my_gen != A._BUILD_GEN["n"]:
            return
        try:
            while True:
                item = ui_q.get_nowait()
                kind = item[0]
                if kind == "log":
                    w = widgets.get("translate.log")
                    if w is not None:
                        w.insert("end", str(item[1]) + "\n"); w.see("end")
                elif kind == "status":
                    w = widgets.get("footer.status")
                    if w is not None:
                        w.configure(text=str(item[1]), fg=item[2] if len(item) > 2 else C["ok"])
                elif kind == "prog":
                    v = max(0.0, min(100.0, float(item[1])))
                    if widgets.get("translate.prog") is not None:
                        widgets["translate.prog"].configure(value=v)

                    # ⑤ 断点续译：翻译过程中就落盘进度。
                    # 之前只在「完全成功」时写一次，所以中断后进度全丢。
                    # 这里按页数换算已完成页，每 5 秒最多写一次，避免频繁 IO。
                    try:
                        _now = time.time()
                        if _now - float(state.get("_prog_save_at") or 0) >= 5.0:
                            state["_prog_save_at"] = _now
                            _cur = state.get("cur_file") or ""
                            _extra = item[3] if len(item) > 3 else None
                            _tot = 0
                            _done_n = 0
                            if _extra:
                                _tot = int(_extra.get("stage_total") or 0)
                                _done_n = int(_extra.get("stage_current") or 0)
                            if not _tot:
                                _tot = int(state.get("_total_pages") or 0)
                            if _cur and _tot > 0:
                                # 用百分比估算已完成页数（无精确页信息时）
                                _dn = _done_n or int(round(_tot * v / 100.0))
                                state["_last_done"] = _dn
                                if _dn > 0 and _dn < _tot:
                                    A.save_progress(_cur, list(range(1, _dn + 1)),
                                                    _tot, completed=False)
                    except Exception:
                        pass
                    # 已用时间 + 剩余时间（按阶段权重估算，比线性准）
                    try:
                        t_start = float(state.get("t_start") or 0)
                        if t_start > 0 and v > 0.5:
                            used = time.time() - t_start
                            eta = A.estimate_eta(
                                used, v,
                                str(stage_live.get("name") or ""),
                                stage_done)
                            el = widgets.get("translate.eta")
                            if el is not None:
                                el.configure(
                                    text="已用 %s · 剩余约 %s"
                                         % (H.fmt_sec(used),
                                            A.fmt_duration(eta)))
                        elif t_start > 0:
                            el = widgets.get("translate.eta")
                            if el is not None:
                                el.configure(text="已用 %s · 计算剩余时间…"
                                                  % H.fmt_sec(time.time() - t_start))
                    except Exception:
                        pass
                    if widgets.get("translate.pct") is not None:
                        widgets["translate.pct"].configure(text="%.0f%%" % v)
                    # ⑪ 同时显示「第 N/M 页」——长文档比百分比直观
                    extra = item[3] if len(item) > 3 else None
                    if extra and widgets.get("translate.pages") is not None:
                        cur_p = extra.get("stage_current")
                        tot_p = extra.get("stage_total")
                        if cur_p and tot_p:
                            widgets["translate.pages"].configure(
                                text="第 %s/%s 页" % (cur_p, tot_p))
                    if len(item) > 2 and item[2] and widgets.get("translate.stage") is not None:
                        widgets["translate.stage"].configure(text=A.stage_cn(str(item[2]))[:18])
                elif kind == "stage":
                    # 只记录阶段起点，【不自动跳页】——
                    # 之前一有 stage 事件就切到「步骤耗时」页，
                    # 用户就看不到翻译页的进度条/日志了（这是主要投诉）。
                    prev_name = stage_live.get("name")
                    prev_start = float(stage_live.get("start") or 0)
                    if prev_name and prev_start > 0:
                        # 上一个阶段结束 → 记入已完成列表
                        stage_done.append({
                            "name": prev_name,
                            "seconds": time.time() - prev_start,
                        })
                    stage_live["name"] = str(item[1])
                    stage_live["start"] = time.time()
                    _live_tick()
                elif kind == "done":
                    _, ok, out_file, err = item[:4]
                    timing = item[4] if len(item) > 4 else {}
                    state["running"] = False
                    if ok and out_file:
                        set_status("完成")
                        # ⑤ 断点续译：整份翻完了 → 标记 completed，下次不再提示
                        try:
                            _p = state.get("cur_file") or ""
                            _pages = timing.get("pages") or []
                            _total = int(timing.get("total_pages") or 0)
                            if _p:
                                A.save_progress(_p, _pages or [], _total,
                                                out_file, completed=True)
                        except Exception:
                            pass
                        if timing:
                            A._UI_STATE["stages"] = timing.get("stages") or []
                            A._UI_STATE["total"] = timing.get("total") or 0
                            # 翻译完成 → 解除历史锁定，把最新结果显示出来
                            try:
                                _viewing["name"] = ""
                                _viewing["path"] = ""
                            except Exception:
                                pass
                            _fill_translate_panels(timing)
                        # 翻译结束：清掉实时阶段状态，避免下次翻译残留
                        try:
                            stage_done.clear()
                            stage_live["name"] = None
                            stage_live["start"] = 0.0
                        except Exception:
                            pass
                        # ⑦ 记一次成功
                        try:
                            state["batch_ok"] = int(state.get("batch_ok") or 0) + 1
                        except Exception:
                            pass

                        # 保存本文件结果，供之后在队列里点选回看
                        try:
                            _rp = state.get("cur_file") or ""
                            if _rp:
                                state.setdefault("results", {})[_rp] = {
                                    "ok": True,
                                    "out_file": str(out_file or ""),
                                    "timing": dict(timing or {}),
                                    "error": "",
                                }
                                _update_queue()
                        except Exception:
                            pass

                        # 单个文件（不在批量里）也发个通知
                        try:
                            if not (state.get("batch") or []):
                                _nm = Path(state.get("cur_file") or "").name
                                A.notify_done("翻译完成", _nm or "已完成", ok=True)
                        except Exception:
                            pass

                        # 批量：继续下一个
                        nxt = (state.get("batch") or [])
                        if nxt:
                            state["batch"] = nxt[1:]
                            _update_queue()          # ④ 落盘新队列
                            root.after(400, lambda p=nxt[0]: start(p))
                        else:
                            # ⑦ 整批结束 → 汇总
                            try:
                                _finish_batch()
                            except Exception:
                                pass
                            # 批量结束 / 单个完成 → 按设置处理
                            af = str(A.load_config().get("after_translate")
                                     or "ask").lower()
                            if af == "open":
                                try:
                                    A.open_file_with(Path(out_file), A.load_config())
                                except Exception:
                                    pass
                            elif af == "ask":
                                # 之前这里【缺失了 ask 分支】—— 选了「每次询问我」
                                # 却什么都不弹，用户以为功能没了。
                                root.after(200, lambda p=out_file:
                                           _ask_open_result(p))
                    else:
                        set_status("失败", C["danger"])
                        # ⑦ 记一次失败
                        try:
                            _fp = state.get("cur_file") or ""
                            _fn = Path(_fp).name if _fp else "?"
                            state.setdefault("batch_fail", []).append(
                                (_fn, str(err or "未知错误")))
                            if _fp:
                                state.setdefault("results", {})[_fp] = {
                                    "ok": False,
                                    "out_file": "",
                                    "timing": {},
                                    "error": str(err or "未知错误"),
                                }
                                _update_queue()
                        except Exception:
                            pass
                        w = widgets.get("translate.log")
                        if w is not None and err:
                            w.insert("end", "❌ " + str(err) + "\n"); w.see("end")
                        # ③ 把技术报错翻译成用户能照着做的建议
                        if err:
                            try:
                                info = A.explain_error(err)
                                w2 = widgets.get("translate.log")
                                if w2 is not None:
                                    w2.insert("end",
                                              "\n💡 " + info["title"] + "\n")
                                    for _ln in info["advice"].splitlines():
                                        w2.insert("end", "   " + _ln + "\n")
                                    w2.see("end")
                                root.after(300, lambda i=info:
                                           _show_error_dialog(i))
                            except Exception:
                                pass
        except queue.Empty:
            pass
        if _my_gen == A._BUILD_GEN["n"]:
            root.after(120, pump)

    def _show_error_dialog(info: dict) -> None:
        """③ 翻译失败时弹出「说人话」的提示窗口。

        不是把技术报错甩给用户，而是告诉他：这是什么问题、该怎么办。
        """
        try:
            dlg = tk.Toplevel(root)
            dlg.title("翻译失败")
            dlg.geometry("540x420")
            dlg.minsize(480, 360)
            dlg.transient(root)
            try:
                dlg.configure(bg=C["bg"])
            except Exception:
                pass

            # 按钮栏先 pack，保证永远可见
            bar = tk.Frame(dlg, bg=C["bg_alt"])
            bar.pack(side="bottom", fill="x")

            body = tk.Frame(dlg, bg=C["bg"])
            body.pack(fill="both", expand=True, padx=18, pady=(16, 8))

            tk.Label(body, text="⚠  " + str(info.get("title") or "翻译失败"),
                     bg=C["bg"], fg=C["danger"],
                     font=_f(12, "bold"), anchor="w").pack(anchor="w")

            tk.Label(body, text=info.get("advice") or "",
                     bg=C["bg"], fg=C["text_sub"], justify="left",
                     anchor="w", wraplength=490,
                     font=_f(9)).pack(anchor="w", pady=(10, 0))

            raw = str(info.get("raw") or "").strip()
            if raw:
                box = tk.Frame(body, bg=C["log_bg"])
                box.pack(fill="both", expand=True, pady=(14, 0))
                txt = tk.Text(box, height=5, wrap="word", bg=C["log_bg"],
                              fg=C["log_fg"], relief="flat", padx=8, pady=6,
                              font=("Consolas", 8))
                sb = ttk.Scrollbar(box, command=txt.yview)
                txt.configure(yscrollcommand=sb.set)
                sb.pack(side="right", fill="y")
                txt.pack(side="left", fill="both", expand=True)
                txt.insert("1.0", raw)
                txt.configure(state="disabled")

            def _copy():
                try:
                    root.clipboard_clear()
                    root.clipboard_append(
                        (info.get("title") or "") + "\n\n" +
                        (info.get("advice") or "") + "\n\n" + raw)
                    toast("已复制错误信息", "ok")
                except Exception:
                    pass

            def _diag():
                dlg.destroy()
                try:
                    A._UI_STATE.get("make_diagnostic", lambda: None)()
                except Exception:
                    pass

            flat_btn(bar, "生成诊断报告", _diag).pack(side="right",
                                                  padx=(8, 12), pady=8)
            flat_btn(bar, "复制错误信息", _copy).pack(side="right", pady=8)
            flat_btn(bar, "知道了", dlg.destroy, "primary").pack(
                side="right", padx=(8, 0), pady=8)
        except Exception:
            pass

    # 实时阶段记录：已完成 + 当前进行中
    stage_done: list = []

    def _live_tick():
        """翻译进行中【实时】刷新翻译主界面的「步骤耗时」面板。

        之前只有当前页是「步骤耗时」时才刷新 —— 而那个页已经删了。
        现在：只要在翻译，每 400ms 就把 stage_done（已完成阶段）
        + 当前进行中的阶段一起刷到翻译页上，用户随时能看到进展。

        ★ 例外：如果用户正在队列里点选某个【已完成】的文件回看，
        就【暂停】实时刷新 —— 否则每 400ms 就把人家正在看的历史数据
        覆盖成「当前正在翻译的那个」，看起来像点不动。
        """
        if _my_gen != A._BUILD_GEN["n"]:
            return

        # 用户正在回看历史 → 让位，不刷
        if _viewing.get("name"):
            root.after(400, _live_tick)
            return

        nm = stage_live.get("name")
        if nm:
            tw = widgets.get("translate.steps")
            if tw is not None:
                try:
                    for i in tw.get_children():
                        tw.delete(i)
                    done_secs = sum(float(r.get("seconds") or 0)
                                    for r in stage_done)
                    el = time.time() - float(stage_live.get("start")
                                             or time.time())
                    grand = done_secs + el
                    for r in stage_done:
                        secs = float(r.get("seconds") or 0)
                        pct = (secs / grand * 100.0) if grand > 0 else 0.0
                        tw.insert("", "end", values=(
                            A.stage_cn(str(r.get("name") or "?")),
                            "%.1f 秒" % secs, "%.1f%%" % pct,
                            "█" * max(1, int(pct / 2.5))))
                    # 进行中的那一行（带 ▶ 标记 + 动态省略号）
                    pct_now = (el / grand * 100.0) if grand > 0 else 0.0
                    tw.insert("", "end", values=(
                        "▶ " + A.stage_cn(nm), "%.1f 秒…" % el,
                        "%.1f%%" % pct_now, "▌" * max(1, int(pct_now / 2.5))))
                    try:
                        tw.see(tw.get_children()[-1])
                    except Exception:
                        pass
                except Exception:
                    pass
            # 汇总标签也实时更新
            sm = widgets.get("translate.steps_sum")
            if sm is not None:
                try:
                    done_secs = sum(float(r.get("seconds") or 0)
                                    for r in stage_done)
                    el = time.time() - float(stage_live.get("start")
                                             or time.time())
                    sm.configure(
                        text="进行中 · 已用 %.1f 秒 · 完成 %d 个阶段"
                             % (done_secs + el, len(stage_done)),
                        fg=C["accent"])
                except Exception:
                    pass
            # ---- 术语表命中：实时刷新 ----
            _refresh_gloss_panel_live()
        root.after(400, _live_tick)

    # 术语表命中的实时刷新（记录上次的指纹，避免每 400ms 重建一次表格）
    _gloss_sig = {"v": None}

    def _refresh_gloss_panel_live() -> None:
        """从子进程留下的统计文件读术语命中，实时刷到面板上。

        为什么用文件：翻译跑在独立子进程里，父进程拿不到它的内存状态。
        glossary_hook 会把统计写成 JSON，这里轮询读取即可。
        """
        try:
            import glossary_hook as _gh
            st = _gh.read_stats_file()
        except Exception:
            return
        hits = st.get("term_hits") or {}
        if not isinstance(hits, dict):
            return
        sig = tuple(sorted(hits.items(), key=lambda x: -(x[1] or 0))[:20])
        if sig == _gloss_sig["v"]:
            return          # 没变化，不重绘
        _gloss_sig["v"] = sig
        try:
            _fill_gloss_panel(hits, int(st.get("glossary_size") or 0)
                              or H.gloss_total_terms())
        except Exception:
            pass

    # 词库条数缓存在 ui_helpers 里，这里重置一次（换词库后要重新读）

    # ================= 工具入口（复用 core.py 里的实现）=================
    def _open_studio():
        """打开自由编辑器。"""
        try:
            import ui_studio
        except Exception as exc:
            messagebox.showerror("模块缺失", "找不到 ui_studio.py：%s" % exc)
            return
        f = filedialog.askopenfilename(
            title="打开要编辑的 PDF", filetypes=[("PDF 文件", "*.pdf")],
            initialdir=A._existing_dir(None, Path.home() / "Documents", APP_DIR))
        if not f:
            return
        try:
            ui_studio.open_studio(root, C, f, on_save=lambda p: log("已保存编辑：" + p))
        except Exception as exc:
            messagebox.showerror("打开编辑器失败", str(exc))

    def _open_glossary():
        """术语表管理（实现在 ui_dialogs）。"""
        return D.open_glossary(root, C, _f, flat_btn, toast, APP_DIR)

    def _open_learner():
        """从 PDF 学词汇（实现在 ui_dialogs）。"""
        return D.open_learner(root, C, _f, flat_btn, toast, APP_DIR, log)

    def _open_viewer():
        try:
            import pdf_viewer
        except Exception as exc:
            messagebox.showerror("模块缺失", str(exc)); return
        f = filedialog.askopenfilename(
            title="打开 PDF", filetypes=[("PDF 文件", "*.pdf")],
            initialdir=A._existing_dir(None, Path.home() / "Documents", APP_DIR))
        if not f:
            return
        _viewer_window(pdf_viewer, f)

    def _viewer_window(pv, path):
        doc = pv.PdfDoc(path)
        win = tk.Toplevel(root)
        win.title("PDF 编辑器 — " + Path(path).name)
        win.geometry("980x740")
        try:
            win.configure(bg=C["bg"])      # 否则弹窗底色是系统白
        except Exception:
            pass
        top = tk.Frame(win, bg=C["bg"]); top.pack(fill="x", padx=10, pady=8)
        pg = tk.IntVar(value=1); zoom = tk.DoubleVar(value=1.4)
        # 阅读区用中性深灰（既衬托白纸，夜间也不刺眼）
        cv = tk.Canvas(win, bg=C.get("reader_bg") or C["bg_alt"],
                       highlightthickness=0)
        vs = ttk.Scrollbar(win, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y"); cv.pack(fill="both", expand=True, padx=10)
        ref = {"img": None}
        bot = tk.Frame(win, bg=C["bg"]); bot.pack(fill="x", padx=10, pady=8)
        txt_v = tk.StringVar()

        def redraw():
            try:
                png, w, h, _ = doc.render(pg.get() - 1, zoom.get())
                img = tk.PhotoImage(data=png)
            except Exception as exc:
                messagebox.showerror("渲染失败", str(exc), parent=win); return
            ref["img"] = img
            cv.delete("all"); cv.create_image(0, 0, anchor="nw", image=img)
            cv.configure(scrollregion=(0, 0, w, h))

        def click(ev):
            x = cv.canvasx(ev.x) / zoom.get(); y = cv.canvasy(ev.y) / zoom.get()
            t = txt_v.get().strip()
            if not t:
                messagebox.showinfo("请输入文字", "先在下面输入要加的文字。", parent=win); return
            try:
                pv.add_text_at(doc, pg.get() - 1, x, y, t, size=12, color="红")
            except Exception:
                pv.add_text_at(doc, pg.get() - 1, x, y, t, size=12, color="red")
            redraw()
        cv.bind("<Button-1>", click)

        flat_btn(top, "◀", lambda: (pg.set(max(1, pg.get() - 1)), redraw())).pack(side="left")
        flat_btn(top, "▶", lambda: (pg.set(min(doc.total, pg.get() + 1)), redraw())).pack(side="left", padx=4)
        tk.Label(top, text="页码", bg=C["bg"], fg=C["text"]).pack(side="left", padx=(10, 4))
        e = ttk.Entry(top, textvariable=pg, width=6); e.pack(side="left")
        e.bind("<Return>", lambda ev: redraw())
        for z, lab in ((1.0, "100%"), (1.4, "140%"), (2.0, "200%")):
            flat_btn(top, lab, lambda zz=z: (zoom.set(zz), redraw())).pack(side="left", padx=3)

        def save():
            d = filedialog.asksaveasfilename(
                title="保存为", defaultextension=".pdf",
                initialfile=Path(path).stem + "_已编辑.pdf",
                filetypes=[("PDF 文件", "*.pdf")],
                initialdir=A._existing_dir(Path(path).parent, APP_DIR))
            if d:
                doc.save_as(d); messagebox.showinfo("已保存", d, parent=win)
        flat_btn(top, "保存为新文件", save, "primary").pack(side="right")

        tk.Label(bot, text="点击画布加文字：", bg=C["bg"], fg=C["text_sub"]).pack(side="left")
        ttk.Entry(bot, textvariable=txt_v, width=30).pack(side="left", padx=6)
        flat_btn(bot, "旋转本页", lambda: (pv.rotate_current(doc, pg.get() - 1, 90), redraw())).pack(side="left")
        redraw()

    def _open_tools():
        import pdf_editor as pe
        win = tk.Toplevel(root); win.title("PDF 工具"); win.geometry("560x420")
        try:
            win.configure(bg=C["bg"])
        except Exception:
            pass
        nb = ttk.Notebook(win); nb.pack(fill="both", expand=True, padx=10, pady=10)
        src_v = tk.StringVar()

        def pickf():
            f = filedialog.askopenfilename(filetypes=[("PDF 文件", "*.pdf")],
                                           initialdir=A._existing_dir(None, APP_DIR))
            if f:
                src_v.set(f)
            return f

        def outname(suffix):
            base = Path(src_v.get()).stem if src_v.get() else "output"
            d = A.resolve_output_dir(A.load_config()) / "edit"
            d.mkdir(parents=True, exist_ok=True)
            return d / (base + suffix + ".pdf")

        def run(label, fn):
            if not src_v.get():
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            try:
                r = fn()
                log("【PDF 工具】%s → %s" % (label, r))
                messagebox.showinfo("完成", "%s\n%s" % (label, r), parent=win)
            except Exception as exc:
                messagebox.showerror("失败", str(exc), parent=win)

        def page(title, rows):
            p = ttk.Frame(nb, padding=14); nb.add(p, text=title)
            for i, (lab, var) in enumerate(rows):
                ttk.Label(p, text=lab).grid(row=i, column=0, sticky="w", pady=6)
                ttk.Entry(p, textvariable=var, width=36).grid(row=i, column=1, sticky="we", padx=8)
            p.columnconfigure(1, weight=1)
            return p

        spec_v = tk.StringVar(value="1-3")
        p1 = page("提取/删除页", [("页码", spec_v)])
        flat_btn(p1, "选文件…", pickf).grid(row=3, column=0, pady=10)
        flat_btn(p1, "提取", lambda: run("提取页",
                lambda: pe.extract_pages(src_v.get(), outname("_提取"), spec_v.get())),
                "primary").grid(row=3, column=1, sticky="w", pady=10)

        ang_v = tk.StringVar(value="90")
        p2 = page("旋转", [("角度", ang_v)])
        flat_btn(p2, "旋转 90°", lambda: run("旋转",
                lambda: pe.rotate_pages(src_v.get(), outname("_旋转"), "", 90))).grid(
                    row=3, column=0, columnspan=2, pady=10)

        fmt_v = tk.StringVar(value="第 {n} 页 / 共 {total} 页")
        p3 = page("加页码/水印", [("页码格式", fmt_v)])
        flat_btn(p3, "加页码", lambda: run("加页码",
                lambda: pe.add_page_numbers(src_v.get(), outname("_页码"), fmt_v.get())),
                "primary").grid(row=3, column=0, pady=10)
        wm_v = tk.StringVar(value="内部资料")
        ttk.Entry(p3, textvariable=wm_v, width=24).grid(row=4, column=1, sticky="w", padx=8)
        flat_btn(p3, "加水印", lambda: run("加水印",
                lambda: pe.add_watermark(src_v.get(), outname("_水印"), wm_v.get()))).grid(
                    row=4, column=0, pady=10)

        lv_v = tk.StringVar(value="medium")
        p4 = page("压缩", [("强度(light/medium/strong)", lv_v)])
        flat_btn(p4, "开始压缩", lambda: run("压缩",
                lambda: pe.compress_pdf(src_v.get(), outname("_压缩"), lv_v.get())),
                "primary").grid(row=3, column=0, columnspan=2, pady=10)



    def _open_inspect():
        import qa
        win = tk.Toplevel(root); win.title("检查工具"); win.geometry("660x480")
        try:
            win.configure(bg=C["bg"])
        except Exception:
            pass
        nb = ttk.Notebook(win); nb.pack(fill="both", expand=True, padx=10, pady=10)
        t1 = tk.Text(nb, wrap="word", bg=C["card"], fg=C["text"],
                     font=_f(9), relief="flat", padx=8, pady=6)
        nb.add(t1, text="术语表自检")
        r = qa.audit_glossary(APP_DIR / "glossary.csv")
        t1.insert("end", "术语表共 %d 条\n\n" % r.get("total", 0))
        t1.insert("end", "同词多译：%d 处\n" % len(r.get("conflicts") or []))
        t1.insert("end", "重复条目：%d 处\n" % len(r.get("duplicate") or []))
        t1.insert("end", "过短词：%d 个\n" % len(r.get("too_short") or []))
        t1.configure(state="disabled")

        t2 = tk.Text(nb, wrap="word", bg=C["card"], fg=C["text"],
                     font=_f(9), relief="flat", padx=8, pady=6)
        nb.add(t2, text="扫描版检测")
        t2.insert("end", "点下面按钮选择 PDF 检测\n")
        t2.configure(state="disabled")
        b = flat_btn(win, "选择 PDF 检测…", lambda: _scan(t2, qa))
        b.pack(pady=(0, 10))

    def _scan(t, qa):
        f = filedialog.askopenfilename(filetypes=[("PDF 文件", "*.pdf")],
                                       initialdir=A._existing_dir(None, APP_DIR))
        if not f:
            return
        r = qa.detect_scanned(f)
        t.configure(state="normal"); t.delete("1.0", "end")
        t.insert("end", "文件：%s\n页数：%d\n每页字符：%d\n\n" % (
            Path(f).name, r.get("pages", 0), r.get("chars_per_page", 0)))
        t.insert("end", "⚠️ 疑似扫描件，翻译会出空白，建议先 OCR。\n"
                 if r.get("scanned") else "✅ 有正常文字层，可以翻译。\n")
        t.configure(state="disabled")

    def A_open_settings():
        messagebox.showinfo("设置",
                            "设置窗口请用经典版（启动.vbs）打开。\n\n"
                            "侧边栏版正在完善中，目前已支持：翻译、历史、耗时、工具。")

    # ================= 组装 =================
    # ⑥ 全局拖放：整个窗口任意位置都能接 PDF
    _drop_tries = {"n": 0}

    def _enable_global_drop():
        """用 Windows 的 WM_DROPFILES 让整个窗口可接收拖放。

        窗口可能还没完全就绪（拿不到 TkTopLevel），失败就重试几次。
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM)
            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                                 ctypes.c_void_p]
            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM]
            # ★ 必须声明 DefWindowProcW 的 argtypes！
            # 不声明时，lparam（64 位）会触发
            #   OverflowError: int too long to convert
            # 而这个异常发生在 Windows 消息回调里 —— ctypes 处理不了，
            # 结果是【整个进程直接闪退】（用户反馈的「拖进去就闪退」）。
            user32.DefWindowProcW.restype = LRESULT
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                                 ctypes.c_void_p]
            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                               wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            WM_DROPFILES = 0x0233
            GWL_WNDPROC = -4

            # 显式声明类型：64 位下 HWND 是 8 字节指针，
            # 不声明 argtypes 时 ctypes 按 32 位 int 传递，高位会被截断。
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                             ctypes.c_int]
            user32.GetClassNameW.restype = ctypes.c_int
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND

            # 拿到真正的顶层窗口。
            # root.frame() 在窗口还没完全创建时可能返回内部子窗口（TkChild），
            # 而 WM_DROPFILES 只投递给顶层窗口（TkTopLevel）。
            # 这里用 winfo_id() + GetParent 往上找，并校验类名，
            # 找不到就退回 root.frame()。
            def _toplevel_hwnd():
                # 候选 1：winfo_id 的父窗口（Tk 里这就是顶层窗口）
                try:
                    wid = wintypes.HWND(root.winfo_id())
                    parent = user32.GetParent(wid)
                    pv = int(parent.value or 0) if hasattr(parent, "value") else int(parent or 0)
                    if pv:
                        buf = ctypes.create_unicode_buffer(256)
                        if user32.GetClassNameW(wintypes.HWND(pv), buf, 256) > 0:
                            if buf.value == "TkTopLevel":
                                return pv
                except Exception:
                    pass
                # 候选 2：root.frame()
                try:
                    fr = root.frame()
                    fv = int(fr, 16) if str(fr).startswith("0x") else int(fr)
                    buf = ctypes.create_unicode_buffer(256)
                    if user32.GetClassNameW(wintypes.HWND(fv), buf, 256) > 0:
                        if buf.value == "TkTopLevel":
                            return fv
                    # 候选 3：再往上找一层
                    p = user32.GetParent(wintypes.HWND(fv))
                    pv = int(p.value or 0) if hasattr(p, "value") else int(p or 0)
                    if pv:
                        return pv
                except Exception:
                    pass
                return 0

            hwnd_i = _toplevel_hwnd()
            if not hwnd_i:
                # 窗口还没就绪 → 稍后重试（最多 10 次，约 2 秒）
                _drop_tries["n"] += 1
                if _drop_tries["n"] <= 10:
                    root.after(200, _enable_global_drop)
                return False
            hwnd = wintypes.HWND(hwnd_i)
            shell32.DragAcceptFiles(hwnd, True)
            old_proc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)

            # ★ 关键：这个回调跑在 Windows 的消息线程里，**不能调用任何 Tk API**。
            # 之前在里面写 root.after(...) 需要 GIL，会触发
            #   Fatal Python error: PyEval_RestoreThread: GIL not held
            # 导致进程直接崩溃 —— 这就是「拖进去就闪退」的真正原因。
            # 现在回调只把路径塞进一个普通 list（纯 C 操作，不需要 GIL），
            # 由 Tk 主线程的定时器取走处理。
            _drop_queue: list = []
            root._drop_queue = _drop_queue

            def handler(h, msg, wparam, lparam):
                try:
                    if msg == WM_DROPFILES:
                        n = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        got = []
                        for i in range(n):
                            buf = ctypes.create_unicode_buffer(1024)
                            shell32.DragQueryFileW(wparam, i, buf, 1024)
                            v = buf.value
                            if v and ("." in v):
                                got.append(v)
                        shell32.DragFinish(wparam)
                        if got:
                            _drop_queue.append(got)
                        return 0
                except Exception:
                    pass
                try:
                    if old_proc:
                        return user32.CallWindowProcW(
                            ctypes.c_void_p(old_proc), h, msg, wparam, lparam)
                    return user32.DefWindowProcW(h, msg, wparam, lparam)
                except Exception:
                    return 0

            cb = WNDPROC(handler)
            user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC,
                                     ctypes.cast(cb, ctypes.c_void_p))
            root._drop_cb = cb
            root._drop_hwnd = hwnd
            root._drop_old = old_proc
            root._drop_ok = True

            # Tk 主线程轮询：把回调塞进队列的路径取出来处理
            def _drain_drop_queue():
                try:
                    while _drop_queue:
                        batch = _drop_queue.pop(0)
                        try:
                            _on_drop_files(batch)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    root.after(120, _drain_drop_queue)
                except Exception:
                    pass
            root.after(120, _drain_drop_queue)

            return True
        except Exception as exc:
            # 不再静默失败：记下原因，方便排查
            try:
                root._drop_err = "%s: %s" % (type(exc).__name__, exc)
            except Exception:
                pass
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            return False

    def _on_drop_files(paths):
        """拖入文件/文件夹的处理。

        行为约定（按用户使用习惯）：
          · 空闲时拖入 1 个     → 立即开始
          · 空闲时拖入多个      → 确认后开始，其余排队
          · 【翻译中】拖入任何  → 全部【追加】到现有队列末尾，不打断当前任务
          · 支持文件夹          → 递归收集里面的 PDF

        之前每次拖入都 state["batch"] = files[1:]，会直接覆盖队列，
        导致「第二次拖入把第一次的队列冲掉」——用户反馈的
        「不能同时投进去」就是这个原因。
        """
        try:
            show("translate")

            # 展开：文件夹 → 里面的 PDF；非 PDF 跳过
            expanded = []
            skipped = 0
            for p in paths:
                pp = Path(str(p))
                try:
                    if pp.is_dir():
                        found = sorted(pp.rglob("*.pdf"))
                        if found:
                            expanded.extend(found)
                        else:
                            skipped += 1
                    elif pp.suffix.lower() == ".pdf":
                        expanded.append(pp)
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1

            if not expanded:
                if skipped:
                    toast("没有找到 PDF（拖入的是文件夹的话，里面没有 PDF）",
                          "warn")
                else:
                    toast("没有可翻译的 PDF", "warn")
                return

            # 去重：已经在队列里、或正在翻译的那个，不再重复添加
            files = []
            seen = set()
            _cur = state.get("cur_file") or ""
            if _cur:
                seen.add(str(Path(_cur).resolve()))
            for p in (state.get("batch") or []):
                try:
                    seen.add(str(Path(p).resolve()))
                except Exception:
                    seen.add(str(p))
            dup = 0
            for x in expanded:
                try:
                    key = str(Path(x).resolve())
                except Exception:
                    key = str(x)
                if key in seen:
                    dup += 1
                    continue
                seen.add(key)
                files.append(str(x))

            if not files:
                toast("这些文件已经在队列里了", "warn")
                return

            running = bool(state.get("running"))

            # —— 情况 A：正在翻译 —— 直接追加，不打断、不再确认 ——
            if running:
                cur_batch = list(state.get("batch") or [])
                state["batch"] = cur_batch + files
                _update_queue()
                m = "已加入队列：%d 个（队列中共 %d 个待处理）" % (
                    len(files), len(state["batch"]))
                if dup:
                    m += "，跳过重复 %d 个" % dup
                toast(m, "ok")
                return

            # —— 情况 B：空闲 ——
            # 单个文件也要走确认窗口（输出版本/页码/位置）。
            # 之前拖入单个直接开始，想改设置都没机会，
            # 和「选择 PDF」按钮的行为也不一致。
            if len(files) == 1:
                if confirm_output(files[0]):
                    start(files[0])
                return

            # 多个文件：弹「可勾选」的确认窗口，而不是只问一句是不是翻译。
            # 之前拖进 10 个，发现第 3 个选错了只能全部取消重来。
            try:
                import ui_batch
                _go, _picked = ui_batch.confirm_batch(
                    root, files, C, _f, flat_btn, toast, A)
            except Exception as _exc:
                toast("批量确认窗口打不开：%s" % _exc, "err")
                _go, _picked = False, []
            if not _go or not _picked:
                return
            # 设置已在那个窗口里设好并保存，
            # 不再弹第二个确认框（之前要弹两次，很繁）
            state["batch"] = _picked[1:]
            _update_queue()
            start(_picked[0])
        except Exception as exc:
            toast("处理拖入文件失败：%s" % exc, "err")

    try:
        root.after(300, _enable_global_drop)
    except Exception:
        pass

    def _register_pages():
        """注册 5 个导航页（重建界面时会被再次调用）。"""
        mk_item("translate", "🌐", "翻译", page_translate)
        mk_item("history", "🕘", "历史记录", page_history)
        mk_item("tools", "🧰", "工具", page_tools)
        mk_item("settings", "⚙", "设置", page_settings)

    _register_pages()
    show("translate")
    pump()

    # ⑧ 托盘：启动后挂上；关闭时清理
    tray_ok = {"ok": False}

    def _init_tray():
        tray_ok["ok"] = _setup_tray()
        if tray_ok["ok"]:
            set_status("托盘图标已就绪")
    try:
        root.after(1200, _init_tray)
    except Exception:
        pass

    def _on_close():
        """关窗口：翻译中要二次确认，避免手滑丢掉几十分钟的进度。"""
        # ④ 翻译中关闭 → 先问
        try:
            if state.get("running"):
                pending = len(state.get("batch") or [])
                msg = "翻译还没完成，确定要退出吗？\n\n"
                if pending:
                    msg += "队列里还有 %d 个文件没处理。\n" % pending
                msg += ("退出后本次翻译会中止"
                        "（已翻完的页会记入断点，下次可续译）。")
                if not messagebox.askyesno("正在翻译", msg, parent=root):
                    return
                try:
                    A.cancel_translation()
                except Exception:
                    pass
                # 队列先落盘，下次打开能恢复
                try:
                    A.save_queue([{"path": p, "name": Path(p).name}
                                  for p in (state.get("batch") or [])])
                except Exception:
                    pass
        except Exception:
            pass

        try:
            rem = getattr(root, "_tray_remove", None)
            if rem:
                rem()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    try:
        root.protocol("WM_DELETE_WINDOW", _on_close)
    except Exception:
        pass

    # ===== 启动时检查未完成的翻译 =====
    # 用户关掉软件再打开，这里会列出上次没翻完的文件，
    # 可以选择「继续翻译」或「忽略」。
    def _resume_task(task):
        """用户选了某个未完成任务 → 直接开始翻剩下的页。"""
        try:
            path = task.get("path") or ""
            rest = task.get("rest") or ""
            if not path or not Path(path).exists():
                toast("源文件找不到了：%s" % path, "err")
                return
            # 把页码范围临时设成剩余页
            c = A.load_config()
            c["pages"] = rest
            A.save_config(c)
            show("translate")
            start(path)
        except Exception as exc:
            toast("继续翻译失败：%s" % exc, "err")

    def _check_resume():
        try:
            import ui_resume
            shown = ui_resume.show_resume_dialog(
                root, C, _f, flat_btn, toast, A, _resume_task)
            if not shown:
                return
        except Exception as exc:
            try:
                toast("续译提示打不开：%s" % exc, "warn")
            except Exception:
                pass

    try:
        root.after(900, _check_resume)
    except Exception:
        pass

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

    def _round_window(win):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            return True
        except Exception:
            return False

    try:
        root.after(200, lambda: _round_window(root))
    except Exception:
        pass
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass

    # 配色（支持「跟随系统」）
    def _system_prefers_dark() -> bool:
        """读 Windows 的深色模式偏好。"""
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                return int(v) == 0
        except Exception:
            return False

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
        mode = "dark" if _system_prefers_dark() else "light"
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
        """把主题应用到 ttk 样式（可重复调用 —— 热切换靠它）。"""
        try:
            st = ttk.Style()
            st.theme_use("clam")
            st.configure(".", font=(FONT, _FS),
                         background=theme["bg"], foreground=theme["text"])
            try:
                import tkinter.font as _tf
                for _nm, _sz in (("TkDefaultFont", _FS), ("TkTextFont", _FS),
                                 ("TkMenuFont", _FS), ("TkHeadingFont", _FS)):
                    _tf.nametofont(_nm).configure(family=FONT, size=_sz)
            except Exception:
                pass
            st.configure("TFrame", background=theme["bg"])
            st.configure("TLabel", background=theme["bg"], foreground=theme["text"])
            # Treeview：clam 会给它画浅色的 3D 边框（lightcolor/darkcolor），
            # 必须一起改掉，否则夜间看着就是一圈白边。
            st.configure("Treeview", rowheight=int(24 * dpi / 96.0),
                         background=theme["card"],
                         fieldbackground=theme["card"], borderwidth=0,
                         relief="flat",
                         bordercolor=theme["border"],
                         lightcolor=theme["card"],
                         darkcolor=theme["card"],
                         foreground=theme["text"])
            st.map("Treeview",
                   background=[("selected", theme["accent"])],
                   foreground=[("selected", "#ffffff")])
            st.configure("Treeview.Item", background=theme["card"],
                         foreground=theme["text"])
            # 这两行是关键：clam 用 lightcolor/darkcolor 画 3D 立体边框，
            # 默认是 #ffffff —— 不显式覆盖，表格四周就是一圈白线。
            st.configure("Treeview.Heading",
                         background=theme["head_bg"],
                         foreground=theme["text_sub"], relief="flat",
                         bordercolor=theme["border"],
                         lightcolor=theme["head_bg"],
                         darkcolor=theme["head_bg"])
            for _st_nm in ("Treeview", "Treeview.Item", "Treeview.Cell",
                           "Treeview.Row"):
                try:
                    st.configure(_st_nm, lightcolor=theme["card"],
                                 darkcolor=theme["card"],
                                 bordercolor=theme["border"])
                except Exception:
                    pass
            st.configure("Treeview.Heading", background=theme["head_bg"],
                         foreground=theme["text_sub"], relief="flat")
            st.map("Treeview", background=[("selected", theme["accent"])],
                   foreground=[("selected", "#ffffff")])
            st.map("Treeview.Heading",
                   background=[("active", theme["accent_sf"])])
            # TScrollbar：每页都有，白边最明显。
            # clam 的滚动条用 lightcolor/darkcolor 画 3D 立体边，
            # 必须全部设成主题色，否则就是一圈白。
            for _sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
                st.configure(_sb,
                             troughcolor=theme["bg_alt"],
                             background=theme["border"],
                             bordercolor=theme["bg_alt"],
                             lightcolor=theme["bg_alt"],
                             darkcolor=theme["bg_alt"],
                             arrowcolor=theme["text_sub"],
                             borderwidth=0, arrowsize=13, relief="flat",
                             gripcount=0)
                st.map(_sb,
                       background=[("active", theme["muted"]),
                                   ("pressed", theme["accent"])],
                       arrowcolor=[("active", theme["text"])],
                       lightcolor=[("active", theme["bg_alt"])],
                       darkcolor=[("active", theme["bg_alt"])])
            st.configure("TCombobox", fieldbackground=theme["entry_bg"],
                         background=theme["card"], foreground=theme["text"],
                         bordercolor=theme["border"],
                         lightcolor=theme["border"], darkcolor=theme["border"],
                         arrowcolor=theme["text_sub"],
                         relief="flat", borderwidth=1)
            st.map("TCombobox",
                   fieldbackground=[("readonly", theme["entry_bg"])],
                   bordercolor=[("focus", theme["accent"])],
                   lightcolor=[("focus", theme["accent"])],
                   darkcolor=[("focus", theme["accent"])])
            # TEntry：同样要覆盖 lightcolor/darkcolor（clam 的 3D 边）
            st.configure("TEntry", fieldbackground=theme["entry_bg"],
                         foreground=theme["text"], insertcolor=theme["text"],
                         bordercolor=theme["border"],
                         lightcolor=theme["border"],
                         darkcolor=theme["border"],
                         relief="flat", borderwidth=1)
            st.map("TEntry",
                   bordercolor=[("focus", theme["accent"])],
                   lightcolor=[("focus", theme["accent"]),
                               ("!focus", theme["border"])],
                   darkcolor=[("focus", theme["accent"]),
                              ("!focus", theme["border"])],
                   fieldbackground=[("readonly", theme["entry_bg"])])
            # ---- 之前漏掉的 ttk 样式：这些是"夜间还有白块/白边"的元凶 ----
            # clam 主题默认给复选框/单选钮/进度条/滑块配了浅色，
            # 不显式覆盖的话，夜间模式下就是一个个白方块。
            # 复选框：clam 的小方块边框也用 lightcolor/darkcolor，
            # 默认 #ffffff —— 夜间就是一个个白框。
            st.configure("TCheckbutton", background=theme["bg"],
                         foreground=theme["text"],
                         indicatorbackground=theme["entry_bg"],
                         indicatorforeground=theme["accent"],
                         focuscolor=theme["bg"], bordercolor=theme["border"],
                         lightcolor=theme["entry_bg"],
                         darkcolor=theme["entry_bg"],
                         indicatorrelief="flat",
                         borderwidth=0)
            st.map("TCheckbutton",
                   indicatorbackground=[("selected", theme["accent"]),
                                        ("active", theme["entry_bg"])],
                   indicatorforeground=[("selected", "#ffffff")],
                   lightcolor=[("selected", theme["accent"]),
                               ("!selected", theme["entry_bg"])],
                   darkcolor=[("selected", theme["accent"]),
                              ("!selected", theme["entry_bg"])])
            st.map("TCheckbutton",
                   background=[("active", theme["bg"])],
                   foreground=[("disabled", theme["muted"])],
                   indicatorbackground=[("selected", theme["accent"]),
                                        ("active", theme["entry_bg"])],
                   indicatorforeground=[("selected", "#ffffff")])
            st.configure("TRadiobutton", background=theme["bg"],
                         foreground=theme["text"],
                         indicatorbackground=theme["entry_bg"],
                         indicatorforeground=theme["accent"],
                         focuscolor=theme["bg"], bordercolor=theme["border"],
                         lightcolor=theme["entry_bg"],
                         darkcolor=theme["entry_bg"],
                         indicatorrelief="flat",
                         borderwidth=0)
            st.map("TRadiobutton",
                   background=[("active", theme["bg"])],
                   foreground=[("disabled", theme["muted"])],
                   indicatorbackground=[("selected", theme["accent"])])
            st.configure("TButton", background=theme["head_bg"],
                         foreground=theme["text"], bordercolor=theme["border"],
                         focuscolor=theme["bg"], relief="flat",
                         lightcolor=theme["head_bg"],
                         darkcolor=theme["head_bg"])
            st.map("TButton",
                   background=[("active", theme["accent_sf"]),
                               ("pressed", theme["accent_sf"]),
                               ("disabled", theme["bg_alt"])],
                   foreground=[("disabled", theme["muted"])])
            st.configure("TProgressbar", background=theme["accent"],
                         troughcolor=theme["entry_bg"],
                         bordercolor=theme["entry_bg"],
                         lightcolor=theme["accent"],
                         darkcolor=theme["accent"],
                         borderwidth=0, thickness=int(12 * dpi / 96.0))
            # 加粗版：翻译页用这个，细条看不清进度
            st.configure("Thick.Horizontal.TProgressbar",
                         background=theme["accent"],
                         troughcolor=theme["entry_bg"],
                         bordercolor=theme["border"],
                         lightcolor=theme["accent"],
                         darkcolor=theme["accent"],
                         borderwidth=0, thickness=int(18 * dpi / 96.0))
            st.configure("Horizontal.TScale", background=theme["bg"],
                         troughcolor=theme["entry_bg"],
                         bordercolor=theme["border"],
                         lightcolor=theme["accent"],
                         darkcolor=theme["accent"])
            st.configure("Vertical.TScale", background=theme["bg"],
                         troughcolor=theme["entry_bg"],
                         bordercolor=theme["border"],
                         lightcolor=theme["accent"],
                         darkcolor=theme["accent"])
            st.configure("Horizontal.TScrollbar", troughcolor=theme["bg_alt"],
                         background=theme["border"], borderwidth=0,
                         arrowsize=13, bordercolor=theme["bg_alt"],
                         lightcolor=theme["border"], darkcolor=theme["border"])
            st.map("Horizontal.TScrollbar",
                   background=[("active", theme["muted"])])
            st.map("Vertical.TScrollbar",
                   background=[("active", theme["muted"])])
            st.configure("TSeparator", background=theme["border"])
            st.configure("TLabelframe", background=theme["bg"],
                         foreground=theme["text"],
                         bordercolor=theme["border"])
            st.configure("TLabelframe.Label", background=theme["bg"],
                         foreground=theme["text_sub"])
            st.configure("TNotebook", background=theme["bg"],
                         bordercolor=theme["border"], lightcolor=theme["bg"],
                         darkcolor=theme["bg"])
            st.configure("TNotebook.Tab", background=theme["head_bg"],
                         foreground=theme["text_sub"], lightcolor=theme["head_bg"],
                         darkcolor=theme["head_bg"], padding=(10, 5))
            st.map("TNotebook.Tab",
                   background=[("selected", theme["card"])],
                   foreground=[("selected", theme["text"])])
            st.configure("TFrame", background=theme["bg"])
            # ---- 原生 tk 控件的系统默认色 ----
            # 这些是"夜间还有白边"的另一个来源：
            #   · highlightcolor 默认是 SystemWindowFrame（系统白/黑）
            #     聚焦时会给 Listbox/Text 画一圈系统色边框
            #   · Checkbutton 的小方块（selectcolor）默认是系统白
            #   · Menu 默认用系统配色，夜间下是浅灰
            root.option_add("*highlightColor", theme["accent"])
            root.option_add("*HighlightColor", theme["accent"])
            root.option_add("*highlightBackground", theme["border"])
            root.option_add("*HighlightBackground", theme["border"])
            root.option_add("*Listbox.background", theme["entry_bg"])
            root.option_add("*Listbox.foreground", theme["text"])
            root.option_add("*Listbox.selectBackground", theme["accent"])
            root.option_add("*Listbox.selectForeground", "#ffffff")
            root.option_add("*Listbox.highlightColor", theme["accent"])
            root.option_add("*Listbox.highlightBackground", theme["border"])
            root.option_add("*Listbox.borderWidth", 1)
            root.option_add("*Text.background", theme["log_bg"])
            root.option_add("*Text.foreground", theme["log_fg"])
            root.option_add("*Text.insertBackground", theme["log_fg"])
            root.option_add("*Text.highlightColor", theme["accent"])
            root.option_add("*Text.highlightBackground", theme["border"])
            root.option_add("*Text.selectBackground", theme["accent"])
            root.option_add("*Text.selectForeground", "#ffffff")
            root.option_add("*Checkbutton.background", theme["bg"])
            root.option_add("*Checkbutton.foreground", theme["text"])
            root.option_add("*Checkbutton.activeBackground", theme["bg"])
            root.option_add("*Checkbutton.activeForeground", theme["accent"])
            root.option_add("*Checkbutton.selectColor", theme["accent"])
            root.option_add("*Checkbutton.highlightThickness", 0)
            root.option_add("*Entry.background", theme["entry_bg"])
            root.option_add("*Entry.foreground", theme["text"])
            root.option_add("*Entry.insertBackground", theme["text"])
            root.option_add("*Entry.highlightThickness", 0)
            # 菜单（右键菜单）也要跟着主题
            root.option_add("*Menu.background", theme["card"])
            root.option_add("*Menu.foreground", theme["text"])
            root.option_add("*Menu.activeBackground", theme["accent"])
            root.option_add("*Menu.activeForeground", "#ffffff")
            root.option_add("*Menu.borderWidth", 0)
            root.option_add("*Menu.activeBorderWidth", 0)
            root.option_add("*TCombobox*Listbox.background", theme["entry_bg"])
            root.option_add("*TCombobox*Listbox.foreground", theme["text"])
            root.option_add("*TCombobox*Listbox.selectBackground", theme["accent"])
            root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        except Exception:
            pass

    def _theme_native_widgets(w) -> None:
        """把原生 tk 控件里残留的"系统色"改成主题色。

        为什么需要单独走一遍：
          · option_add 只影响之后新建的控件
          · Listbox / Text 的 highlightColor 默认是 SystemWindowFrame，
            聚焦时会画一圈系统色边框 —— 夜间看着就是白边
          · 原生 Checkbutton 的小方块默认是系统白
        """
        theme = C
        try:
            kids = w.winfo_children()
        except Exception:
            return
        for c in kids:
            cls = ""
            try:
                cls = c.winfo_class()
            except Exception:
                pass
            try:
                if cls == "Listbox":
                    c.configure(background=theme["entry_bg"],
                                foreground=theme["text"],
                                highlightcolor=theme["accent"],
                                highlightbackground=theme["border"],
                                selectbackground=theme["accent"],
                                selectforeground="#ffffff",
                                borderwidth=1, relief="flat")
                elif cls == "Text":
                    c.configure(background=theme["log_bg"],
                                foreground=theme["log_fg"],
                                insertbackground=theme["log_fg"],
                                highlightcolor=theme["accent"],
                                highlightbackground=theme["border"],
                                selectbackground=theme["accent"],
                                selectforeground="#ffffff",
                                borderwidth=0, relief="flat")
                elif cls == "Checkbutton":
                    c.configure(background=theme["bg"],
                                foreground=theme["text"],
                                activebackground=theme["bg"],
                                activeforeground=theme["accent"],
                                selectcolor=theme["accent"],
                                highlightthickness=0, borderwidth=0)
                elif cls == "Entry":
                    c.configure(background=theme["entry_bg"],
                                foreground=theme["text"],
                                insertbackground=theme["text"],
                                highlightthickness=0, relief="flat")
                elif cls == "Menu":
                    c.configure(background=theme["card"],
                                foreground=theme["text"],
                                activebackground=theme["accent"],
                                activeforeground="#ffffff",
                                selectcolor=theme["accent"],
                                disabledforeground=theme["muted"],
                                borderwidth=0, relief="flat")
                elif cls == "Canvas":
                    # Canvas 默认的 insert/select 色也是系统色
                    c.configure(background=c.cget("background"),
                                highlightthickness=0,
                                insertbackground=theme["text"],
                                selectbackground=theme["accent"],
                                selectforeground="#ffffff")
                elif cls == "Label":
                    c.configure(activebackground=theme["bg"],
                                activeforeground=theme["accent"],
                                selectbackground=theme["accent"],
                                selectforeground="#ffffff",
                                highlightthickness=0)
                elif cls == "Frame":
                    c.configure(highlightthickness=0)
            except Exception:
                pass
            _theme_native_widgets(c)

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

    def _resolve_theme(cfg: dict) -> str:
        m = str(cfg.get("ui_theme") or "light").lower()
        if m == "auto":
            m = "dark" if _system_prefers_dark() else "light"
        return m if m in A.THEMES else "light"

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
        m = _resolve_theme(cfg)

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
        """为一个页面创建（或复用）容器。"""
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

        # 更新导航高亮
        for k, it in items.items():
            on = (k == key)
            bg = C["sidebar_on"] if on else C["sidebar"]
            try:
                it["row"].configure(bg=bg)
                it["lbl"].configure(bg=bg, fg=C["accent"] if on else C["text"],
                                    font=_f(10, "bold" if on else "normal"))
                it["bar"].configure(bg=C["accent"] if on else C["sidebar"])
            except Exception:
                pass

        # 隐藏所有已建页面（pack_forget 很快，不销毁控件）
        for k, h in page_cache.items():
            try:
                if h.winfo_exists() and h.winfo_manager():
                    h.pack_forget()
            except Exception:
                pass

        # 首次进入该页才构建
        holder = page_cache.get(key)
        if holder is None or not holder.winfo_exists():
            holder = _make_holder(key)

        if not page_built.get(key):
            try:
                # 注意：这里【不能】widgets.clear() —— 那会把其它页面
                # （尤其是翻译页的 prog/log）的控件引用一起清掉，
                # 导致翻译时进度条和日志都不再更新。
                _cur_ns["k"] = key
                items[key]["build"](holder)
                page_built[key] = True
            except Exception as exc:
                try:
                    for ch in list(holder.winfo_children()):
                        ch.destroy()
                except Exception:
                    pass
                tk.Label(holder, text="页面加载失败：%s" % exc, bg=C["bg"],
                         fg=C["danger"]).pack(padx=20, pady=20)
                traceback.print_exc()

        try:
            holder.pack(fill="both", expand=True)
        except Exception:
            pass

        # 页面是懒加载的，刚建好时补一遍原生控件主题化
        # （option_add 只对新控件生效，且这里必须等控件真的建出来）
        try:
            _theme_native_widgets(holder)
        except Exception:
            pass

        # ⑦ 切页过渡：内容从下方轻微滑入（120ms，不阻塞操作）
        if animate:
            try:
                holder.pack_configure(pady=(8, 0))

                def _slide(i=0, steps=4):
                    if i > steps:
                        try:
                            holder.pack_configure(pady=0)
                        except Exception:
                            pass
                        return
                    try:
                        if not holder.winfo_exists():
                            return
                        holder.pack_configure(
                            pady=(int(8 * (1 - i / float(steps))), 0))
                        holder.after(30, lambda: _slide(i + 1))
                    except Exception:
                        pass
                _slide()
            except Exception:
                pass

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

    def toast(text, kind="info"):
        """⑤ Toast 提示（不打断操作，几秒自动消失）。"""
        try:
            if _W is not None:
                _W.toast(root, C, text, kind=kind)
        except Exception:
            pass

    def header(parent, title, subtitle=""):
        tk.Label(parent, text=title, bg=C["bg"], fg=C["text"],
                 font=_f(14, "bold"), anchor="w").pack(
            anchor="w", padx=20, pady=(18, 0))
        if subtitle:
            tk.Label(parent, text=subtitle, bg=C["bg"], fg=C["muted"],
                     font=_f(9), anchor="w").pack(anchor="w", padx=20, pady=(3, 0))

    def card(parent, radius=12):
        """③ 圆角卡片容器（比直角 Frame 柔和）。"""
        if _W is not None:
            try:
                return _W.Card(parent, C, radius=radius)
            except Exception:
                pass
        f = tk.Frame(parent, bg=C["card"], highlightbackground=C["border"],
                     highlightthickness=1)
        return f

    def loading(parent, text="加载中…"):
        """⑥ 加载态提示（避免"卡住了"的错觉）。"""
        lbl = tk.Label(parent, text="◌  " + text, bg=C["bg"], fg=C["accent"],
                       font=_f(11))
        lbl.pack(pady=30)

        def tick(i=0):
            try:
                if not lbl.winfo_exists():
                    return
                frames = ["◌", "◔", "◑", "◕", "●", "◕", "◑", "◔"]
                lbl.configure(text=frames[i % len(frames)] + "  " + text)
                lbl.after(120, lambda: tick(i + 1))
            except Exception:
                pass
        tick()
        return lbl

    def empty_state(parent, icon, title, hint="", action=None,
                    action_text=""):
        """⑧ 空状态：图标 + 说明 + 可选按钮。

        比光秃秃一行"暂无数据"友好得多 —— 用户一眼知道该干什么。
        """
        box = tk.Frame(parent, bg=C["bg"])
        box.pack(expand=True, pady=40)
        tk.Label(box, text=icon, bg=C["bg"], fg=C["border"],
                 font=_f(40)).pack()
        tk.Label(box, text=title, bg=C["bg"], fg=C["text_sub"],
                 font=_f(11)).pack(pady=(8, 2))
        if hint:
            tk.Label(box, text=hint, bg=C["bg"], fg=C["muted"],
                     font=_f(9), wraplength=360, justify="center").pack()
        if action and action_text:
            holder = tk.Frame(box, bg=C["bg"])
            holder.pack(pady=(14, 0))
            flat_btn(holder, action_text, action, "primary")
        return box

    def flat_btn(parent, text, cmd, kind="normal"):
        """建一个按钮。

        优先用 Canvas 自绘的【圆角按钮】（带平滑动画）；
        万一 ui_widgets 缺失，退回普通 Label 按钮，保证功能不断。
        """
        if _W is not None:
            try:
                b = _W.make_round_button(parent, text, cmd, kind=kind, C=C,
                                         radius=9, padx=16, pady=7,
                                         font=_f(10))
                return b
            except Exception:
                pass
        # 兜底：直角 Label 按钮
        pal = {"primary": (C["accent"], "#ffffff", C["accent_hv"]),
               "danger": (C["head_bg"], C["danger"], C["accent_sf"])}.get(
                   kind, (C["head_bg"], C["text"], C["accent_sf"]))
        bg, fg, hv = pal
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=_f(10),
                     padx=15, pady=8, cursor="hand2")
        b.bind("<Enter>", lambda e: b.configure(bg=hv), add="+")
        b.bind("<Leave>", lambda e: b.configure(bg=bg), add="+")
        b.bind("<Button-1>", lambda e: cmd(), add="+")
        return b

    # ================= 页面：翻译 =================
    def page_translate(parent):
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
        q_lb = tk.Listbox(qf, height=2, bg=C["entry_bg"], fg=C["text_sub"],
                          font=_f(9), relief="flat", activestyle="none",
                          highlightbackground=C["border"], highlightthickness=1,
                          selectbackground=C["accent"], selectforeground="#ffffff")
        q_lb.pack(fill="x", pady=(2, 0))
        widgets["translate.queue"] = q_lb

        # ⑥ 拖拽排序：按下记录位置，松开时把该行移到新位置
        q_drag = {"idx": None}

        def q_press(e):
            q_drag["idx"] = q_lb.nearest(e.y)
            try:
                q_lb.selection_clear(0, "end")
                q_lb.selection_set(q_drag["idx"])
            except Exception:
                pass

        def q_motion(e):
            if q_drag["idx"] is None:
                return
            tgt = q_lb.nearest(e.y)
            if tgt == q_drag["idx"] or tgt < 0:
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
                q_lb.selection_clear(0, "end")
                q_lb.selection_set(tgt)
            except Exception:
                pass

        def q_release(_e=None):
            q_drag["idx"] = None
            _update_queue()

        q_lb.bind("<ButtonPress-1>", q_press)
        q_lb.bind("<B1-Motion>", q_motion)
        q_lb.bind("<ButtonRelease-1>", q_release)

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

        # 页面被缓存，重新进入时刷新"最近打开"
        widgets["translate.on_enter"] = refresh_recent


    # ================= 页面：历史 =================
    def page_history(parent):
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
            """删除选中的历史记录 —— 可选同时删掉输出文件。"""
            sel = tw.selection()
            if not sel:
                toast("先选中要删除的记录（可按住 Ctrl 多选）", "warn")
                return
            items = _rows_to_items(sel)
            if not items:
                return
            files = _files_of(items)

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
                if del_files.get():
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
            try:
                A.HISTORY_FILE.write_text("[]", encoding="utf-8")
            except Exception:
                pass
            refresh()
            toast("历史已清空", "ok")

        flat_btn(btns, "清空全部", clear_all, "danger").pack(side="right")
        tk.Label(btns, text="（右键记录有更多操作）", bg=C["bg"], fg=C["muted"],
                 font=_f(8)).pack(side="left", padx=8)
        refresh()
        # 页面被缓存，重新进入时刷新历史列表（新建的记录才会出现）
        widgets["history.on_enter"] = refresh

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
        header(parent, "设置", "引擎、术语表、输出、界面 —— 界面那组改完立即生效")

        # ⑤ 设置搜索：选项多时快速定位
        sbar = tk.Frame(parent, bg=C["bg"])
        sbar.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(sbar, text="🔍", bg=C["bg"], fg=C["muted"],
                 font=_f(10)).pack(side="left")
        search_v = tk.StringVar()
        s_entry = ttk.Entry(sbar, textvariable=search_v, width=28)
        s_entry.pack(side="left", padx=(6, 8))
        s_hint = tk.Label(sbar, text="输入关键词快速跳转（如 并发 / 主题 / 术语）",
                          bg=C["bg"], fg=C["muted"], font=_f(8))
        s_hint.pack(side="left")

        # ============ 二级导航：左侧竖排分类 + 右侧内容 ============
        cfg = A.load_config()

        body = tk.Frame(parent, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(8, 18))

        # 左：竖排分类列表
        nav2 = tk.Frame(body, bg=C["bg_alt"], width=136)
        nav2.pack(side="left", fill="y")
        nav2.pack_propagate(False)
        # 竖向分隔线
        tk.Frame(body, bg=C["border"], width=1).pack(side="left", fill="y",
                                                      padx=(0, 12))

        # 右：内容区（可滚动）
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)
        cvs = tk.Canvas(right, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(right, orient="vertical", command=cvs.yview)
        cvs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)
        holder = tk.Frame(cvs, bg=C["bg"])
        wid = cvs.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>",
                    lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.bind("<Configure>", lambda e: cvs.itemconfigure(wid, width=e.width))
        # 滚轮
        def _w(e):
            try:
                cvs.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        cvs.bind("<Enter>", lambda e: cvs.bind_all("<MouseWheel>", _w))
        cvs.bind("<Leave>", lambda e: cvs.unbind_all("<MouseWheel>"))

        # 分类定义：key -> (图标, 名称)
        CATS = [
            ("engine",  "🌐", "翻译引擎"),
            ("perf",    "⚡", "性能"),
            ("gloss",   "📖", "术语表"),
            ("output",  "📁", "输出"),
            ("ui",      "🎨", "界面"),
            ("preset",  "💾", "预设"),
            ("about",   "ℹ️", "关于"),
        ]
        # 每一类的容器（懒建）
        pages: dict = {}
        cur_cat = {"k": "engine"}
        nav_rows: dict = {}

        def _build_cat(key):
            """建某一类的内容（只建一次）。"""
            if key in pages:
                return pages[key]
            f = tk.Frame(holder, bg=C["bg"])
            pages[key] = f
            return f

        def show_cat(key):
            cur_cat["k"] = key
            for k, rowd in nav_rows.items():
                on = (k == key)
                try:
                    # 选中：文字变蓝加粗 + 左侧一道彩色竖条
                    rowd["row"].configure(bg=C["accent_sf"] if on else C["bg_alt"])
                    rowd["lbl"].configure(
                        bg=C["accent_sf"] if on else C["bg_alt"],
                        fg=C["accent"] if on else C["text_sub"],
                        font=_f(10, "bold") if on else _f(10))
                    rowd["bar"].configure(
                        bg=C["accent"] if on else C["bg_alt"])
                except Exception:
                    pass
            for k, f in pages.items():
                try:
                    if f.winfo_exists() and f.winfo_manager():
                        f.pack_forget()
                except Exception:
                    pass
            f = pages.get(key)
            if f is None:
                return
            f.pack(fill="both", expand=True)
            try:
                cvs.yview_moveto(0)
            except Exception:
                pass

        def mk_cat(key, icon, label):
            """竖排分类项：左侧一条彩色竖条表示选中。"""
            row = tk.Frame(nav2, bg=C["bg_alt"], cursor="hand2")
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, bg=C["bg_alt"], width=3)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(row, text="%s  %s" % (icon, label), bg=C["bg_alt"],
                           fg=C["text_sub"], anchor="w", padx=10, pady=8,
                           font=_f(10))
            lbl.pack(side="left", fill="x", expand=True)

            def enter(_e=None):
                if cur_cat["k"] != key:
                    row.configure(bg=C["accent_sf"])
                    lbl.configure(bg=C["accent_sf"], fg=C["text"])
            def leave(_e=None):
                if cur_cat["k"] != key:
                    row.configure(bg=C["bg_alt"])
                    lbl.configure(bg=C["bg_alt"], fg=C["text_sub"])
            for w in (row, lbl):
                w.bind("<Enter>", enter, add="+")
                w.bind("<Leave>", leave, add="+")
                w.bind("<Button-1>", lambda e, k=key: show_cat(k), add="+")
            nav_rows[key] = {"row": row, "lbl": lbl, "bar": bar}

        for k, ic, lb in CATS:
            mk_cat(k, ic, lb)

        # 每类的内容容器（提前建好，show_cat 时切换）
        for k, _ic, _lb in CATS:
            _build_cat(k)

        def section(title):
            """在一个分类页里加小标题（同一类里还能再分小节）。"""
            f = pages[cur_cat["k"]]
            lbl = tk.Label(f, text=title, bg=C["bg"], fg=C["accent"],
                           font=_f(11, "bold"))
            lbl.pack(anchor="w", pady=(14, 6))
            return lbl

        def _on_search(*_a):
            """按关键词切到对应分类。"""
            q = search_v.get().strip().lower()
            if not q:
                s_hint.configure(text="输入关键词快速跳转（如 并发 / 主题 / 术语）",
                                 fg=C["muted"])
                return
            kw_map = {
                "engine": ("引擎", "key", "api", "模型", "语言", "连接"),
                "perf":   ("并发", "性能", "线程", "缓存", "重试"),
                "gloss":  ("术语", "词汇", "领域"),
                "output": ("输出", "保存", "位置", "目录", "完成后"),
                "ui":     ("界面", "主题", "缩放", "字号", "字体", "夜间", "白天", "反色"),
                "preset": ("预设",),
                "about":  ("关于", "版本", "界面版本"),
            }
            for k, words in kw_map.items():
                if any(q in w for w in words):
                    show_cat(k)
                    name = dict((c[0], c[2]) for c in CATS).get(k, k)
                    s_hint.configure(text="已跳到「%s」" % name, fg=C["ok"])
                    return
            s_hint.configure(text="没找到「%s」" % q, fg=C["danger"])

        s_entry.bind("<KeyRelease>", _on_search)
        search_v.trace_add("write", lambda *a: _on_search())

        def row(label, hint=""):
            f = pages[cur_cat["k"]]
            fr = tk.Frame(f, bg=C["bg"])
            fr.pack(fill="x", pady=3)
            tk.Label(fr, text=label, bg=C["bg"], fg=C["text"],
                     font=_f(10), width=12, anchor="w").pack(side="left")
            box = tk.Frame(fr, bg=C["bg"])
            box.pack(side="left", fill="x", expand=True)
            if hint:
                tk.Label(box, text=hint, bg=C["bg"], fg=C["muted"],
                         font=_f(8), anchor="w").pack(anchor="w")
            return box

        # ---- 引擎 ----
        cur_cat["k"] = "engine"
        section("翻译引擎")
        eng_labels = [c[0] for c in A.ENGINE_CHOICES]
        cur_eng = str(cfg.get("engine") or "bing").lower()
        eng_v = tk.StringVar(value=A.ENGINE_LABEL.get(cur_eng, cur_eng))
        box = row("引擎")
        ttk.Combobox(box, textvariable=eng_v, values=eng_labels,
                     state="readonly", width=34).pack(anchor="w")

        key_v = tk.StringVar(value=str(cfg.get("api_key") or ""))
        box = row("API Key", "免费引擎（Bing/Google）可留空")
        ttk.Entry(box, textvariable=key_v, width=34).pack(anchor="w")

        # ---- 测试连接（省得填完 Key 才发现不通）----
        test_box = row("连接测试", "改完引擎/Key 建议先测一下")
        test_msg = tk.Label(test_box, text="", bg=C["bg"], fg=C["muted"],
                            font=_f(9), wraplength=380, justify="left",
                            anchor="w")
        test_btn = flat_btn(test_box, "测试连接", lambda: _do_test())

        def _do_test():
            """后台线程测引擎连通性，避免卡界面。"""
            lab = eng_v.get()
            key = next((k for l, k, _n, _g in A.ENGINE_CHOICES if l == lab), None)
            eng = key or str(cfg.get("engine") or "bing")
            api = key_v.get().strip()
            test_msg.configure(text="正在测试…", fg=C["warn"])
            test_btn.configure(state="disabled")

            def work():
                try:
                    ok, msg = A.test_engine_connection(eng, api, "", "")
                except Exception as exc:
                    ok, msg = False, str(exc)[:120]

                def show():
                    try:
                        test_msg.configure(
                            text=("✅ " if ok else "❌ ") + msg,
                            fg=(C["ok"] if ok else C["danger"]))
                        test_btn.configure(state="normal")
                    except Exception:
                        pass
                try:
                    root.after(0, show)
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()

        test_btn.pack(anchor="w", pady=(2, 0))
        test_msg.pack(anchor="w", pady=(4, 0))

        # ---- 性能 ----
        cur_cat["k"] = "perf"
        section("性能")
        # 并发数：滑块 + 可直接输入的数值框（两者联动）
        th_v = tk.IntVar(value=int(cfg.get("threads") or 20))
        th_txt = tk.StringVar(value=str(th_v.get()))
        box = row("并发数", "越高越快（可拖动或直接输入数字）；实测 20 比 10 快约 1.6 倍")
        sf = tk.Frame(box, bg=C["bg"])
        sf.pack(anchor="w")
        th_lbl = tk.Label(sf, text=str(th_v.get()), bg=C["bg"], fg=C["accent"],
                          font=_f(10, "bold"), width=3)
        # 直接输入的框
        th_e = ttk.Entry(sf, textvariable=th_txt, width=5)
        th_e.pack(side="left")

        def _from_entry(*_a):
            """输入框 → 滑块"""
            try:
                n = int(float(th_txt.get() or 0))
            except Exception:
                return
            n = max(1, min(30, n))
            th_v.set(n)
            sc.set(n)
            th_lbl.configure(text=str(n))
        th_txt.trace_add("write", _from_entry)

        def _from_slider(v):
            """滑块 → 输入框"""
            try:
                n = max(1, min(30, int(float(v))))
            except Exception:
                return
            th_lbl.configure(text=str(n))
            if th_txt.get() != str(n):
                th_txt.set(str(n))

        sc = ttk.Scale(sf, from_=1, to=30, orient="horizontal",
                       variable=th_v, length=180, command=_from_slider)
        sc.pack(side="left", padx=(8, 6))
        th_lbl.pack(side="left", padx=(0, 6))
        tk.Label(sf, text="（可输入）", bg=C["bg"], fg=C["muted"],
                 font=_f(8)).pack(side="left")

        # ---- 术语表 ----
        cur_cat["k"] = "gloss"
        section("术语表")
        gl_v = tk.BooleanVar(value=bool(cfg.get("glossary_enabled", True)))
        tk.Checkbutton(pages["gloss"], text="启用内置电子工程术语表（1100 条，免费引擎也生效）",
                       variable=gl_v, bg=C["bg"], fg=C["text"],
                       selectcolor=C["card"], activebackground=C["bg"],
                       font=_f(9)).pack(anchor="w", pady=2)

        dom_labels = ["全部领域", "电源与转换器", "模拟与信号链", "数字与接口",
                      "半导体器件", "封装与可靠性", "射频与无线"]
        dom_map = {"全部领域": "all", "电源与转换器": "power",
                   "模拟与信号链": "analog", "数字与接口": "digital",
                   "半导体器件": "device", "封装与可靠性": "package",
                   "射频与无线": "rf"}
        rev_dom = {v: k for k, v in dom_map.items()}
        dom_v = tk.StringVar(value=rev_dom.get(str(cfg.get("glossary_domain") or "all"),
                                               "全部领域"))
        box = row("启用领域", "只启用某类术语，减少误替换")
        ttk.Combobox(box, textvariable=dom_v, values=dom_labels,
                     state="readonly", width=20).pack(anchor="w")

        # ---- 输出 ----
        cur_cat["k"] = "output"
        section("输出")
        after_labels = ["每次询问我", "自动打开结果", "不打开"]
        after_map = {"每次询问我": "ask", "自动打开结果": "open", "不打开": "none"}
        rev_after = {v: k for k, v in after_map.items()}
        after_v = tk.StringVar(value=rev_after.get(str(cfg.get("after_translate") or "ask"),
                                                   "每次询问我"))
        box = row("完成后")
        ttk.Combobox(box, textvariable=after_v, values=after_labels,
                     state="readonly", width=20).pack(anchor="w")

        dir_v = tk.StringVar(value=str(cfg.get("output_dir") or ""))
        box = row("保存位置", "留空 = 程序目录下的 output")
        drow = tk.Frame(box, bg=C["bg"])
        drow.pack(anchor="w", fill="x")
        ttk.Entry(drow, textvariable=dir_v, width=34).pack(
            side="left", fill="x", expand=True)

        def browse_dir():
            """浏览选择保存位置。"""
            start = dir_v.get().strip()
            if not start or not Path(start).is_dir():
                start = str(A.resolve_output_dir(A.load_config()))
            d = filedialog.askdirectory(title="选择保存位置", initialdir=start)
            if d:
                dir_v.set(str(Path(d)))
        flat_btn(drow, "浏览…", browse_dir).pack(side="left", padx=(6, 0))

        def open_dir():
            """直接打开当前保存位置。"""
            p = dir_v.get().strip() or str(A.resolve_output_dir(A.load_config()))
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
                os.startfile(p)
            except Exception as exc:
                toast("打不开：%s" % exc, "err")
        flat_btn(drow, "打开", open_dir).pack(side="left", padx=(4, 0))

        tk.Label(box, text="当前：%s" % A.resolve_output_dir(cfg),
                 bg=C["bg"], fg=C["muted"], font=_f(8)).pack(anchor="w", pady=(2, 0))

        # ---- 界面 ----
        cur_cat["k"] = "ui"
        # 这一组「改完立刻生效」，不需要点保存 —— 之前用户改了下拉框却
        # 不知道要点保存，看着就是"改了没反应"。
        section("界面")
        tk.Label(pages["ui"], text="这一组改完立即生效，不用点保存",
                 bg=C["bg"], fg=C["accent"], font=_f(8)).pack(anchor="w")

        theme_labels = ["☀ 白天", "🌙 夜间", "跟随系统"]
        theme_map = {"☀ 白天": "light", "🌙 夜间": "dark", "跟随系统": "auto"}
        rev_theme = {v: k for k, v in theme_map.items()}
        theme_v = tk.StringVar(value=rev_theme.get(str(cfg.get("ui_theme") or "light"),
                                                   "☀ 白天"))
        box = row("主题", "选「跟随系统」会自动读取 Windows 的深色模式")
        theme_box = ttk.Combobox(box, textvariable=theme_v, values=theme_labels,
                                 state="readonly", width=20)
        theme_box.pack(anchor="w")

        def _apply_theme_now(_e=None):
            c = A.load_config()
            c["ui_theme"] = theme_map.get(theme_v.get(), "light")
            A.save_config(c)
            root.after(10, _rebuild)
        theme_box.bind("<<ComboboxSelected>>", _apply_theme_now)

        # ---- ⑨⑩ 界面缩放与字号 ----
        cur_cat["k"] = "ui"
        section("界面显示")
        scale_v = tk.DoubleVar(value=float(cfg.get("ui_scale") or 1.0))
        box = row("界面缩放", "小屏幕看不清时调大（1.0 = 原始大小）")
        sf = tk.Frame(box, bg=C["bg"])
        sf.pack(anchor="w")
        sc_lbl = tk.Label(sf, text="%.2fx" % scale_v.get(), bg=C["bg"],
                          fg=C["text"], font=_f(9), width=6)

        _scale_job = {"id": None}

        def _on_scale(v):
            """拖动时只更新数字；停手 400ms 后才真正重建（避免拖一次重建几十遍）。"""
            try:
                sc_lbl.configure(text="%.2fx" % float(v))
            except Exception:
                pass
            if _scale_job["id"]:
                try:
                    root.after_cancel(_scale_job["id"])
                except Exception:
                    pass
            _scale_job["id"] = root.after(400, _apply_scale_now)

        def _apply_scale_now():
            _scale_job["id"] = None
            try:
                val = round(max(0.8, min(1.6, float(scale_v.get()))), 2)
            except Exception:
                return
            c = A.load_config()
            if abs(float(c.get("ui_scale") or 1.0) - val) < 0.001:
                return
            c["ui_scale"] = val
            A.save_config(c)
            _rebuild()

        ttk.Scale(sf, from_=0.8, to=1.6, orient="horizontal", length=200,
                  variable=scale_v, command=_on_scale).pack(side="left")
        sc_lbl.pack(side="left", padx=4)

        font_v = tk.IntVar(value=int(cfg.get("ui_font_size") or 10))
        box = row("字体大小", "点数值，默认 10")
        font_box = ttk.Combobox(box, textvariable=font_v, width=8,
                                state="readonly",
                                values=[8, 9, 10, 11, 12, 13, 14])
        font_box.pack(anchor="w")

        def _apply_font_now(_e=None):
            c = A.load_config()
            try:
                c["ui_font_size"] = max(8, min(14, int(font_v.get())))
            except Exception:
                return
            A.save_config(c)
            root.after(10, _rebuild)
        font_box.bind("<<ComboboxSelected>>", _apply_font_now)

        # ---- ⑧ 自动反色 ----
        auto_inv = tk.BooleanVar(value=bool(cfg.get("auto_invert_dark", True)))
        tk.Checkbutton(pages["ui"], text="夜间模式下自动反色预览 PDF（看白底文档不刺眼）",
                       variable=auto_inv, bg=C["bg"], fg=C["text"],
                       selectcolor=C["card"], activebackground=C["bg"],
                       font=_f(9)).pack(anchor="w", pady=(10, 0))

        # ---- 翻译预设 ----
        cur_cat["k"] = "preset"
        section("翻译预设")
        tk.Label(pages["preset"], text="把当前整套设置存成预设，以后一键切换",
                 bg=C["bg"], fg=C["muted"], font=_f(8)).pack(anchor="w")
        preset_names = list((cfg.get("presets") or {}).keys())
        pv = tk.StringVar(value=str(cfg.get("preset_active") or "（不使用）"))
        pbox = ttk.Combobox(pages["preset"], textvariable=pv, state="readonly", width=22,
                            values=["（不使用）"] + preset_names)
        pbox.pack(anchor="w", pady=(4, 0))

        def apply_preset(_e=None):
            name = pv.get()
            c = A.load_config()
            if name == "（不使用）":
                c["preset_active"] = ""
                A.save_config(c)
                return
            data = (c.get("presets") or {}).get(name)
            if not data:
                toast("这个预设是空的", "warn")
                return
            c.update(data)
            c["preset_active"] = name
            A.save_config(c)
            toast("已套用预设「%s」" % name, "ok")
            # 直接重建，不再弹窗问 —— 用户选预设就是要它生效
            root.after(80, _rebuild)
        pbox.bind("<<ComboboxSelected>>", apply_preset)

        def save_preset():
            nm = simpledialog.askstring("保存为预设", "预设名称：", parent=root)
            if not nm or not nm.strip():
                return
            nm = nm.strip()
            c = A.load_config()
            snap = {k: v for k, v in c.items()
                    if k not in ("presets", "preset_active")}
            c.setdefault("presets", {})[nm] = snap
            c["preset_active"] = nm
            A.save_config(c)
            toast("预设「%s」已保存" % nm, "ok")
            root.after(80, _rebuild)

        pbar = tk.Frame(pages["preset"], bg=C["bg"])
        pbar.pack(anchor="w", pady=(4, 0))
        flat_btn(pbar, "另存为预设…", save_preset).pack(side="left")

        # ---- 保存 ----
        def do_save():
            c = A.load_config()
            # 引擎
            lab = eng_v.get()
            key = next((k for l, k, _n, _g in A.ENGINE_CHOICES if l == lab), None)
            if key:
                c["engine"] = key
            c["api_key"] = key_v.get().strip()
            c["threads"] = max(1, min(30, int(th_v.get())))
            c["glossary_enabled"] = bool(gl_v.get())
            c["glossary_domain"] = dom_map.get(dom_v.get(), "all")
            c["after_translate"] = after_map.get(after_v.get(), "ask")
            c["output_dir"] = dir_v.get().strip()
            new_theme = theme_map.get(theme_v.get(), "light")
            c["ui_theme"] = new_theme
            c["ui_scale"] = round(float(scale_v.get()), 2)
            c["ui_font_size"] = int(font_v.get())
            c["auto_invert_dark"] = bool(auto_inv.get())
            A.save_config(c)
            toast("设置已保存", "ok")
            # 只要"外观相关"的设置变了就直接重建 —— 不再弹窗问，
            # 用户点了保存就是想让设置生效（之前弹窗+mode 不更新导致"切换无效"）
            visual_keys = ("ui_theme", "ui_scale", "ui_font_size", "auto_invert_dark")
            changed = any(c.get(k) != cfg.get(k) for k in visual_keys)
            if changed:
                root.after(80, _rebuild)
            else:
                # 非外观设置：刷新设置页自身，让控件显示新值
                root.after(80, lambda: (_invalidate("settings"),
                                        show("settings")))

        # 关于页：诊断包入口（⑫ 反馈问题用）
        cur_cat["k"] = "about"
        section("问题反馈")

        def make_diagnostic():
            """把环境+配置+日志打包成一个 txt，方便发给开发者。"""
            log_w = widgets.get("translate.log")
            log_text = ""
            try:
                if log_w is not None:
                    log_text = log_w.get("1.0", "end")
            except Exception:
                pass
            try:
                p = A.build_diagnostic(log_text)
            except Exception as exc:
                messagebox.showerror("生成失败", str(exc))
                return
            if not p:
                messagebox.showerror("生成失败", "无法写入诊断文件")
                return
            if messagebox.askyesno(
                    "诊断报告已生成",
                    "已生成：%s\n\n"
                    "内容包含运行环境、脱敏后的配置、最近日志。\n"
                    "API Key 等敏感信息已自动移除。\n\n"
                    "现在打开所在文件夹吗？" % Path(p).name):
                try:
                    A.open_file_with(Path(p).parent, A.load_config(), mode="folder")
                except Exception:
                    pass

        flat_btn(pages["about"], "生成诊断报告", make_diagnostic).pack(
            anchor="w", pady=(6, 0))
        tk.Label(pages["about"],
                 text="（软件出问题时生成一个，连同现象一起反馈；不含密钥）",
                 bg=C["bg"], fg=C["muted"], font=_f(8)).pack(anchor="w")

        # 系统深色模式检测（供「跟随系统」用）
        def detect_system_dark() -> bool:
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                    v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                    return int(v) == 0
            except Exception:
                return False

        if str(cfg.get("ui_theme")) == "auto":
            tk.Label(pages["about"],
                     text="当前系统为%s模式" % ("深色" if detect_system_dark() else "浅色"),
                     bg=C["bg"], fg=C["muted"], font=_f(8)).pack(anchor="w")

        # ---- 底部固定操作条（不随分类滚动）----
        bar = tk.Frame(parent, bg=C["bg_alt"])
        bar.pack(side="bottom", fill="x", padx=20, pady=(0, 12))
        flat_btn(bar, "💾  保存设置", do_save, "primary").pack(
            side="left", padx=(10, 0), pady=8)
        flat_btn(bar, "⚙  高级设置窗口",
                 lambda: A_open_settings()).pack(side="left", padx=8, pady=8)
        tk.Label(bar, text="「界面」里的项改完立即生效，其余点保存",
                 bg=C["bg_alt"], fg=C["muted"], font=_f(8)).pack(
            side="left", padx=10)

        # 默认显示第一类
        show_cat("engine")

    # ================= 底部：状态 + 主题（可重建）=================
    def toggle_theme():
        """切换主题 —— 真正重建界面，立即生效，不重启进程。"""
        c = A.load_config()
        cur_t = _resolve_theme(c)
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

        bar = tk.Frame(dlg, bg=C["bg"])
        bar.pack(side="bottom", fill="x", padx=18, pady=(0, 14))
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

    def _fmt_sec(s: float) -> str:
        """把秒数格式化成「1 分 23 秒」。"""
        try:
            s = int(max(0, s))
        except Exception:
            return "-"
        if s < 60:
            return "%d 秒" % s
        m, sec = divmod(s, 60)
        if m < 60:
            return "%d 分 %02d 秒" % (m, sec)
        h, m2 = divmod(m, 60)
        return "%d 时 %02d 分" % (h, m2)

    def _update_queue():
        """② 刷新队列面板（Listbox 版，支持 ⑥ 拖拽排序）。"""
        w = widgets.get("translate.queue")
        if w is None:
            return
        try:
            cur_p = state.get("cur_file") or ""
            todo = state.get("batch") or []
            w.delete(0, "end")
            if not cur_p and not todo:
                w.insert("end", "（单个文件翻译时不显示队列）")
                return
            if cur_p:
                w.insert("end", "▶ 进行中：%s" % Path(cur_p).name)
            for i, f in enumerate(todo, 1):
                w.insert("end", "   %d. %s" % (i, Path(f).name))
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

    def do_cancel():
        """终止正在进行的翻译。"""
        if not A.is_translating():
            toast("当前没有在翻译", "warn")
            return
        if not messagebox.askyesno("终止翻译", "确定要终止当前的翻译吗？\n\n"
                                              "已翻译的部分不会保留。"):
            return
        if A.cancel_translation():
            toast("已终止翻译", "ok")
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
        dlg.geometry("560x480")
        dlg.minsize(500, 420)
        try:
            dlg.configure(bg=C["bg"])
        except Exception:
            pass
        dlg.transient(root)

        body = tk.Frame(dlg, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(body, text="要翻译的文件", bg=C["bg"], fg=C["muted"],
                 font=_f(9), anchor="w").pack(anchor="w")
        tk.Label(body, text=Path(pdf_path).name, bg=C["bg"], fg=C["accent"],
                 font=_f(11, "bold"), anchor="w").pack(anchor="w", pady=(2, 4))

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
        opt.pack(fill="x", pady=(10, 0))
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
        bar = tk.Frame(dlg, bg=C["bg"])
        bar.pack(side="bottom", fill="x", padx=18, pady=(0, 14))
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
                                         % (_fmt_sec(used),
                                            A.fmt_duration(eta)))
                        elif t_start > 0:
                            el = widgets.get("translate.eta")
                            if el is not None:
                                el.configure(text="已用 %s · 计算剩余时间…"
                                                  % _fmt_sec(time.time() - t_start))
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
                        # ⑤ 断点续译：记下这份 PDF 已经翻完的页
                        try:
                            _p = state.get("cur_file") or ""
                            _pages = timing.get("pages") or []
                            _total = int(timing.get("total_pages") or 0)
                            if _p and _pages:
                                A.save_progress(_p, _pages, _total, out_file)
                            elif _p:
                                # 没有页信息就当作全部完成
                                A.clear_progress(_p)
                        except Exception:
                            pass
                        if timing:
                            A._UI_STATE["stages"] = timing.get("stages") or []
                            A._UI_STATE["total"] = timing.get("total") or 0
                            # 用最终统计数据刷新翻译主界面的两个面板
                            _fill_translate_panels(timing)
                        # 翻译结束：清掉实时阶段状态，避免下次翻译残留
                        try:
                            stage_done.clear()
                            stage_live["name"] = None
                            stage_live["start"] = 0.0
                        except Exception:
                            pass
                        # 批量：继续下一个
                        nxt = (state.get("batch") or [])
                        if nxt:
                            state["batch"] = nxt[1:]
                            _update_queue()          # ④ 落盘新队列
                            root.after(400, lambda p=nxt[0]: start(p))
                        else:
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
                        w = widgets.get("translate.log")
                        if w is not None and err:
                            w.insert("end", "❌ " + str(err) + "\n"); w.see("end")
        except queue.Empty:
            pass
        if _my_gen == A._BUILD_GEN["n"]:
            root.after(120, pump)

    # 实时阶段记录：已完成 + 当前进行中
    stage_done: list = []

    def _live_tick():
        """翻译进行中【实时】刷新翻译主界面的「步骤耗时」面板。

        之前只有当前页是「步骤耗时」时才刷新 —— 而那个页已经删了。
        现在：只要在翻译，每 400ms 就把 stage_done（已完成阶段）
        + 当前进行中的阶段一起刷到翻译页上，用户随时能看到进展。
        """
        if _my_gen != A._BUILD_GEN["n"]:
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
                              or _gloss_total_terms())
        except Exception:
            pass

    def _gloss_total_terms() -> int:
        """词库总条数（缓存，避免每次都解析 CSV）。"""
        if _gloss_total_terms._n is None:
            try:
                import glossary_hook as _gh
                _gloss_total_terms._n = int(_gh.load_glossary() or 0)
            except Exception:
                _gloss_total_terms._n = 0
        return _gloss_total_terms._n
    _gloss_total_terms._n = None

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
            for i in sorted((int(x[1:]) for x in sel), reverse=True):
                if 0 <= i < len(rows):
                    rows.pop(i)
            if save():
                refresh()
                toast("已删除 %d 条" % len(sel), "ok")

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

    def _open_learner():
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
        tv.bind("<TreeviewSelect>", pick)

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
    def _enable_global_drop():
        """用 Windows 的 WM_DROPFILES 让整个窗口可接收拖放。"""
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
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                               wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            WM_DROPFILES = 0x0233
            GWL_WNDPROC = -4
            frame = root.frame()
            hwnd = int(frame, 16) if str(frame).startswith("0x") else int(frame)
            hwnd = wintypes.HWND(hwnd)
            shell32.DragAcceptFiles(hwnd, True)
            old_proc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)

            def handler(h, msg, wparam, lparam):
                try:
                    if msg == WM_DROPFILES:
                        n = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        paths = []
                        for i in range(n):
                            buf = ctypes.create_unicode_buffer(1024)
                            shell32.DragQueryFileW(wparam, i, buf, 1024)
                            paths.append(buf.value)
                        shell32.DragFinish(wparam)
                        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
                        if pdfs:
                            root.after(0, lambda: _on_drop_files(pdfs))
                        return 0
                except Exception:
                    pass
                if old_proc:
                    return user32.CallWindowProcW(
                        ctypes.c_void_p(old_proc), h, msg, wparam, lparam)
                return user32.DefWindowProcW(h, msg, wparam, lparam)

            cb = WNDPROC(handler)
            user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC,
                                     ctypes.cast(cb, ctypes.c_void_p))
            root._drop_cb = cb
            root._drop_hwnd = hwnd
            root._drop_old = old_proc
            return True
        except Exception:
            return False

    def _on_drop_files(paths):
        """拖入文件的处理：提示并开始翻译。"""
        try:
            show("translate")
            if len(paths) == 1:
                start(paths[0])
            else:
                if messagebox.askyesno("批量翻译",
                                       "拖入 %d 个文件，依次翻译？" % len(paths)):
                    state["batch"] = list(paths[1:])
                    start(paths[0])
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

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

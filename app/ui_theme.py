# -*- coding: utf-8 -*-
"""主题与样式：把配色应用到 ttk / 原生 tk 控件。

这两块原本嵌在 app_sidebar.main() 里，合计 320+ 行，
是纯粹的「样式配置」，不涉及业务逻辑，最适合单独成文件。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def apply_style(theme: dict, dpi: float = 96.0,
                font_family: str = "Microsoft YaHei UI",
                font_size: int = 10) -> None:
    """把主题应用到 ttk 样式（可重复调用 —— 热切换靠它）。

    font_family / font_size 必须传进来：
    它们是 app_sidebar 里的 FONT / _FS，提取后不能再靠闭包拿。
    （之前漏传导致整个函数被 try 静默吞掉，夜间模式表格一直是白底。）
    """
    FONT = font_family
    _FS = font_size
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


def theme_native_widgets(w, C: dict, themed_ids: set, force: bool = False) -> None:
    """把原生 tk 控件里残留的"系统色"改成主题色。

    为什么需要单独走一遍：
      · option_add 只影响之后新建的控件
      · Listbox / Text 的 highlightColor 默认是 SystemWindowFrame，
        聚焦时会画一圈系统色边框 —— 夜间看着就是白边
      · 原生 Checkbutton 的小方块默认是系统白

    force=True 用于主题切换（颜色变了要重刷）；
    普通切页走缓存，已处理过的控件直接跳过。
    """
    theme = C
    try:
        kids = w.winfo_children()
    except Exception:
        return
    for c in kids:
        try:
            cls = c.winfo_class()
        except Exception:
            cls = ""
        key = id(c)
        if not force and key in themed_ids:
            # 已处理过：只递归子控件（子控件可能是新加的）
            theme_native_widgets(c, C, themed_ids)
            continue
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
        else:
            themed_ids.add(key)
        theme_native_widgets(c, C, themed_ids)

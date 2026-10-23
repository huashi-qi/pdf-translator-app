# -*- coding: utf-8 -*-
"""界面基础控件（依赖主题与字体，但不依赖页面状态）。

这些函数原本嵌在 app_sidebar.main() 里，只用到：
  · C  —— 主题色字典
  · _f —— 字体工厂函数 (size, *styles) -> (family, size, *styles)
  · _W —— ui_widgets 模块（圆角按钮），可能为 None

把这三个作为参数传进来，就不必再依赖闭包，
页面代码也能单独复用这些控件。
"""
from __future__ import annotations

import tkinter as tk


def make_header(parent, title, subtitle, C, _f):
    """页面顶部标题 + 副标题。"""
    tk.Label(parent, text=title, bg=C["bg"], fg=C["text"],
             font=_f(14, "bold"), anchor="w").pack(
        anchor="w", padx=20, pady=(18, 0))
    if subtitle:
        tk.Label(parent, text=subtitle, bg=C["bg"], fg=C["muted"],
                 font=_f(9), anchor="w").pack(anchor="w", padx=20, pady=(3, 0))


def make_card(parent, C, _f, _W, radius=12):
    """圆角卡片容器（比直角 Frame 柔和）。"""
    if _W is not None:
        try:
            return _W.Card(parent, C, radius=radius)
        except Exception:
            pass
    return tk.Frame(parent, bg=C["card"], highlightbackground=C["border"],
                    highlightthickness=1)


def make_flat_btn(parent, text, cmd, C, _f, _W, kind="normal"):
    """建一个按钮。

    优先用 Canvas 自绘的圆角按钮（带平滑动画）；
    万一 ui_widgets 缺失，退回普通 Label 按钮，保证功能不断。
    """
    if _W is not None:
        try:
            return _W.make_round_button(parent, text, cmd, kind=kind, C=C,
                                        radius=9, padx=16, pady=7,
                                        font=_f(10))
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


def make_loading(parent, text, C, _f):
    """加载态提示（避免「卡住了」的错觉）。"""
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


def make_empty_state(parent, icon, title, C, _f, _W, hint="",
                     action=None, action_text=""):
    """空状态：图标 + 说明 + 可选按钮。

    比光秃秃一行「暂无数据」友好得多 —— 用户一眼知道该干什么。
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
        make_flat_btn(holder, action_text, action, C, _f, _W, "primary")
    return box

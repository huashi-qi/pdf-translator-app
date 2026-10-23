# -*- coding: utf-8 -*-
"""侧边栏版界面（独立新版，与经典版并存）。

布局参考 Zotero / VS Code：
    ┌────────┬──────────────────────────────┐
    │ 导航栏  │  内容区                       │
    │ 翻译    │  （随左侧选中项切换）          │
    │ 历史    │                              │
    │ 耗时    │                              │
    │ 工具    │                              │
    │ 设置    │                              │
    └────────┴──────────────────────────────┘

与经典版的区别只在布局，后端完全共用：
    worker / 术语表 / 历史记录 / 设置 / PDF 工具

配色跟随 THEMES（白天/夜间），主题热切换同样适用。
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def build_sidebar_ui(root, tk, ttk, C, api):
    """构建侧边栏界面。

    参数
    ----
    root : Tk 根窗口
    tk, ttk : tkinter 模块
    C    : 当前主题配色
    api  : core.py / 调用方提供的回调字典，键包括
           log, set_status, pick_pdf, to_word, pdf_tools, open_pdf,
           inspect_tools, open_settings, refresh_history, load_history,
           history_actions(refresh) 等
    """
    NAV_W = 176
    FONT = "Microsoft YaHei UI"

    # ================= 骨架 =================
    shell = tk.Frame(root, bg=C["bg"])
    shell.pack(fill="both", expand=True)

    nav = tk.Frame(shell, bg=C["sidebar"], width=NAV_W)
    nav.pack(side="left", fill="y")
    nav.pack_propagate(False)

    body = tk.Frame(shell, bg=C["bg"])
    body.pack(side="left", fill="both", expand=True)

    # ---- 侧边栏标题 ----
    brand = tk.Frame(nav, bg=C["sidebar"])
    brand.pack(fill="x", pady=(14, 10))
    tk.Label(brand, text="PDF 翻译助手", bg=C["sidebar"], fg=C["text"],
             font=(FONT, 11, "bold"), anchor="w").pack(fill="x", padx=16)
    tk.Label(brand, text="拖入 PDF 即可翻译", bg=C["sidebar"], fg=C["muted"],
             font=(FONT, 8), anchor="w").pack(fill="x", padx=16, pady=(2, 0))

    tk.Frame(nav, bg=C["border"], height=1).pack(fill="x", padx=12, pady=(0, 8))

    # ================= 导航项 =================
    items: dict = {}
    cur = {"key": None}

    def _mk_item(key: str, icon: str, label: str, builder):
        row = tk.Frame(nav, bg=C["sidebar"], cursor="hand2")
        row.pack(fill="x", padx=8, pady=1)
        bar = tk.Frame(row, bg=C["sidebar"], width=3)
        bar.pack(side="left", fill="y")
        lbl = tk.Label(row, text="%s   %s" % (icon, label), bg=C["sidebar"],
                       fg=C["text"], anchor="w", padx=9, pady=8, font=(FONT, 10))
        lbl.pack(side="left", fill="x", expand=True)

        def enter(_e=None):
            if cur["key"] != key:
                row.configure(bg=C["bg_alt"]); lbl.configure(bg=C["bg_alt"])

        def leave(_e=None):
            if cur["key"] != key:
                row.configure(bg=C["sidebar"]); lbl.configure(bg=C["sidebar"])

        def click(_e=None):
            show(key)

        for w in (row, lbl):
            w.bind("<Enter>", enter, add="+")
            w.bind("<Leave>", leave, add="+")
            w.bind("<Button-1>", click, add="+")
        items[key] = {"row": row, "lbl": lbl, "bar": bar, "build": builder}

    def show(key: str):
        cur["key"] = key
        for k, it in items.items():
            on = (k == key)
            bg = C["sidebar_on"] if on else C["sidebar"]
            try:
                it["row"].configure(bg=bg)
                it["lbl"].configure(bg=bg, fg=C["accent"] if on else C["text"],
                                    font=(FONT, 10, "bold" if on else "normal"))
                it["bar"].configure(bg=C["accent"] if on else C["sidebar"])
            except Exception:
                pass
        for ch in list(body.winfo_children()):
            try:
                ch.destroy()
            except Exception:
                pass
        try:
            items[key]["build"](body)
        except Exception as exc:
            tk.Label(body, text="页面加载失败：%s" % exc, bg=C["bg"],
                     fg=C["danger"], font=(FONT, 10)).pack(padx=20, pady=20)

    # ================= 内容页构建 =================

    def _page_header(parent, title: str, subtitle: str = ""):
        h = tk.Frame(parent, bg=C["bg"])
        h.pack(fill="x", padx=18, pady=(16, 10))
        tk.Label(h, text=title, bg=C["bg"], fg=C["text"],
                 font=(FONT, 14, "bold"), anchor="w").pack(anchor="w")
        if subtitle:
            tk.Label(h, text=subtitle, bg=C["bg"], fg=C["muted"],
                     font=(FONT, 9), anchor="w").pack(anchor="w", pady=(3, 0))
        return h

    def _btn(parent, text, cmd, kind="normal"):
        """按角色上色的按钮（尺寸恒定，悬停只变颜色）。"""
        pal = {
            "primary": (C["accent"], "#ffffff", C["accent_hv"]),
            "normal":  (C["head_bg"], C["text"], C["accent_sf"]),
            "danger":  (C["head_bg"], C["danger"], C["accent_sf"]),
        }.get(kind, (C["head_bg"], C["text"], C["accent_sf"]))
        bg, fg, hv = pal
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, 10),
                     padx=14, pady=7, cursor="hand2")
        b.bind("<Enter>", lambda e: b.configure(bg=hv), add="+")
        b.bind("<Leave>", lambda e: b.configure(bg=bg), add="+")
        b.bind("<Button-1>", lambda e: cmd(), add="+")
        return b

    # ---- 翻译页 ----
    def page_translate(parent):
        _page_header(parent, "翻译", "选择 PDF 后翻译；可多选批量处理")

        card = tk.Frame(parent, bg=C["card"], highlightbackground=C["border"],
                        highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        drop = tk.Frame(card, bg=C["bg_alt"], highlightbackground=C["border"],
                        highlightthickness=1)
        drop.pack(fill="x", padx=14, pady=(14, 10))
        dl = tk.Label(drop, text="⬇   把 PDF 拖到这里，或点下面的按钮选择",
                      bg=C["bg_alt"], fg=C["accent"], font=(FONT, 10),
                      pady=18, cursor="hand2")
        dl.pack(fill="x")
        dl.bind("<Button-1>", lambda e: api["pick_pdf"]())
        for w in (drop, dl):
            w.bind("<Button-1>", lambda e: api["pick_pdf"]())

        row = tk.Frame(card, bg=C["card"])
        row.pack(fill="x", padx=14, pady=(0, 12))
        _btn(row, "选择 PDF", api["pick_pdf"], "primary").pack(side="left")
        api["register_pick_button"](None)  # 侧边栏版按钮自带样式，无需额外绑定

        # 当前设置概览
        info = tk.Frame(card, bg=C["card"])
        info.pack(fill="x", padx=14, pady=(4, 12))
        tk.Label(info, text="当前设置", bg=C["card"], fg=C["muted"],
                 font=(FONT, 9), anchor="w").pack(anchor="w")
        lbl = tk.Label(info, text="", bg=C["card"], fg=C["text_sub"],
                       font=(FONT, 9), anchor="w", justify="left")
        lbl.pack(anchor="w", pady=(2, 0))
        api["bind_info_label"](lbl)

        # 日志
        tk.Label(card, text="运行日志", bg=C["card"], fg=C["muted"],
                 font=(FONT, 9), anchor="w").pack(anchor="w", padx=14)
        wrap = tk.Frame(card, bg=C["card"])
        wrap.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        txt = tk.Text(wrap, height=10, wrap="word", bg=C["log_bg"],
                      fg=C["log_fg"], insertbackground=C["log_fg"],
                      font=("Consolas", 9), relief="flat", padx=8, pady=6)
        sb = ttk.Scrollbar(wrap, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); txt.pack(side="left", fill="both", expand=True)
        api["bind_log_widget"](txt)

    # ---- 历史页 ----
    def page_history(parent):
        _page_header(parent, "历史记录", "双击打开；右键有更多操作")
        holder = tk.Frame(parent, bg=C["bg"])
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        api["build_history_into"](holder)

    # ---- 耗时页 ----
    def page_steps(parent):
        _page_header(parent, "步骤耗时", "看每个阶段花了多久，据此调并发")
        holder = tk.Frame(parent, bg=C["bg"])
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        api["build_steps_into"](holder)

    # ---- 工具页 ----
    def page_tools(parent):
        _page_header(parent, "工具", "PDF 处理与检查，都不改动原文件")
        grid = tk.Frame(parent, bg=C["bg"])
        grid.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        tools = [
            ("📄", "打开 PDF", "像阅读器一样打开、翻页、加标注", api["open_pdf"]),
            ("🔧", "PDF 工具", "合并 / 拆分 / 旋转 / 压缩 / 加页码水印", api["pdf_tools"]),
            ("📝", "PDF 转 Word", "不翻译，直接转成 Word", api["to_word"]),
            ("🔍", "检查工具", "术语表自检 / 扫描检测 / 段落对照", api["inspect_tools"]),
        ]
        for icon, title, desc, cmd in tools:
            card = tk.Frame(grid, bg=C["card"], highlightbackground=C["border"],
                            highlightthickness=1, cursor="hand2")
            card.pack(fill="x", pady=5)
            inner = tk.Frame(card, bg=C["card"])
            inner.pack(fill="x", padx=14, pady=12)
            tk.Label(inner, text=icon, bg=C["card"], fg=C["accent"],
                     font=(FONT, 16)).pack(side="left", padx=(0, 12))
            tx = tk.Frame(inner, bg=C["card"])
            tx.pack(side="left", fill="x", expand=True)
            tk.Label(tx, text=title, bg=C["card"], fg=C["text"],
                     font=(FONT, 11, "bold"), anchor="w").pack(anchor="w")
            tk.Label(tx, text=desc, bg=C["card"], fg=C["muted"],
                     font=(FONT, 9), anchor="w").pack(anchor="w")
            tk.Label(inner, text="›", bg=C["card"], fg=C["muted"],
                     font=(FONT, 16)).pack(side="right")

            def _bind_all(w, c=cmd):
                w.bind("<Button-1>", lambda e: c(), add="+")
                for ch in w.winfo_children():
                    _bind_all(ch, c)
            _bind_all(card)

    # ---- 设置页 ----
    def page_settings(parent):
        _page_header(parent, "设置", "引擎、术语表、输出、高级选项")
        holder = tk.Frame(parent, bg=C["bg"])
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        if api.get("build_settings_into"):
            api["build_settings_into"](holder)
        else:
            _btn(holder, "打开设置窗口", api["open_settings"], "primary").pack(anchor="w")

    # ================= 底部：状态 + 主题 =================
    tk.Frame(nav, bg=C["border"], height=1).pack(side="bottom", fill="x", padx=12, pady=(8, 0))
    foot = tk.Frame(nav, bg=C["sidebar"])
    foot.pack(side="bottom", fill="x", pady=8)

    st = tk.Label(foot, text="就绪", bg=C["sidebar"], fg=C["ok"],
                  font=(FONT, 9), anchor="w", wraplength=NAV_W - 24, justify="left")
    st.pack(fill="x", padx=14)
    api["bind_status_label"](st)

    def _toggle():
        api["toggle_theme"]()

    tb = tk.Label(foot, text=("☀  白天模式" if api["theme_mode"]() == "dark"
                              else "🌙  夜间模式"),
                  bg=C["sidebar"], fg=C["accent"], font=(FONT, 9),
                  anchor="w", padx=14, pady=6, cursor="hand2")
    tb.pack(fill="x", pady=(6, 0))
    tb.bind("<Enter>", lambda e: tb.configure(bg=C["sidebar_on"]), add="+")
    tb.bind("<Leave>", lambda e: tb.configure(bg=C["sidebar"]), add="+")
    tb.bind("<Button-1>", lambda e: _toggle(), add="+")

    # ================= 组装导航 =================
    _mk_item("translate", "🌐", "翻译", page_translate)
    _mk_item("history", "🕘", "历史记录", page_history)
    _mk_item("steps", "⏱", "步骤耗时", page_steps)
    _mk_item("tools", "🧰", "工具", page_tools)
    _mk_item("settings", "⚙", "设置", page_settings)

    show("translate")
    return {"shell": shell, "nav": nav, "body": body, "show": show, "items": items}

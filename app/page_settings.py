# -*- coding: utf-8 -*-
"""设置页（引擎/性能/术语表/输出/界面/预设/关于）。

原本是 app_sidebar.main() 里的一个 583 行嵌套函数，
靠闭包访问 C / root / widgets 等 11 个外部名字。
提取时把它们全部改成参数传进来；函数体内部的调用写法不变，
所以不需要改几百行代码，也就不会漏改。
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

def page_settings(parent, C, root, widgets, _f, flat_btn, header,
                  toast, show, _invalidate, _rebuild,
                  A_open_settings, messagebox, ttk, tk,
                  APP_DIR, A):
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
    # 记住当前分类：保存后会重建页面，不记住就跳回初始页了。
    _saved_cat = str(A._UI_STATE.get("settings_cat") or "engine")
    if _saved_cat not in ("engine", "perf", "gloss", "output",
                          "ui", "preset", "about"):
        _saved_cat = "engine"
    cur_cat = {"k": _saved_cat}
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
        # 记住当前分类：保存设置后页面会重建，不记住就会跳回初始页
        try:
            A._UI_STATE["settings_cat"] = key
        except Exception:
            pass
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

    # 直接用模块级的 make_diagnostic（见文件末尾）
    def make_diagnostic():
        return build_diagnostic(widgets, messagebox, A)

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

    # 显示上次停留的分类（首次进入才是 engine）。
    # 之前这里硬编码 show_cat("engine")，
    # 导致每次保存后重建页面都跳回第一类 —— 用户改了半天的分类被重置。
    show_cat(_saved_cat)

# ---------------------------------------------------------------
# 诊断报告：模块级函数，主程序和设置页都能用
# 之前它是 page_settings 的内嵌函数，主程序调 make_diagnostic()
# 会抛 NameError，又被 except 吞掉 —— 表现为「点了没反应」。
# ---------------------------------------------------------------
def build_diagnostic(widgets, messagebox, A):
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
            "已生成：%s" + chr(10) * 2 +
            "内容包含运行环境、脱敏后的配置、最近日志。" + chr(10) +
            "API Key 等敏感信息已自动移除。" + chr(10) * 2 +
            "现在打开所在文件夹吗？" % Path(p).name):
        try:
            A.open_file_with(Path(p).parent, A.load_config(), mode="folder")
        except Exception:
            pass

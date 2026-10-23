# -*- coding: utf-8 -*-
"""圆角按钮 + 平滑动效（Tk 原生控件做不到，用 Canvas 自绘）。

为什么需要这个
--------------
Tk 的按钮只有直角矩形，ttk 也没有圆角选项，更没有过渡动画。
这里用 Canvas 画一个圆角矩形，自己处理悬停/按下的颜色渐变。

特点
----
· 圆角（可调半径）
· 悬停时颜色【平滑过渡】（约 120ms，多帧插值），不是生硬跳变
· 按下有下沉/加深反馈
· 悬停时轻微上浮 1px，让界面"活"起来
· 完全跟随主题配色
"""
from __future__ import annotations

import tkinter as tk


def _hex_to_rgb(h: str) -> tuple:
    h = (h or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (0, 0, 0)


def _rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def lerp_color(c1: str, c2: str, t: float) -> str:
    """在两个颜色之间插值，t 从 0 到 1。"""
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def _round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形，返回 item id。

    用 polygon + smooth 模拟圆角：在四个角各打两个点，
    smooth=True 会让拐角变成平滑曲线。
    """
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=12, **kw)


class RoundButton(tk.Canvas):
    """圆角按钮。

    用法：
        b = RoundButton(parent, text="保存", command=fn,
                        bg=C["accent"], fg="#ffffff", hover=C["accent_hv"])
        b.pack(side="left")
    """

    def __init__(self, parent, text: str = "", command=None,
                 bg: str = "#2f6fd0", fg: str = "#ffffff",
                 hover: str | None = None, active: str | None = None,
                 radius: int = 10, padx: int = 16, pady: int = 8,
                 font=None, width: int | None = None, **kw):
        self._bg = bg
        self._fg = fg
        self._hover = hover or lerp_color(bg, "#ffffff", 0.18)
        self._active = active or lerp_color(bg, "#000000", 0.15)
        self._command = command
        self._radius = radius
        self._enabled = True

        # 先算尺寸（用临时 font 量文字宽度）
        f = font or ("Microsoft YaHei UI", 10)
        try:
            import tkinter.font as _tkfont
            tmp = _tkfont.Font(font=f)
        except Exception:
            tmp = None
        if tmp is not None:
            tw = tmp.measure(text)
            th = tmp.metrics("linespace")
        else:
            tw = len(text) * 9
            th = 16

        w = width or (tw + padx * 2)
        h = th + pady * 2

        # 外层留 2px 给「上浮」动画用
        pbg = bg
        try:
            pbg = parent.cget("bg")
        except Exception:
            pass
        super().__init__(parent, width=w, height=h + 2,
                         highlightthickness=0, bd=0, bg=pbg, **kw)
        # 注意：绝对不能用 self._w 存宽度！
        # tkinter 内部用 self._w 保存【控件路径名】（如 ".!canvas"），
        # 覆盖它会让后续所有 Tcl 调用找不到控件，报 invalid command name。
        self._btn_w, self._btn_h = w, h
        self._pad = parent
        self._text = text
        self._font = f
        self._cur_color = bg
        self._anim_job = None
        self._offset = 0
        # 注意：_draw 必须在 super().__init__ 之后调用，
        # 否则 Canvas 还没建好，delete("all") 会报 invalid command name。
        self._draw(self._bg, 0)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")

    # ---------- 绘制 ----------
    def _draw(self, color: str, y_off: int = 0):
        self.delete("all")
        y1 = 2 + y_off
        y2 = y1 + self._btn_h
        _round_rect(self, 1, y1, self._btn_w - 1, y2, self._radius,
                    fill=color, outline="")
        self.create_text(self._btn_w / 2, (y1 + y2) / 2, text=self._text,
                         fill=self._fg, font=self._font)

    def set_text(self, text: str):
        self._text = text
        self._draw(self._cur_color, self._offset)

    def set_colors(self, bg: str, fg: str, hover: str | None = None):
        """主题切换时更新配色。"""
        self._bg, self._fg = bg, fg
        self._hover = hover or lerp_color(bg, "#ffffff", 0.18)
        self._active = lerp_color(bg, "#000000", 0.15)
        self._cur_color = bg
        try:
            self.configure(bg=self._pad.cget("bg"))
        except Exception:
            pass
        self._draw(bg, self._offset)

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        try:
            self.configure(cursor="hand2" if on else "")
        except Exception:
            pass
        self._draw(lerp_color(self._bg, "#999999", 0.4) if not on else self._cur_color,
                   self._offset)

    # ---------- 动画 ----------
    def _animate_to(self, target: str, steps: int = 8, interval: int = 16):
        """颜色平滑过渡（分帧插值）。"""
        if self._anim_job:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        start = self._cur_color
        seq = [lerp_color(start, target, (i + 1) / steps) for i in range(steps)]

        def step(i=0):
            if i >= len(seq):
                self._cur_color = target
                self._anim_job = None
                return
            try:
                if not self.winfo_exists():
                    return
                self._cur_color = seq[i]
                self._draw(seq[i], self._offset)
                self._anim_job = self.after(interval, lambda: step(i + 1))
            except Exception:
                self._anim_job = None
        step()

    # ---------- 事件 ----------
    def _on_enter(self, _e=None):
        if not self._enabled:
            return
        self._offset = -1              # 轻微上浮
        self._animate_to(self._hover)

    def _on_leave(self, _e=None):
        self._offset = 0
        self._animate_to(self._bg)

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._offset = 1               # 按下下沉
        self._animate_to(self._active, steps=5)

    def _on_release(self, _e=None):
        if not self._enabled:
            return
        self._offset = -1
        self._animate_to(self._hover)
        if self._command:
            try:
                self._command()
            except Exception:
                pass


class Card(tk.Canvas):
    """圆角卡片容器（比 tk.Frame 的直角更柔和）。

    内部放一个普通 Frame 承载子控件，外层 Canvas 画圆角底。
    """

    def __init__(self, parent, C: dict, radius: int = 12, padx: int = 0,
                 pady: int = 0, **kw):
        self._C = C
        self._radius = radius
        try:
            pbg = parent.cget("bg")
        except Exception:
            pbg = C.get("bg", "#eef1f6")
        super().__init__(parent, bg=pbg, highlightthickness=0, bd=0, **kw)
        self.inner = tk.Frame(self, bg=C.get("card", "#ffffff"))
        self._win = self.create_window(padx, pady, anchor="nw", window=self.inner)
        self._shape = None
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _e=None):
        try:
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 4 or h < 4:
                return
            self.delete("shape")
            _round_rect(self, 0, 0, w, h, self._radius,
                        fill=self._C.get("card", "#ffffff"),
                        outline=self._C.get("border", "#d8dee8"), width=1)
            self.tag_lower("shape")
            self.itemconfigure(self._win, width=w)
        except Exception:
            pass


def toast(root, C: dict, text: str, seconds: float = 2.4,
          kind: str = "info", action=None, action_text: str = "撤销"):
    """右下角浮出提示，几秒后自动消失 —— 不打断操作，不用点确定。

    比 messagebox 友好得多：保存成功、复制成功这类反馈不该弹窗。

    action 给定时，提示右侧会出现一个按钮（默认「撤销」）。
    点它会执行 action() 并立刻关闭提示 —— 用于「删除后可反悔」。
    带 action 的提示会自动延长到 6 秒，给用户反应时间。
    """
    try:
        pal = {"info": C.get("accent", "#2f6fd0"),
               "ok":   C.get("ok", "#1f9d55"),
               "warn": C.get("warn", "#c77700"),
               "err":  C.get("danger", "#c0392b")}
        bg = pal.get(kind, pal["info"])

        w = tk.Toplevel(root)
        w.wm_overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass

        row = tk.Frame(w, bg=bg)
        row.pack()
        tk.Label(row, text=text, bg=bg, fg="#ffffff",
                 font=("Microsoft YaHei UI", 10), padx=16, pady=10,
                 justify="left", wraplength=380).pack(side="left")

        if action is not None:
            # 撤销类提示给足时间（6 秒），否则用户来不及点
            seconds = max(float(seconds or 0), 6.0)
            act = tk.Label(row, text=action_text, bg=bg, fg="#ffffff",
                           font=("Microsoft YaHei UI", 10, "underline"),
                           padx=12, pady=10, cursor="hand2")
            act.pack(side="right")

            def _do_action(_e=None):
                try:
                    action()
                except Exception:
                    pass
                close()

            act.bind("<Button-1>", _do_action)

        try:
            root.update_idletasks()
            rx = root.winfo_rootx() + root.winfo_width()
            ry = root.winfo_rooty() + root.winfo_height()
        except Exception:
            rx, ry = 900, 700
        w.update_idletasks()
        tw, th = w.winfo_reqwidth(), w.winfo_reqheight()
        n = len(toast._live)
        x = max(10, rx - tw - 28)
        y = max(10, ry - th - 48 - 46 * n)
        w.wm_geometry("+%d+%d" % (x, y))
        toast._live.append(w)

        def close():
            try:
                if w in toast._live:
                    toast._live.remove(w)
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass

        w.after(int(max(0.6, seconds) * 1000), close)
        return w
    except Exception:
        return None


toast._live = []


# 兼容别名
show_toast = toast


def fade_in_widget(root, widget, steps: int = 6, interval: int = 16,
                   dy: int = 8):
    """让控件做一次「向上滑入」的入场动画（Tk 无透明度，用位移模拟）。"""
    try:
        info = widget.pack_info()
    except Exception:
        return

    def step(i=0):
        if i > steps:
            return
        try:
            if not widget.winfo_exists():
                return
            pady = info.get("pady", 0)
            if isinstance(pady, str):
                pady = 0
            py = int(pady or 0) + int(dy * (1 - i / float(steps)))
            widget.pack_configure(pady=py)
            widget.after(interval, lambda: step(i + 1))
        except Exception:
            pass
    step()


def make_round_button(parent, text, command, kind="normal", C=None, **kw):
    """按角色快速建一个圆角按钮。"""
    C = C or {}
    pal = {
        "primary": (C.get("accent", "#2f6fd0"), "#ffffff",
                    C.get("accent_hv", "#1f5bb8")),
        "accent":  (C.get("card", "#ffffff"), C.get("accent", "#2f6fd0"),
                    C.get("accent_sf", "#e8f0fd")),
        "danger":  (C.get("card", "#ffffff"), C.get("danger", "#c0392b"),
                    C.get("accent_sf", "#fdecea")),
        "ghost":   (C.get("bg_alt", "#f7f9fc"), C.get("text_sub", "#4a5160"),
                    C.get("head_bg", "#e3e9f2")),
    }.get(kind, (C.get("head_bg", "#e3e9f2"), C.get("text", "#1f2329"),
                 C.get("accent_sf", "#e8f0fd")))
    bg, fg, hv = pal
    parent_bg = C.get("bg", "#eef1f6")
    return RoundButton(parent, text=text, command=command,
                       bg=bg, fg=fg, hover=hv, **kw)

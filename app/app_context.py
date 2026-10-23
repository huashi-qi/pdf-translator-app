# -*- coding: utf-8 -*-
"""应用上下文：集中管理界面共享状态。

为什么需要它
------------
之前 app_sidebar.main() 里有 60 个嵌套函数、30 个共享变量，
全靠 Python 闭包访问。后果是：
  · main() 有 4000+ 行，改一处不知道会影响谁
  · 想把某个页面拆到独立文件，得把十几个变量挨个传进去
  · 出过「_QS 定义在 A 函数里、B 函数用不到」这种作用域 bug

AppContext 把这些状态收进一个对象，页面函数接收 ctx，
以后要拆出去只要传一个参数。
"""
from __future__ import annotations

import queue
from pathlib import Path


class AppContext:
    """界面共享状态。一个实例贯穿整个应用生命周期。"""

    def __init__(self, root, cfg: dict, themes: dict):
        # ---- 基础 ----
        self.root = root
        self.C = themes                     # 当前主题色字典
        self.cfg = cfg

        # ---- 字体 / 缩放 ----
        self.FONT = "Microsoft YaHei UI"
        self._FS = 10                       # 字号（会被 ui_font_size 覆盖）
        self.dpi = 96.0

        # ---- 翻译运行态 ----
        self.state = {
            "running": False,
            "last_dir": "",
            "t_start": 0.0,
            "cur_file": "",
            "batch": [],
            "results": {},
            "batch_ok": 0,
            "batch_fail": [],
            "batch_started": False,
        }

        # ---- 队列视图态 ----
        # 用户正在回看的那个文件（非空时暂停实时刷新）
        self.viewing = {"name": "", "path": ""}

        # ---- 控件注册表（key 带页面前缀） ----
        self.widgets: dict = {}
        self._cur_ns = {"k": ""}
        self.NS_NAMES = {"translate", "history", "tools", "settings",
                         "footer"}

        # ---- 页面缓存 ----
        self.page_cache: dict = {}
        self.page_built: dict = {}
        self.cur = {"key": "translate", "visible": "", "prev_key": ""}

        # ---- 导航 ----
        self.items: dict = {}
        self.ui_ref = {"nav": None, "body": None}
        self.NAV_W = 180

        # ---- 事件队列 ----
        self.ui_q: "queue.Queue" = queue.Queue()

        # ---- 阶段计时 ----
        self.stage_live = {"name": None, "start": 0.0}
        self.stage_done: list = []

        # ---- 主题 ----
        self.themed_ids: set = set()
        self.ui_state = {"theme": "light", "scale": 1.0, "font": 10}

        # ---- 队列状态映射 ----
        self.QS = {
            "wait":  ("○ 等待", "muted"),
            "run":   ("▶ 进行中", "accent"),
            "ok":    ("✓ 完成", "ok"),
            "fail":  ("✗ 失败", "danger"),
            "skip":  ("— 跳过", "muted"),
        }

        # ---- 杂项 ----
        self.gloss_sig = {"v": None}
        self.drop_tries = {"n": 0}
        self.tray_ok = {"ok": False}
        self.build_gen = {"n": 0}

    # ---- 便捷方法 ----
    def key(self, name: str) -> str:
        """把控件 key 加上当前页前缀。"""
        ns = self._cur_ns.get("k") or ""
        if not ns or not name:
            return name
        return ns + "." + name if not name.startswith(ns + ".") else name

    def get_widget(self, name: str):
        """取控件，自动补页面前缀。"""
        return self.widgets.get(self.key(name))

    def color(self, role: str) -> str:
        """按角色名取颜色（如 "muted" / "accent"）。"""
        return self.C.get(role) or self.C.get("text") or "#000000"

    def font(self, size: int, *styles):
        """字体工厂：返回 (family, 实际字号, *styles)。"""
        real = int(size + (self._FS - 10))
        return (self.FONT, real) + tuple(styles) if styles else \
               (self.FONT, real)

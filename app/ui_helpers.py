# -*- coding: utf-8 -*-
"""界面通用工具函数（无状态，可独立测试）。

这些函数原本嵌在 app_sidebar.main() 里，
但它们不依赖闭包状态，抽出来有三个好处：
  1. main() 变小，改一处不容易牵动全局
  2. 可以单独写单元测试
  3. 以后别的界面模块也能直接复用
"""
from __future__ import annotations

from pathlib import Path


def round_window(win) -> bool:
    """把 Windows 11 窗口的四角改成圆角。

    DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
    Win10 及更早版本会静默失败，返回 False，不影响功能。
    """
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        pref = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        return True
    except Exception:
        return False


def system_prefers_dark() -> bool:
    """读 Windows 的深色模式偏好。读不到就当作浅色。"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return int(v) == 0
    except Exception:
        return False


def resolve_theme(cfg: dict, valid_themes) -> str:
    """把配置里的 ui_theme 解析成实际要用的主题名。

    "auto" 会读系统偏好；其它值不在 valid_themes 里就退回 light。
    """
    m = str(cfg.get("ui_theme") or "light").lower()
    if m == "auto":
        m = "dark" if system_prefers_dark() else "light"
    return m if m in valid_themes else "light"


def fmt_sec(s) -> str:
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


def gloss_total_terms() -> int:
    """词库总条数（进程内缓存，避免每次都解析 CSV）。"""
    if gloss_total_terms._n is None:
        try:
            import glossary_hook as _gh
            gloss_total_terms._n = int(_gh.load_glossary() or 0)
        except Exception:
            gloss_total_terms._n = 0
    return gloss_total_terms._n


gloss_total_terms._n = None


def shorten_path(p, keep: int = 2) -> str:
    """路径只保留最后几段，用于日志/诊断（避免暴露用户名）。"""
    try:
        parts = Path(str(p)).parts
        return str(Path(*parts[-keep:])) if len(parts) >= keep else str(p)
    except Exception:
        return str(p)

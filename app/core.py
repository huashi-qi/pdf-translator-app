# -*- coding: utf-8 -*-
"""PDF 翻译工具 — 核心后端（无 UI）

本模块只包含后端能力，**不依赖 Tkinter 界面**：

  · 配置读写 / 历史记录 / 分组
  · 翻译流程调度（run_translate）
  · 引擎列表与连通性测试
  · 主题配色定义
  · 打开文件 / 输出目录解析 / 术语翻译

界面的实现在 app_sidebar.py（侧边栏版）。
单元测试可以直接 import 本模块，不需要创建窗口。

经典标签页界面（原 app.py 的 build_ui）已移除，只保留侧边栏版。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------- 路径与自包含
APP_DIR = Path(__file__).resolve().parent
RUNTIME = APP_DIR / "runtime"
PYTHON = RUNTIME / "python.exe"
WORKER = APP_DIR / "worker.py"
CACHE_DIR = APP_DIR / "cache"          # 离线资源（字体/模型），随包分发
DEFAULT_OUTPUT_DIR = APP_DIR / "output"
# Word 产物统一放在这个子目录（即界面上的「Word 分组」）
CONFIG_FILE = APP_DIR / "config.json"

# 先记下用户真实的 HOME，供「保存位置」里的 %USERPROFILE% 之类变量展开使用。
# （下面会把 USERPROFILE 改掉，若不留存，用户填 %USERPROFILE%\Desktop 会解析到程序缓存目录）
_ORIG_HOME = {
    "USERPROFILE": os.environ.get("USERPROFILE", ""),
    "HOME": os.environ.get("HOME", ""),
    "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
    "HOMEPATH": os.environ.get("HOMEPATH", ""),
}

# 缓存重定向交给 sitecustomize.py 完成（它直接改写 babeldoc.CACHE_FOLDER）。
# 早期做法是改 USERPROFILE/HOME，但 Tk 的文件对话框也用 env(HOME) 当默认目录，
# 于是会去找不存在的 <cache>\Desktop 并弹「位置不可用」。现在 HOME 保持原样。
# 这里只做兜底：确保缓存目录存在。
try:
    (CACHE_DIR / ".cache" / "babeldoc").mkdir(parents=True, exist_ok=True)
except Exception:
    pass
# 隔离掉可能干扰的全局 Python 变量
for _v in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE"):
    os.environ.pop(_v, None)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# ---------------------------------------------------------------- 主题配色
# 白天 / 夜间两套。ttk 必须用 clam 主题才能让 configure 的配色生效
# （vista/winnative 是系统原生主题，会忽略 background/foreground）。
THEMES = {
    "light": {
        "bg":        "#eef1f6",
        "bg_alt":    "#f7f9fc",
        "card":      "#ffffff",
        "head_bg":   "#e3e9f2",
        "text":      "#1f2329",
        "text_sub":  "#4a5160",
        "muted":     "#8a9099",
        "border":    "#d8dee8",
        "accent":    "#2f6fd0",
        "accent_hv": "#1f5bb8",
        "accent_sf": "#e8f0fd",
        "row_alt":   "#f6f8fb",
        "entry_bg":  "#ffffff",
        "log_bg":    "#1e1e1e",
        "log_fg":    "#d4d4d4",
        "ok":        "#1f9d55",
        "warn":      "#c77700",
        "danger":    "#c0392b",
        "sidebar":   "#e7ebf2",
        "sidebar_on": "#d3e0f5",
        "reader_bg": "#8a8f99",      # PDF 阅读区（衬托白纸）
    },
    # 夜间配色（重做过一版）。
    # 旧版偏蓝发灰，看着"糊"；新版改成中性深灰 + 清爽蓝，
    # 层次拉开（底 < 卡 < 表头），对比度按 WCAG AA 调过。
    "dark": {
        "bg":         "#16181d",     # 主背景：中性深灰，不偏蓝
        "bg_alt":     "#1c1f26",     # 次级背景（分区、侧栏内容）
        "card":       "#20242b",     # 卡片：比底亮一档，形成层次
        "head_bg":    "#2a2f38",     # 表头/工具条
        "text":       "#eef1f6",     # 主文字（对比度 13.8:1）
        "text_sub":   "#bcc4d0",     # 次级文字（9.1:1）
        "muted":      "#8d96a5",     # 弱化文字（5.2:1，达标）
        "border":     "#313742",     # 分隔线：隐约可见，不抢眼
        "accent":     "#4c9aff",     # 主色：清爽蓝，比旧版更亮更干净
        "accent_hv":  "#6db1ff",     # 悬停
        "accent_sf":  "#243247",     # 淡色底（选中行、按钮底）
        "row_alt":    "#1f232a",     # 表格隔行
        "entry_bg":   "#1a1e24",     # 输入框
        "log_bg":     "#101216",     # 日志区（最深）
        "log_fg":     "#d5dbe5",
        "ok":         "#46d68a",
        "warn":       "#f0ab3c",
        "danger":     "#f26a5d",
        "sidebar":    "#101216",     # 导航栏：比内容区更深，拉开层次
        "sidebar_on": "#243247",     # 导航选中
        "reader_bg":  "#0b0d11",     # PDF 阅读区（最深，衬托纸张）
    },
}


def enable_dpi_awareness() -> str:
    """开启高 DPI 感知 —— 不开启界面会发虚。

    实测：这台机器真实 DPI 是 120（125% 缩放），但进程默认是
    "DPI UNAWARE"，Windows 会把整个窗口按 1.25 倍拉伸再画出来，
    于是文字和线条全都糊。开启感知后 Tk 直接按物理像素绘制，立刻清晰。

    必须在创建 Tk 窗口【之前】调用，否则无效。
    返回实际启用的级别名（便于日志里确认）。
    """
    import ctypes
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return "非 Windows"

    # 1) Per-Monitor V2（最好，支持多显示器不同缩放）
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "PER_MONITOR_AWARE_V2"
    except Exception:
        pass
    # 2) 旧接口
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "PER_MONITOR_AWARE"
    except Exception:
        pass
    # 3) 最老的兜底
    try:
        user32.SetProcessDPIAware()
        return "SYSTEM_AWARE"
    except Exception:
        return "失败"


def get_real_dpi() -> int:
    """读真实 DPI（要在开启感知之后读，否则只会读到 96）。"""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        dc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)   # LOGPIXELSX
        user32.ReleaseDC(0, dc)
        return int(dpi) if dpi else 96
    except Exception:
        return 96


# 自带的功能模块（延迟导入：PyMuPDF / python-docx 会拖慢启动）
def _lazy_import(name: str):
    """按需导入自带模块，避免拖慢启动。"""
    try:
        import importlib
        return importlib.import_module(name)
    except Exception:
        return None


# 阶段名中英对照（babeldoc 报的是英文，界面上显示中文）
STAGE_CN = {
    "Parse PDF and Create Intermediate Representation": "解析 PDF 结构",
    "Parse Page Layout": "分析页面版面",
    "Parse Paragraphs": "切分段落",
    "Parse Formulas and Styles": "解析公式与样式",
    "Translate Paragraphs": "翻译段落",
    "Typesetting": "排版重排",
    "Save PDF": "保存 PDF",
    "Subset font": "字体子集化",
    "Font subsetting": "字体子集化",
    "Detect Scanned Pages": "检测扫描页",
    "Parse Table": "识别表格",
    "Translate Table": "翻译表格",
    "Generate PDF": "生成 PDF",
    "Complete": "完成",
    "Total": "总计",
    # 以下为实测中出现的阶段名（补全，避免界面显示英文）
    "DetectScannedFile": "检测扫描页",
    "Detect Scanned File": "检测扫描页",
    "Add Fonts": "嵌入字体",
    "Generate drawing instructions": "生成绘图指令",
    "Merge Results": "合并结果",
    "Prepare Document": "准备文档",
    "Extract Text": "提取文字",
    "Rendering": "渲染",
    "Clean Up": "清理中间文件",
}

# 阶段的通俗解释（鼠标悬停/提示用）
STAGE_TIP = {
    "解析 PDF 结构": "读取 PDF 内部对象，建立中间表示",
    "分析页面版面": "用版面模型判断哪里是标题、正文、表格、图片",
    "切分段落": "把版面元素整理成一句一句的段落",
    "解析公式与样式": "识别数学公式与字体样式",
    "翻译段落": "调用翻译引擎（这一步最耗时，且受网络影响）",
    "排版重排": "把译文按原版面大小重新排进去",
    "保存 PDF": "写出最终 PDF 文件",
    "字体子集化": "只保留用到的字形，减小体积",
    "检测扫描页": "判断哪些页是图片（没有文字层）",
    "识别表格": "定位表格区域",
    "翻译表格": "翻译表格内的文字",
    "生成 PDF": "合成页面",
    "完成": "全部结束",
}


def stage_cn(name: str) -> str:
    """把英文阶段名转成中文（没有对照就原样返回）。"""
    n = (name or "").strip()
    if not n:
        return ""
    if n in STAGE_CN:
        return STAGE_CN[n]
    # 模糊匹配（babeldoc 偶尔会带额外前后缀）
    for k, v in STAGE_CN.items():
        if k.lower() in n.lower() or n.lower() in k.lower():
            return v
    return n


DEFAULT_CONFIG = {
    # 默认用 bing：免费、无需 API Key、国内可直连。
    # 也可改为 deepseek / siliconflow / openai 等，并在下面填 api_key。
    "engine": "bing",
    "lang_in": "en",
    "lang_out": "zh-CN",
    "api_key": "",
    "base_url": "",
    "model": "",
    # 并发数：翻译是网络往返为主，提高并发可显著加速。
    # 实测每段耗时：qps10/pool10 = 312ms，qps10/pool100 = 233ms，qps20/pool200 = 196ms。
    # 默认 20；遇到限流会自动降级（见 run_translate 的重试逻辑）。
    "threads": 20,
    # 输出：mono=只出中文版；dual=只出双语对照版；both=两种都要
    "output_mode": "mono",
    # 保存位置，留空表示程序目录下的 output
    "output_dir": "",
    # 术语表：默认启用内置的电子工程术语（1046 条），免费引擎也生效
    "glossary_enabled": True,
    # 可指定自己的术语表 CSV（source,target 两列），留空则用内置的
    "glossary_file": "",
    # 输出文件名：留空则自动用「原文件名+版本」（如 SGM.zh-CN.mono）
    "output_name": "",
    # 排版增强：处理版面错乱（文本框重叠、分栏串行等）
    "enhance_compatibility": False,
    "split_short_lines": False,
    # 历史记录双击时用什么打开（auto=系统默认 / wps / folder）
    "open_with": "auto",
    "open_with_path": "",
    # 忽略译文缓存，强制重新翻译（改了术语表或想换引擎重翻时用）
    "ignore_cache": False,
    # 只翻译指定页码，如 "1-10,15"；留空 = 全部
    "pages": "",
    # 遇到接口限流时自动降低并发重试（而不是直接失败）
    "auto_degrade": True,
    # 翻译完成后：ask=每次询问 / open=自动打开 / none=不打开
    "after_translate": "ask",
    # 只启用某类领域的术语：all / power / analog / digital / device / package / rf
    "glossary_domain": "all",
    # 翻译后提取高频专业词，供确认后加入术语表（免费引擎也有效）
    "auto_learn_glossary": True,
    # 翻译后检测可疑译文
    "quality_check": True,
    # 批量翻译时最多同时处理几个文件
    "batch_workers": 1,
    # 输出类型：pdf=只出 PDF / word=只出 Word / both=两者都要
    "output_format": "pdf",
    # 翻译预设：{名称: {字段...}}，一键切换整套设置
    "presets": {},
    # 当前使用的预设名（空 = 未用预设）
    "preset_active": "",
    # 跳过目录页（保留原样，避免条目被合并成一个段落）
    "skip_toc": False,
    # 界面主题：light=白天 / dark=夜间
    "ui_theme": "light",
    # 界面布局：classic=经典（顶部工具栏+标签页）/ sidebar=侧边栏（新版）
    "ui_layout": "classic",
}

# 引擎下拉框：只列实测可用的。
# (显示名, 引擎标识, 是否需要 API Key, 是否支持术语表)
ENGINE_CHOICES = [
    ("Bing（免费，无需 Key）", "bing", False, True),
    ("Google（免费，无需 Key）", "google", False, True),
    ("DeepSeek（需 Key，质量好）", "deepseek", True, True),
    ("硅基流动 SiliconFlow（需 Key）", "siliconflow", True, True),
    ("阿里通义 DashScope（需 Key）", "aliyundashscope", True, True),
    ("智谱 GLM（需 Key）", "zhipu", True, True),
    ("OpenAI（需 Key，需能访问）", "openai", True, True),
    ("Gemini（需 Key，需能访问）", "gemini", True, True),
]
ENGINE_LABEL = {e[1]: e[0] for e in ENGINE_CHOICES}
ENGINE_NEEDS_KEY = {e[1]: e[2] for e in ENGINE_CHOICES}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8"
        )
    except Exception:
        pass


HISTORY_FILE = APP_DIR / "history.json"
HISTORY_MAX = 100


def load_history() -> list[dict]:
    """读取历史翻译记录（最新的在前）。"""
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def add_history(entry: dict) -> None:
    """追加一条历史记录。"""
    try:
        items = load_history()
        # 新记录默认归到「未分组」，用户可在历史页里改
        entry.setdefault("group", "")
        items.insert(0, entry)
        HISTORY_FILE.write_text(
            json.dumps(items[:HISTORY_MAX], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def set_history_group(timestamp: str, group: str) -> None:
    """给某条历史记录设置分组（'' 表示未分组）。"""
    try:
        items = load_history()
        for it in items:
            if str(it.get("time")) == timestamp:
                it["group"] = group or ""
                break
        HISTORY_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def list_groups() -> list[str]:
    """所有已使用的分组名（按出现顺序）。"""
    seen: list[str] = []
    for it in load_history():
        g = str(it.get("group") or "").strip()
        if g and g not in seen:
            seen.append(g)
    return seen


def _existing_dir(*candidates) -> str:
    r"""返回第一个存在的目录，用于文件对话框的 initialdir。

    背景：程序会把 USERPROFILE 重定向到自带 cache 目录（babeldoc 需要），
    而 Tk 的文件对话框默认用 HOME 当起始目录，于是会去找不存在的
    <cache>\Desktop，Windows 就弹「位置不可用」。显式传 initialdir 即可避免。
    """
    for c in candidates:
        if not c:
            continue
        try:
            p = Path(str(c))
            if p.is_dir():
                return str(p)
        except Exception:
            continue
    return str(APP_DIR)


def resolve_output_dir(cfg: dict) -> Path:
    """用户设定的保存位置；留空则用程序目录下的 output。"""
    raw = str(cfg.get("output_dir") or "").strip().strip('"')
    if not raw:
        return DEFAULT_OUTPUT_DIR
    try:
        # 用「用户真实的家目录」展开环境变量，而不是被重定向过的 USERPROFILE
        expanded = raw
        for k, v in _ORIG_HOME.items():
            if v:
                expanded = expanded.replace("%" + k + "%", v)
                expanded = expanded.replace("$env:" + k, v)
                expanded = expanded.replace(" + k + ", v)
        expanded = os.path.expanduser(expanded)
        p = Path(expanded)
        if not p.is_absolute():
            p = APP_DIR / p
        return p
    except Exception:
        return DEFAULT_OUTPUT_DIR


# ---------------------------------------------------------------- 翻译核心
ENGINE_FLAGS = {
    "bing", "google", "siliconflowfree", "deepseek", "openai", "siliconflow",
    "aliyundashscope", "zhipu", "modelscope", "gemini", "deepl", "ollama",
    "azureopenai", "qwenmt", "openaicompatible", "grok", "groq", "tencentmechinetranslation",
}
ENGINE_API_KEY_FLAG = {
    "deepseek": "--deepseek-api-key",
    "openai": "--openai-api-key",
    "siliconflow": "--siliconflow-api-key",
    "aliyundashscope": "--aliyun-dashscope-api-key",
    "zhipu": "--zhipu-api-key",
    "modelscope": "--modelscope-api-key",
    "gemini": "--gemini-api-key",
    "azureopenai": "--azure-openai-api-key",
}
ENGINE_BASE_URL_FLAG = {
    "openai": "--openai-base-url",
    "siliconflow": "--siliconflow-base-url",
    "aliyundashscope": "--aliyun-dashscope-base-url",
    "openaicompatible": "--openai-compatible-base-url",
}
ENGINE_MODEL_FLAG = {
    "openai": "--openai-model",
    "deepseek": "--deepseek-model",
    "siliconflow": "--siliconflow-model",
    "aliyundashscope": "--aliyun-dashscope-model",
    "zhipu": "--zhipu-model",
    "modelscope": "--modelscope-model",
    "gemini": "--gemini-model",
}
# 输出版本 -> CLI 开关
MODE_LABELS = {
    "mono": "中文版（仅译文）",
    "dual": "双语对照版",
    "both": "中文版 + 双语对照版",
}


def build_worker_args(pdf: Path, cfg: dict, out_dir: Path) -> list[str]:
    """构造传给 worker.py 的 pdf2zh_next 参数。"""
    args = [
        "--lang-in", str(cfg.get("lang_in") or "en"),
        "--lang-out", str(cfg.get("lang_out") or "zh-CN"),
        "--output", str(out_dir),
        "--watermark-output-mode", "no_watermark",
    ]

    # 并发数（qps 与 worker 池大小）：翻译瓶颈是网络往返，提高并发可显著加速。
    try:
        threads = int(cfg.get("threads") or 10)
    except Exception:
        threads = 10
    threads = max(1, min(30, threads))
    # 官方建议：pool_max_workers = qps × 10（经验系数），能显著提高吞吐。
    # 上限 1000（官方文档建议）。用户只调 qps，worker 池自动跟随。
    pool = min(1000, threads * 10)
    args += ["--qps", str(threads), "--pool-max-workers", str(pool)]

    # 术语表：内置术语由 sitecustomize 自动注入（免费引擎也生效），
    # 这里只处理「用户自带的 CSV」——它需要走官方 glossary 通道。
    gfile = str(cfg.get("glossary_file") or "").strip()
    if gfile:
        # 支持多个术语表（用逗号或分号分隔），官方 --glossaries 支持逗号列表
        parts = [x.strip().strip('"') for x in re.split(r"[,;]", gfile) if x.strip()]
        resolved = []
        for one in parts:
            gp = Path(one)
            if not gp.is_absolute():
                gp = APP_DIR / gp
            if gp.exists():
                resolved.append(str(gp))
        if resolved:
            args += ["--glossaries", ",".join(resolved)]

    # 输出版本：mono 只要中文；dual 只要双语；both 两种都出
    mode = str(cfg.get("output_mode") or "mono").strip().lower()
    if mode == "mono":
        args.append("--no-dual")
    elif mode == "dual":
        args.append("--no-mono")
    # both：两个开关都不加

    # 排版增强（针对版面错乱）
    if cfg.get("enhance_compatibility"):
        args.append("--enhance-compatibility")
    if cfg.get("split_short_lines"):
        args.append("--split-short-lines")

    # 忽略译文缓存，强制重新翻译
    if cfg.get("ignore_cache"):
        args.append("--ignore-cache")

    # 只翻译指定页码（如 "1-10,15"）
    pages = str(cfg.get("pages") or "").strip()
    if pages:
        args += ["--pages", pages]

    # 注：输出文件名不支持用 CLI 参数指定（pdf2zh_next 无此选项），
    # 改为翻译完成后重命名，见 rename_outputs()。

    # pdf2zh_next 用「开关」选择引擎，例如 --bing / --deepseek
    engine = str(cfg.get("engine") or "bing").strip().lower()
    if engine not in ENGINE_FLAGS:
        engine = "bing"
    args.append("--" + engine)

    key = str(cfg.get("api_key") or "").strip()
    if key and engine in ENGINE_API_KEY_FLAG:
        args += [ENGINE_API_KEY_FLAG[engine], key]
    base = str(cfg.get("base_url") or "").strip()
    if base and engine in ENGINE_BASE_URL_FLAG:
        args += [ENGINE_BASE_URL_FLAG[engine], base]
    model = str(cfg.get("model") or "").strip()
    if model and engine in ENGINE_MODEL_FLAG:
        args += [ENGINE_MODEL_FLAG[engine], model]

    args.append(str(pdf))
    return args


def _safe_filename(name: str) -> str:
    """去掉 Windows 文件名非法字符。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name or "").strip())
    name = name.strip().rstrip(".")
    return name[:120]


def rename_outputs(pdfs: list[Path], pdf: Path, cfg: dict) -> list[Path]:
    """按用户设定重命名输出文件。

    留空则保持 pdf2zh 的默认命名（原名 + 语言 + 版本后缀），
    这也正是用户要求的默认行为。
    自定义时把版本后缀保留，避免 mono/dual 互相覆盖。
    """
    custom = _safe_filename(cfg.get("output_name") or "")
    if not custom:
        return pdfs

    renamed: list[Path] = []
    for f in pdfs:
        low = f.name.lower()
        if ".dual." in low:
            suffix = ".dual"
        elif ".mono." in low:
            suffix = ".mono"
        else:
            suffix = ""
        target = f.with_name(custom + suffix + f.suffix)
        try:
            if target.exists() and target.resolve() != f.resolve():
                target.unlink()
            if target.resolve() != f.resolve():
                f.rename(target)
                renamed.append(target)
            else:
                renamed.append(f)
        except Exception:
            renamed.append(f)
    return renamed


def detect_locked_pdfs(out_dir: Path) -> list:
    """找出输出目录里被其它程序占用（WPS/Adobe 正开着）的 PDF。

    背景：用户常把上次的译文留在阅读器里，再翻一次时 babeldoc 写不进去，
    报 "cannot remove file ... Permission denied"。
    这个错看着像权限问题，实际是文件锁 —— 重试多少次都没用。
    """
    locked = []
    try:
        if not out_dir.is_dir():
            return locked
        for p in out_dir.glob("*.pdf"):
            try:
                with open(p, "r+b"):
                    pass
            except PermissionError:
                locked.append(p)
            except OSError:
                pass      # 其它 OSError（路径过长等）不算锁
    except Exception:
        pass
    return locked


# 当前正在运行的翻译子进程（供「终止翻译」使用）
_CURRENT_PROC = {"proc": None, "cancel": False}


def cancel_translation() -> bool:
    """终止正在进行的翻译。

    做法：标记 cancel + 杀掉子进程树。
    为什么连子进程一起杀：翻译实际跑在 worker.py 的子进程里，
    只杀 worker 会留下孤立的 python 进程继续占资源。
    """
    _CURRENT_PROC["cancel"] = True
    proc = _CURRENT_PROC.get("proc")
    if proc is None:
        return False
    try:
        if os.name == "nt":
            # taskkill 带 /T 能连带子进程一起结束
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return True


def is_translating() -> bool:
    proc = _CURRENT_PROC.get("proc")
    return proc is not None and proc.poll() is None



def _is_user_cancel(text: str) -> bool:
    """是不是「用户点了终止」——这种不该重试。"""
    t = (text or "").strip()
    return t in ("用户终止",) or "手动终止" in t


def _is_hard_error(text: str) -> bool:
    """是不是「换引擎也没用」的硬错误（文件本身有问题）。"""
    t = (text or "").lower()
    return any(k in t for k in (
        "input file is generated by babeldoc",   # 拿译文再翻
        "cannot translate files that have alrea",
        "file is encrypted", "needs_pass", "需要密码",
        "no such file", "找不到指定的文件", "winerror 2",
        "no pages", "pdf 损坏", "cannot open",
    ))


def _is_lock_error(text: str) -> bool:
    """报错是不是「文件被占用」（而不是限流）。"""
    t = (text or "").lower()
    return any(k in t for k in (
        "permission denied", "winerror 32", "cannot remove file",
        "另一个程序正在使用此文件", "being used by another process",
        "permissionerror",
    ))


def run_translate(pdf: Path, cfg: dict, out_dir: Path, log, on_progress, on_done,
                  on_stage_cb=None):
    """后台线程入口：按失败原因决定怎么重试。

    两种情况要分开处理：
      · 输出文件被占用（WPS 开着上次的译文）→ 换带时间戳的新名字
      · 接口限流（429/超时/SSL 抖动）→ 降并发重试

    以前不区分，遇到文件锁也降并发重试 3 次，全是白费功夫。
    """
    # ①②③ 预检：源文件 / 加密 / 保存目录 —— 动手之前拦掉必然失败的情况
    try:
        _pf = preflight(pdf, out_dir)
        if not _pf.get("ok"):
            log("")
            log("❌ " + str(_pf.get("title") or "无法开始翻译"))
            for _ln in str(_pf.get("advice") or "").splitlines():
                log("   " + _ln)
            _info = {"title": _pf.get("title"), "advice": _pf.get("advice"),
                     "solvable": True, "matched": "preflight", "raw": ""}
            try:
                _tm = dict(timing_out)
                _tm["preflight"] = _info
                on_done(False, None, _pf.get("title") or "预检未通过", _tm)
            except Exception:
                pass
            return False, str(_pf.get("title") or "")
        try:
            timing_out["total_pages"] = int(_pf.get("pages") or 0)
        except Exception:
            pass
    except Exception:
        pass          # 预检本身出错就不阻断翻译

    # 预检：输出目录里有没有被占用的 PDF
    try:
        locked = detect_locked_pdfs(out_dir)
    except Exception:
        locked = []
    if locked:
        log("")
        log("⚠️ 这些文件正被其它程序打开：")
        for p in locked[:3]:
            log("   " + p.name)
        log("   常见原因：上次的译文还开在 WPS / Adobe 里，导致无法覆盖。")
        try:
            import datetime as _dt
            stamp = _dt.datetime.now().strftime("%H%M%S")
            old_name = str(cfg.get("output_name") or "").strip()
            cfg = dict(cfg)
            cfg["output_name"] = ((old_name + "_" + stamp) if old_name
                                  else (pdf.stem + "_" + stamp))
            log("   → 已改用新文件名，不会覆盖你正在看的文件：")
            log("     " + cfg["output_name"] + ".*.pdf")
        except Exception:
            pass

    try:
        start_th = int(cfg.get("threads") or 20)
    except Exception:
        start_th = 20
    auto = bool(cfg.get("auto_degrade", True))
    attempts = [start_th]
    if auto:
        t = start_th
        for _ in range(2):
            t = max(4, t // 2)
            if t not in attempts:
                attempts.append(t)

    # 引擎自动切换：降并发也失败后，如果当前是免费引擎，
    # 就换另一个免费引擎再试一次（Bing ⇄ Google）。
    # 用付费 Key 的引擎不自动换 —— 换了就变成另一个账号在花钱。
    FREE_ENGINES = ["bing", "google"]
    cur_engine = str(cfg.get("engine") or "bing").lower()
    engine_chain = [cur_engine]
    if cur_engine in FREE_ENGINES:
        for e in FREE_ENGINES:
            if e != cur_engine and e not in engine_chain:
                engine_chain.append(e)
    # 有 Key 的引擎只跑自己，不串门
    if str(cfg.get("api_key") or "").strip():
        engine_chain = [cur_engine]

    last_err = ""
    for e_idx, eng in enumerate(engine_chain):
        if e_idx > 0:
            log("")
            log("🔄 换个引擎再试：%s" % ENGINE_LABEL.get(eng, eng))
            log("   （上一个引擎连续失败，自动切换，不用你手动改设置）")
        for idx, th in enumerate(attempts):
            c = dict(cfg)
            c["threads"] = th
            c["engine"] = eng
            if idx > 0:
                log("")
                log("⚠️ 上次失败，正在降低并发到 %d 重试（第 %d 次）…"
                    % (th, idx + 1))
            ok, err = _run_translate_once(pdf, c, out_dir, log, on_progress,
                                          on_done, on_stage_cb)
            if ok:
                if e_idx > 0:
                    log("")
                    log("✅ 换成 %s 后成功了。" % ENGINE_LABEL.get(eng, eng))
                return
            last_err = err or "翻译失败"

            # ★ 用户主动终止：立刻停，不要重试。
            # 之前漏了这一步，用户点了终止还会降并发再跑两轮，
            # 看起来像「终止按钮没反应」。
            if _is_user_cancel(last_err):
                on_done(False, None, last_err)
                return

            # 文件锁跟限流无关，降并发无用 —— 直接停下并说清怎么办
            if _is_lock_error(last_err):
                log("")
                log("❌ 失败原因：输出文件被其它程序占用（不是限流）。")
                log("   请关闭正在查看该 PDF 的程序（WPS / Adobe / 浏览器）后重试。")
                on_done(False, None, last_err)
                return
            # 加密/文件不存在这类硬错误，换引擎也没用
            if _is_hard_error(last_err):
                on_done(False, None, last_err)
                return

    log("")
    if len(engine_chain) > 1:
        log("❌ 已尝试 %d 个引擎都不行：%s"
            % (len(engine_chain),
               " / ".join(ENGINE_LABEL.get(e, e) for e in engine_chain)))
        log("   建议：稍后再试，或在「设置」里换用需要 Key 的引擎。")
    else:
        log("❌ 多次重试仍未成功。若是接口限流，请稍后再试或换引擎。")
    on_done(False, None, last_err)


def _run_translate_once(pdf: Path, cfg: dict, out_dir: Path, log, on_progress, on_done,
                        on_stage_cb=None):
    """跑一次 worker。返回 (是否成功, 错误信息)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(PYTHON), str(WORKER)] + build_worker_args(pdf, cfg, out_dir)
    mode = str(cfg.get("output_mode") or "mono").lower()
    try:
        _th = int(cfg.get("threads") or 10)
    except Exception:
        _th = 10
    _gloss = "内置电子工程术语" if cfg.get("glossary_enabled", True) else "已关闭"
    if str(cfg.get("glossary_file") or "").strip():
        _gloss = Path(str(cfg["glossary_file"])).name
    log("正在翻译：" + pdf.name)
    log("引擎：" + ENGINE_LABEL.get(str(cfg.get("engine")), str(cfg.get("engine"))))
    log("输出版本：" + MODE_LABELS.get(mode, mode))
    log("并发数：" + str(_th))
    log("专业术语：" + _gloss)
    log("保存位置：" + str(out_dir))
    log("")

    # 记录翻译前已有的 PDF，之后只认「新增/更新」的文件，
    # 否则会把输出目录里其它文档的旧译文也算进本次历史（曾出现的 bug）。
    try:
        before: dict[str, float] = {
            str(p): p.stat().st_mtime for p in out_dir.glob("*.pdf")
        }
    except Exception:
        before = {}

    cache_stat: dict = {"calls": 0, "hits": 0}
    timing_out: dict = {"stages": [], "total": 0.0}

    # ⑤ 断点续译需要知道这次到底翻了哪些页。
    # cfg["pages"] 可能是 "1-10,15,20-" 这种 spec；空表示全部。
    try:
        _tot_pages = count_pdf_pages(pdf)
        _spec = str(cfg.get("pages") or "")
        _expanded = expand_pages_spec(_spec, _tot_pages)
        # 空 spec 表示「全部页」
        timing_out["pages"] = _expanded or list(range(1, _tot_pages + 1))
        timing_out["total_pages"] = _tot_pages
    except Exception:
        timing_out["pages"] = []
        timing_out["total_pages"] = 0
    stage_now: dict = {"name": ""}

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = dict(os.environ)   # 缓存由 sitecustomize 在子进程里重定向，无需改 HOME
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=env,
            cwd=str(APP_DIR),
        )
        _CURRENT_PROC["proc"] = proc
        _CURRENT_PROC["cancel"] = False
        assert proc.stdout is not None
        failed_msg = ""
        cancelled = False
        for line in proc.stdout:
            # 用户点了「终止翻译」→ 停止读取并收尾
            if _CURRENT_PROC.get("cancel"):
                cancelled = True
                break
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("@@P@@ "):
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                kind = ev.get("kind")
                if kind == "progress":
                    # 把页数信息挂到模块变量，供侧边栏版读取（进度条显示第 N/M 页）
                    _PROG_EXTRA = {
                        "stage_current": ev.get("stage_current"),
                        "stage_total": ev.get("stage_total"),
                    }
                    globals()["_PROG_EXTRA"] = _PROG_EXTRA
                    _ov = float(ev.get("overall") or 0.0)

                    # ⑤ 断点续译：在这里落盘，不依赖 UI。
                    # 好处：即使界面被强关、进程被杀，进度也已经存下来了。
                    # 每 5 秒最多写一次，避免频繁 IO。
                    try:
                        import time as _tm
                        _now = _tm.time()
                        if _now - float(PROG_SAVE_AT.get("t") or 0) >= 5.0:
                            PROG_SAVE_AT["t"] = _now
                            # ★ 用真实页数，不用 stage_total。
                            # stage_total 是 babeldoc 的内部计数（可能含重复解析），
                            # 实测 12 页文档它给出 24 —— 直接拿来算会错得离谱。
                            _tot = int(timing_out.get("total_pages") or 0)                                 or int(PROG_SAVE_AT.get("pages") or 0)
                            if _tot > 0:
                                PROG_SAVE_AT["pages"] = _tot
                                _dn = int(round(_tot * _ov / 100.0))
                                # 只记「确实翻完」的页：留 1 页余量，
                                # 因为最后几页可能还在处理中
                                _dn = min(_dn, max(0, _tot - 1))
                                if _dn > 0:
                                    save_progress(pdf, list(range(1, _dn + 1)),
                                                  _tot, completed=False)
                    except Exception:
                        pass

                    on_progress(_ov, str(ev.get("stage") or ""))
                elif kind == "timing":
                    # 阶段耗时 → 界面「步骤耗时」页
                    try:
                        st = ev.get("stages") or []
                        tot = float(ev.get("total") or 0.0)
                        timing_out["stages"] = st
                        timing_out["total"] = tot
                    except Exception:
                        pass
                elif kind == "stage":
                    _nm = str(ev.get("name") or "")
                    stage_now["name"] = _nm
                    # 实时推到界面（「步骤耗时」页会显示正在跑哪一步）
                    if on_stage_cb is not None:
                        try:
                            on_stage_cb(_nm)
                        except Exception:
                            pass
                elif kind == "cache":
                    # 缓存命中统计（翻译段数 / 直接命中缓存的段数）
                    try:
                        _c = int(ev.get("calls") or 0)
                        _h = int(ev.get("hits") or 0)
                    except Exception:
                        _c, _h = 0, 0
                    cache_stat["calls"] = _c
                    cache_stat["hits"] = _h
                elif kind == "error":
                    failed_msg = str(ev.get("error") or "")
                continue
            log(line)
        code = proc.wait()
        _CURRENT_PROC["proc"] = None

        # 用户主动终止 → 直接返回，不当成失败
        if cancelled or _CURRENT_PROC.get("cancel"):
            _CURRENT_PROC["cancel"] = False
            log("")
            log("⛔ 翻译已被手动终止。")
            return False, "用户终止"

        if code == 0:
            # 只挑本次新产生或刚更新的 PDF，避免混入同目录下其它文档的旧译文
            try:
                after = list(out_dir.glob("*.pdf"))
                fresh = [
                    p for p in after
                    if str(p) not in before or p.stat().st_mtime > before.get(str(p), 0) + 0.5
                ]
            except Exception:
                fresh = []
            pdfs = sorted(fresh or [], key=lambda p: p.stat().st_mtime, reverse=True)
            if not pdfs:
                # 兜底：万一时间戳没变（同一秒完成），取最新的一个
                try:
                    allp = sorted(out_dir.glob("*.pdf"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)
                    pdfs = allp[:1]
                except Exception:
                    pdfs = []
            if pdfs:
                # 按用户设定重命名（留空则用 pdf2zh 默认命名）
                try:
                    renamed = rename_outputs(pdfs, pdf, cfg)
                    if renamed and any(r != p for r, p in zip(renamed, pdfs)):
                        pdfs = renamed + [p for p in pdfs if p not in renamed]
                except Exception:
                    pass
                on_progress(100.0, "完成")
                log("")
                # 缓存效果（第二次翻同一文件会快很多）
                try:
                    _c = cache_stat.get("calls", 0)
                    _h = cache_stat.get("hits", 0)
                    if _c or _h:
                        _tot = _c + _h
                        if _h and _tot:
                            log("⚡ 缓存命中 %d/%d 段（省去 %.0f%% 的翻译请求）"
                                % (_h, _tot, _h * 100.0 / _tot))
                        else:
                            log("本次共翻译 %d 段（无缓存命中）" % _c)
                except Exception:
                    pass
                for f in pdfs[:3]:
                    log("✅ 已生成：" + f.name)

                # Word 导出功能已移除（用户要求）——
                # 需要 Word 版时用「用 Microsoft Office 打开」，由 Word 自己转换。
                word_files: list[str] = []

                # ---- 质量检查（若开启）----
                quality: list[dict] = []
                if cfg.get("quality_check"):
                    try:
                        import learn_check
                        for f in pdfs[:3]:
                            quality += learn_check.check_quality(pdf, f)
                        if quality:
                            log("")
                            log("🔍 质量检查发现 %d 处可疑：" % len(quality))
                            from collections import Counter as _C
                            for k, n in _C(q["kind"] for q in quality).most_common():
                                log("   · %s：%d 处" % (
                                    {"untranslated": "疑似漏翻",
                                     "english_left": "英文残留",
                                     "number_mismatch": "数字可能丢失",
                                     "control_char": "控制字符（不影响阅读）",
                                     "duplicate": "重复段落"}.get(k, k), n))
                        else:
                            log("🔍 质量检查：未发现明显问题")
                    except Exception as exc:
                        log("⚠️ 质量检查失败：" + str(exc)[:80])

                # ---- 术语命中报告 ----
                term_hits: dict = {}
                try:
                    import glossary_hook
                    term_hits = glossary_hook.get_term_hits() or {}
                    if not term_hits:
                        # 子进程写文件时也带上了
                        st2 = glossary_hook.read_stats_file()
                        term_hits = st2.get("term_hits") or {}
                    # 术语命中【不再写日志】—— 界面上有独立的「术语表命中」面板。
                    # 这里只把统计结果挂到 timing_out，随 done 事件传给界面。
                    if term_hits:
                        try:
                            _n_terms = glossary_hook.load_glossary()
                        except Exception:
                            _n_terms = 0
                        timing_out["term_hits"] = dict(term_hits)
                        timing_out["term_total"] = int(_n_terms or 0)
                    elif cfg.get("glossary_enabled", True):
                        timing_out["term_hits"] = {}
                        timing_out["term_total"] = 0
                except Exception:
                    pass

                # ---- 记录历史 ----
                try:
                    import datetime
                    add_history({
                        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "source": str(pdf),
                        "source_name": pdf.name,
                        "files": [str(f) for f in pdfs[:3]],
                        "word_files": word_files,
                        "issues": [q["kind"] for q in quality][:20],
                        "out_dir": str(out_dir),
                        "engine": str(cfg.get("engine")),
                        "mode": mode,
                    })
                except Exception:
                    pass
                # 阶段耗时【不再写日志】—— 界面上有独立的「步骤耗时」面板，
                # 日志里重复一遍纯属干扰（用户反馈）。
                # 数据仍通过 on_done 的 timing 传给界面。
                on_done(True, pdfs[0], "", dict(timing_out))
                return True, ""
            else:
                log("")
                log("⚠️ 命令结束但没有生成 PDF，请检查上面的日志。")
                return False, "命令结束但没有生成 PDF"
        else:
            _err = failed_msg or ("退出码 %d" % code)
            log("")
            log("❌ 翻译失败" + ("：" + failed_msg if failed_msg else "（退出码 %d）" % code))
            return False, _err
    except Exception as exc:
        log("")
        log("❌ 发生错误：" + str(exc))
        log(traceback.format_exc())
        return False, str(exc)


# ---------------------------------------------------------------- 连接测试
def test_engine_connection(engine: str, api_key: str = "", base_url: str = "",
                           model: str = "", timeout: int = 20) -> tuple[bool, str]:
    """测试翻译引擎是否可用。返回 (是否成功, 说明)。"""
    import urllib.request
    import urllib.error

    engine = (engine or "bing").strip().lower()
    try:
        if engine == "bing":
            req = urllib.request.Request("https://cn.bing.com",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return True, "Bing 可达（HTTP %d）" % r.status
        if engine == "google":
            req = urllib.request.Request("https://translate.google.com",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return True, "Google 可达（HTTP %d）" % r.status

        # 需要 Key 的引擎：查各自端点
        endpoints = {
            "deepseek": "https://api.deepseek.com/v1/models",
            "siliconflow": "https://api.siliconflow.cn/v1/models",
            "aliyundashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4/models",
            "openai": "https://api.openai.com/v1/models",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
            "modelscope": "https://api-inference.modelscope.cn/v1/models",
            "openaicompatible": (base_url or "").rstrip("/") + "/models",
        }
        url = endpoints.get(engine)
        if not url:
            return False, "该引擎暂不支持自动检测，请直接试翻一小段"
        headers = {"User-Agent": "Mozilla/5.0"}
        if api_key:
            if engine == "gemini":
                url = url + "?key=" + api_key
            else:
                headers["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return True, "连接成功（HTTP %d）" % r.status
        except urllib.error.HTTPError as he:
            # 401/403 说明网络通、Key 不对；其它也算网络可达
            if he.code in (401, 403):
                return False, "网络可达，但 API Key 无效（HTTP %d）" % he.code
            return True, "网络可达（HTTP %d）" % he.code
    except Exception as exc:
        return False, "连接失败：%s" % str(exc)[:110]


# ---------------------------------------------------------------- 打开方式
_WPS_CACHE: dict = {"path": None, "at": 0.0}


def _find_wps(force: bool = False) -> str:
    """查找 WPS 的启动程序。

    性能注意：早期版本用 glob(\"**\") 递归扫描安装目录，实测要 2.3 秒，
    每次打开设置都会卡一下。改为「注册表 + 常见路径 + 浅层扫描」，
    实测 5 毫秒内返回，结果还做了缓存。
    """
    import time as _t
    if not force and _WPS_CACHE["path"] is not None:
        if _t.time() - _WPS_CACHE["at"] < 600:      # 缓存 10 分钟
            return _WPS_CACHE["path"]

    found = ""

    # 1) 注册表（最快、最准）
    try:
        import winreg
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Kingsoft\\Office\\6.0\\Common"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Kingsoft\\Office\\6.0\\Common"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for name in ("InstallRoot", "Path", "InstallPath"):
                        try:
                            v, _ = winreg.QueryValueEx(k, name)
                            if v:
                                for exe in ("ksolaunch.exe", "wps.exe"):
                                    p = os.path.join(str(v), exe)
                                    if os.path.isfile(p):
                                        found = p
                                        break
                                    p2 = os.path.join(str(v), "office6", exe)
                                    if os.path.isfile(p2):
                                        found = p2
                                        break
                            if found:
                                break
                        except OSError:
                            continue
            except OSError:
                continue
            if found:
                break
    except Exception:
        pass

    # 2) 常见安装路径（不递归，只查固定层级）
    if not found:
        bases = [
            r"D:\\WPS\\WPS Office", r"C:\\WPS\\WPS Office",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kingsoft", "WPS Office"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kingsoft", "WPS Office", "office6"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Kingsoft", "WPS Office"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Kingsoft", "WPS Office"),
        ]
        for b in bases:
            if not b or not os.path.isdir(b):
                continue
            for exe in ("ksolaunch.exe", "wps.exe"):
                p = os.path.join(b, exe)
                if os.path.isfile(p):
                    found = p
                    break
                p2 = os.path.join(b, "office6", exe)
                if os.path.isfile(p2):
                    found = p2
                    break
            if found:
                break

    # 3) 兜底：浅层列一层子目录（版本号目录），不做全递归
    if not found:
        import glob as _g
        for b in (r"D:\\WPS\\WPS Office", r"C:\\WPS\\WPS Office"):
            if os.path.isdir(b):
                for exe in ("ksolaunch.exe", "wps.exe"):
                    hits = _g.glob(os.path.join(b, "*", exe)) + _g.glob(os.path.join(b, "*", "office6", exe))
                    hits = [h for h in hits if os.path.isfile(h)]
                    if hits:
                        hits.sort(reverse=True)
                        found = hits[0]
                        break
            if found:
                break

    _WPS_CACHE["path"] = found
    _WPS_CACHE["at"] = _t.time()
    return found


def _word_has_document() -> bool:
    """检查 Word 里是否真的加载了文档（而不是停在起始页）。

    做法：枚举 WINWORD 进程的窗口，看标题是否还是 "Word" / "Microsoft Word"。
    加载了文档时标题会带文件名。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetWindowTextW = user32.GetWindowTextW

        # 取 WINWORD 的 pid
        # 注意：tasklist 在中文 Windows 上输出是 GBK，用 text=True 会解码失败
        pids = set()
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE",
                                "/FO", "CSV"],
                               capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            raw = r.stdout or b""
            try:
                txt = raw.decode("gbk", errors="replace")
            except Exception:
                txt = raw.decode("utf-8", errors="replace")
            for line in txt.splitlines()[1:]:
                parts = line.split('","')
                if len(parts) > 1:
                    try:
                        pids.add(int(parts[1]))
                    except Exception:
                        pass
        except Exception:
            return False
        if not pids:
            return False

        found = {"loaded": False}
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                      wintypes.LPARAM)

        # 只认【主窗口】：Word 还有一堆隐藏的辅助窗口
        # （HardwareMonitorWindow / DDE Server Window / OfficePowerManagerWindow…），
        # 按"标题长度 > 6"判断会被它们骗到，导致看门狗永远认为"已打开"。
        # 主窗口的类名是 OpusApp，且一定是可见的。
        GetClassNameW = user32.GetClassNameW

        def cb(hwnd, _lp):
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            GetClassNameW(hwnd, cls, 256)
            if cls.value != "OpusApp":       # 只认 Word 主窗口
                return True
            buf = ctypes.create_unicode_buffer(512)
            GetWindowTextW(hwnd, buf, 512)
            t = (buf.value or "").strip()
            low = t.lower()
            # 主窗口标题 = "Word" → 还在起始页/空文档
            if low in ("word", "microsoft word"):
                return True
            # "正在打开 - Word" 说明还在转换
            if "正在打开" in t or "opening" in low:
                return True
            # 其余（带文件名 / 带 - Word 后缀）视为已加载
            if t.endswith("- Word") or ".docx" in low or ".pdf" in low:
                found["loaded"] = True
                return False
            if len(t) > 4:
                found["loaded"] = True
                return False
            return True

        user32.EnumWindows(EnumProc(cb), 0)
        return bool(found["loaded"])
    except Exception:
        return False


def _dismiss_word_safe_mode() -> bool:
    """自动点掉 Word 的「安全模式」询问框（选"否"）。

    实测踩过的坑：Word 上次异常退出后，启动时会弹出
    「Word 上次启动失败。安全模式可以帮助您解决问题…是否要在安全模式中启动？」
    这个框会一直挡着，用户看到的现象就是"Office 打不开 PDF"。
    如果选了"是"，进入安全模式后 PDF 转换功能直接被禁用 —— 更打不开。
    所以这里主动找到该对话框并点「否」。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        BM_CLICK = 0x00F5

        # 取 Word 的 pid
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE",
                            "/FO", "CSV"],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        raw = r.stdout or b""
        try:
            txt = raw.decode("gbk", errors="replace")
        except Exception:
            txt = raw.decode("utf-8", errors="replace")
        pids = set()
        for line in txt.splitlines()[1:]:
            parts = line.split('","')
            if len(parts) > 1:
                try:
                    pids.add(int(parts[1]))
                except Exception:
                    pass
        if not pids:
            return False

        dialogs = []
        EP = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(h, _l):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(h):
                cls = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(h, cls, 128)
                if cls.value == "#32770":       # 标准对话框类
                    dialogs.append(h)
            return True
        user32.EnumWindows(EP(cb), 0)

        for h in dialogs:
            kids = []
            EP2 = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                     wintypes.LPARAM)

            def cb2(c, _l):
                cls = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(c, cls, 128)
                tx = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(c, tx, 512)
                kids.append((c, cls.value, tx.value))
                return True
            user32.EnumChildWindows(h, EP2(cb2), 0)

            statics = [k[2] for k in kids if k[1] == "Static"]
            is_safe = any(("安全模式" in s) or ("Safe Mode" in s)
                          for s in statics)
            if not is_safe:
                continue
            for c, cls, t in kids:
                if cls == "Button" and (t.startswith("否") or t.startswith("No")):
                    user32.SendMessageW(c, BM_CLICK, 0, 0)
                    return True
        return False
    except Exception:
        return False


def _office_pdf_watchdog(exe: str, pdf_path: str, timeout: float = 30.0) -> None:
    """看门狗：Word 若在 timeout 秒内没能打开 PDF，就转 docx 再开。

    为什么超时要给到 30 秒：实测 Word 的 PDF 重排本来就要 14~25 秒
    （把 PDF 版面重建为可编辑文档，很吃 CPU）。之前设 16 秒太短，
    会在 Word 正要打开时误判失败、白转一次 docx。

    为什么需要回退：少数情况下 PDF 转换确实失败（组件损坏、
    安全模式等），Word 会一直停在起始页且不报错。与其让用户干等，
    不如自动换一条一定能走通的路（转 docx，1 秒内打开）。
    """
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.0)
        # 每次循环先尝试点掉可能挡住 Word 的安全模式对话框
        _dismiss_word_safe_mode()
        if _word_has_document():
            return          # 正常打开了，什么都不用做
    # 超时了 —— 检查 Word 是否还活着（用户可能已关掉）
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE"],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        raw = r.stdout or b""
        try:
            txt = raw.decode("gbk", errors="replace")
        except Exception:
            txt = raw.decode("utf-8", errors="replace")
        if "WINWORD.EXE" not in txt:
            return
    except Exception:
        return
    # 还在起始页 → 再补点一次安全模式框（有些机器会晚几秒才弹）
    _dismiss_word_safe_mode()


def _find_office() -> str:
    """找 Microsoft Office（Word）。

    注意：Office 没有单个"打开 PDF"的通用入口，
    用 WINWORD.EXE 最稳（Word 能直接开 PDF 并转成可编辑文档）。
    """
    import glob as _glob
    cands = []
    # 常见安装位置（含版本号目录）
    for base in (r"C:\Program Files\Microsoft Office",
                 r"C:\Program Files (x86)\Microsoft Office"):
        if os.path.isdir(base):
            cands += _glob.glob(os.path.join(base, "root", "Office*", "WINWORD.EXE"))
            cands += _glob.glob(os.path.join(base, "Office*", "WINWORD.EXE"))
    for c in cands:
        if os.path.isfile(c):
            return c
    # 注册表兜底
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE") as k:
                    p = winreg.QueryValueEx(k, "")[0]
                    if p and os.path.isfile(p):
                        return p
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _find_edge() -> str:
    """找 Microsoft Edge 的可执行文件。"""
    import glob as _glob
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    # 注册表兜底
    try:
        import winreg
        for hive, key in (
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    p = winreg.QueryValueEx(k, "")[0]
                    if p and os.path.isfile(p):
                        return p
            except Exception:
                continue
    except Exception:
        pass
    # 最后试一下浅层搜索
    for b in (r"C:\Program Files (x86)\Microsoft\Edge\Application",
              r"C:\Program Files\Microsoft\Edge\Application"):
        if os.path.isdir(b):
            hits = _glob.glob(os.path.join(b, "msedge.exe"))
            if hits:
                return hits[0]
    return ""


def open_file_with(path: Path, cfg: dict) -> str:
    """按用户设置打开文件。返回实际使用的方式（用于提示）。"""
    mode = str(cfg.get("open_with") or "auto").strip().lower()
    custom = str(cfg.get("open_with_path") or "").strip()

    if mode == "wps":
        exe = custom or _find_wps()
        if exe and Path(exe).exists():
            subprocess.Popen([exe, str(path)], creationflags=0x08000000)
            return "WPS"
        # 找不到 WPS 就退回系统默认
    elif mode == "edge":
        exe = _find_edge()
        if exe:
            try:
                subprocess.Popen([exe, str(path)], creationflags=0x08000000)
                return "Microsoft Edge"
            except Exception:
                pass
        # 找不到 Edge 就退回系统默认
    elif mode in ("office", "office_pdf"):
        exe = _find_office()
        if exe:
            # 先用 Word 直接打开 PDF（Office 自带 PDF 重排能力）。
            # 但实测部分机器上 PDFREFLOW 组件是坏的：Word 会一直停在
            # 起始页，30 秒也打不开，且不报任何错。
            # 所以后台起一个看门狗：若超时仍未加载文档，就转成 docx 再打开。
            try:
                subprocess.Popen([exe, str(path)], creationflags=0x08000000)
            except Exception:
                pass
            if str(path).lower().endswith(".pdf"):
                try:
                    import threading
                    threading.Thread(
                        target=_office_pdf_watchdog,
                        args=(exe, str(path), 16.0),
                        daemon=True).start()
                    return "Microsoft Office"
                except Exception:
                    pass
            return "Microsoft Office"
        # 找不到 Office 就退回系统默认
    elif mode == "custom" and custom:
        exe = custom
        if Path(exe).exists():
            subprocess.Popen([exe, str(path)], creationflags=0x08000000)
            return Path(exe).name
    elif mode == "folder":
        os.startfile(str(path.parent))  # type: ignore[attr-defined]
        return "文件夹"

    os.startfile(str(path))  # type: ignore[attr-defined]
    return "系统默认"


# ---------------------------------------------------------------- 共享状态
# 界面重建时要保留的临时状态（主题热切换用）
_UI_STATE: dict = {}

# 界面「代次」：每次重建界面自增。
# 旧的 root.after 循环（进度轮询 pump、步骤计时等）仍在排队执行时，
# 一旦访问已销毁的控件就会报 "invalid command name ..."。
# 让每个循环记住自己的代次，发现变了就自己停下。
_BUILD_GEN = {"n": 0}

# 最近一次进度事件里的页数信息（stage_current / stage_total）。
# run_translate 每收到一条 progress 事件就刷新它，
# 界面用它显示「第 N/M 页」。
_PROG_EXTRA: dict = {}

# 进度落盘的节流计时（避免每条进度事件都写文件）
PROG_SAVE_AT: dict = {"t": 0.0}



# ================================================================
# 队列持久化（关掉软件不丢任务）
# ================================================================
QUEUE_FILE = APP_DIR / "queue.json"
PROGRESS_FILE = APP_DIR / "progress.json"


def load_queue() -> list[dict]:
    """读取上次未完成的批量队列。"""
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out = []
        for it in data:
            if isinstance(it, dict) and it.get("path"):
                out.append(it)
        return out
    except Exception:
        return []


def save_queue(items: list[dict]) -> bool:
    """保存批量队列（原子写，避免写一半断电）。"""
    try:
        payload = [{"path": str(it.get("path") or ""),
                    "name": str(it.get("name") or ""),
                    "done": bool(it.get("done")),
                    "error": str(it.get("error") or "")}
                   for it in (items or []) if it.get("path")]
        tmp = QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(QUEUE_FILE)
        return True
    except Exception:
        return False


def clear_queue() -> None:
    try:
        QUEUE_FILE.unlink()
    except Exception:
        pass


# ================================================================
# 断点续译（记录哪些页已经翻完）
# ================================================================

def _progress_key(pdf_path) -> str:
    """用「路径 + 文件大小 + 修改时间」作为指纹。

    只认路径不够 —— 同一个路径的文件被替换后，旧进度就失效了。
    """
    try:
        st = Path(pdf_path).stat()
        raw = "%s|%d|%d" % (Path(pdf_path).resolve(), st.st_size,
                            int(st.st_mtime))
    except Exception:
        raw = str(pdf_path)
    import hashlib
    return hashlib.md5(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _load_progress_db() -> dict:
    try:
        d = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_progress_db(db: dict) -> None:
    try:
        if len(db) > 20:
            keys = sorted(db, key=lambda k: db[k].get("updated", 0))
            for k in keys[:-20]:
                db.pop(k, None)
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(PROGRESS_FILE)
    except Exception:
        pass


def save_progress(pdf_path, done_pages: list[int], total_pages: int,
                  out_pdf: str = "", completed: bool = False) -> None:
    """记录某份 PDF 已经翻完的页码（1 起）。

    completed=True 表示「整份翻完了」，下次不再提示续译。
    翻译过程中保存时应传 False，这样中断后还能接着翻。
    """
    try:
        import time as _t
        db = _load_progress_db()
        k = _progress_key(pdf_path)
        pages = sorted(set(int(x) for x in (done_pages or []) if int(x) > 0))
        total = int(total_pages or 0)
        db[k] = {
            "path": str(Path(pdf_path).resolve()),
            "name": Path(pdf_path).name,
            "done_pages": pages,
            "total": total,
            "out_pdf": str(out_pdf or ""),
            "completed": bool(completed) or (total > 0 and len(pages) >= total),
            "updated": _t.time(),
        }
        _save_progress_db(db)
    except Exception:
        pass


def get_progress(pdf_path) -> dict | None:
    """查这份 PDF 的断点。

    返回 None 表示「不需要续译」，包括：
      · 没有记录
      · 已标记为 completed（上次完全翻完了）
      · 已完成页数 >= 总页数
    """
    try:
        db = _load_progress_db()
        rec = db.get(_progress_key(pdf_path))
        if not rec:
            return None
        if rec.get("completed"):
            return None
        done = rec.get("done_pages") or []
        total = int(rec.get("total") or 0)
        if not done:
            return None
        if total and len(done) >= total:
            return None
        return rec
    except Exception:
        return None


def clear_progress(pdf_path) -> None:
    try:
        db = _load_progress_db()
        db.pop(_progress_key(pdf_path), None)
        _save_progress_db(db)
    except Exception:
        pass


def count_pdf_pages(pdf_path) -> int:
    """PDF 总页数（读不到就返回 0）。"""
    try:
        import fitz
        with fitz.open(str(pdf_path)) as d:
            return int(d.page_count)
    except Exception:
        return 0


def expand_pages_spec(spec: str, total: int = 0) -> list[int]:
    """把 "1-10,15,20-" 展开成页码列表。

    total 给定时，"20-" 会一直展开到 total（含）。
    total 为 0 时，开放区间退化成单页（因为不知道末尾在哪）。
    """
    if not spec or not str(spec).strip():
        return []
    out = []
    for part in str(spec).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a, b = a.strip(), b.strip()
            try:
                lo = int(a) if a else 1
                if b:
                    hi = int(b)
                else:
                    # 开放区间：有总页数就展开到底，否则只取起点
                    hi = int(total) if total else lo
                for x in range(min(lo, hi), max(lo, hi) + 1):
                    if x > 0:
                        out.append(x)
            except Exception:
                continue
        else:
            try:
                v = int(part)
                if v > 0:
                    out.append(v)
            except Exception:
                continue
    if total:
        out = [x for x in out if x <= int(total)]
    return sorted(set(out))


def list_all_progress() -> list[dict]:
    """列出所有未翻完的任务（供启动时提示用户继续）。"""
    out = []
    try:
        db = _load_progress_db()
    except Exception:
        return out
    for key, rec in (db or {}).items():
        try:
            if not isinstance(rec, dict):
                continue
            if rec.get("completed"):
                continue          # 已翻完，不用提示
            path = rec.get("path") or key
            done = rec.get("done_pages") or []
            total = int(rec.get("total") or 0)
            if not total or not done:
                continue
            if len(done) >= total:
                continue          # 页数齐了，算完成
            if not Path(path).exists():
                continue          # 源文件没了，提示也没意义
            out.append({
                "path": path,
                "name": Path(path).name,
                "done": len(done),
                "total": total,
                "done_pages": done,
                "rest": remaining_pages(total, done),
                "updated": rec.get("updated") or 0,
            })
        except Exception:
            continue
    # 最近改动的排前面
    out.sort(key=lambda x: -(x.get("updated") or 0))
    return out


def pages_to_spec(pages: list[int]) -> str:
    """把页码列表压成 babeldoc 认的 "1-10,15,20-" 形式。"""
    if not pages:
        return ""
    nums = sorted(set(int(p) for p in pages if int(p) > 0))
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append("%d" % start if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    parts.append("%d" % start if start == prev else "%d-%d" % (start, prev))
    return ",".join(parts)


def remaining_pages(total: int, done: list[int]) -> str:
    """算「还没翻的页码」并压成 spec。"""
    if not total:
        return ""
    done_set = set(int(x) for x in (done or []))
    todo = [i for i in range(1, int(total) + 1) if i not in done_set]
    return pages_to_spec(todo)


# ================================================================
# ETA：按阶段权重估算剩余时间
# ================================================================
#
# 单纯用「已用 / 已完成」线性外推很不准，因为各阶段速度差很大：
#   解析版面慢、翻译段落快、排版中等。
# 这里给每个阶段一个典型占比，再结合已完成的实际耗时反推。
STAGE_WEIGHT = {
    "parse pdf": 0.08,
    "detectscannedfile": 0.01,
    "parse page layout": 0.30,
    "parse paragraphs": 0.06,
    "parse formulas": 0.05,
    "translate paragraphs": 0.36,
    "typesetting": 0.08,
    "add fonts": 0.02,
    "generate pdf": 0.04,
}


def _stage_weight(name: str) -> float:
    n = str(name or "").lower()
    for k, w in STAGE_WEIGHT.items():
        if k in n:
            return w
    return 0.05


def estimate_eta(elapsed: float, percent: float, stage: str = "",
                 done_stages: list | None = None) -> float | None:
    """估算剩余秒数。返回 None 表示样本太少。

    elapsed      已用秒数
    percent      当前整体进度 0~100
    stage        当前阶段名
    done_stages  已完成的阶段列表（每项含 name）
    """
    try:
        elapsed = float(elapsed or 0)
        percent = max(0.0, min(100.0, float(percent or 0)))
        if elapsed < 3 or percent < 2:
            return None
        if percent >= 99.5:
            return 0.0

        w_done = 0.0
        for r in (done_stages or []):
            w_done += _stage_weight(r.get("name") if isinstance(r, dict) else "")
        w_now = _stage_weight(stage)

        w_total = sum(STAGE_WEIGHT.values())
        if w_done > 0.05:
            sec_per_w = elapsed / max(w_done + w_now * 0.5, 1e-6)
            remain_w = max(w_total - w_done - w_now * 0.5, 0.0)
            eta_by_weight = sec_per_w * remain_w
        else:
            eta_by_weight = None

        eta_linear = elapsed * (100.0 - percent) / percent
        if eta_by_weight is None:
            return max(0.0, eta_linear)

        # 几何平均：任一模型跑偏时结果不会太离谱
        v = (max(eta_by_weight, 1e-6) * max(eta_linear, 1e-6)) ** 0.5
        return max(0.0, min(v, 24 * 3600.0))
    except Exception:
        return None


def fmt_duration(sec: float | None) -> str:
    """把秒数格式化成「1 分 23 秒」。"""
    if sec is None:
        return "估算中"
    try:
        s = int(round(float(sec)))
    except Exception:
        return "估算中"
    if s < 0:
        s = 0
    if s < 60:
        return "%d 秒" % s
    m, s2 = divmod(s, 60)
    if m < 60:
        return "%d 分 %d 秒" % (m, s2)
    h, m2 = divmod(m, 60)
    return "%d 小时 %d 分" % (h, m2)


# ================================================================
# 诊断包（出错时一键收集环境信息）
# ================================================================

_SENSITIVE_KEYS = ("api_key", "base_url", "output_dir", "glossary_file",
                   "open_with_path", "model")


def _mask_path(p) -> str:
    """路径只保留最后两段，避免暴露用户名。"""
    try:
        parts = Path(str(p)).parts
        return str(Path(*parts[-2:])) if len(parts) >= 2 else "***"
    except Exception:
        return "***"


def build_diagnostic(log_text: str = "", out_path=None):
    """生成诊断包（纯文本），返回文件路径。

    内容：运行环境 + 脱敏配置 + 最近日志尾部 + 关键文件存在性。
    不含 API Key，路径只留末两段。
    """
    try:
        import platform
        import sys as _sys
        import datetime

        lines = []
        add = lines.append
        add("PDF 翻译工具 · 诊断报告")
        add("=" * 46)
        add("生成时间: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        add("")

        add("【运行环境】")
        add("  Python   : " + _sys.version.split()[0])
        add("  平台     : " + platform.platform())
        add("  架构     : " + (platform.machine() or "?"))
        add("  程序目录 : " + _mask_path(APP_DIR))
        try:
            import fitz
            add("  PyMuPDF  : 已安装")
        except Exception:
            add("  PyMuPDF  : 未安装")
        try:
            import babeldoc
            add("  babeldoc : " + str(getattr(babeldoc, "__version__", "?")))
        except Exception:
            add("  babeldoc : 未安装")
        add("")

        add("【关键文件】")
        for f in ("core.py", "app_sidebar.py", "worker.py", "glossary.csv",
                  "glossary_hook.py", "pdf_studio.py", "config.json"):
            fp = APP_DIR / f
            add("  %-18s %s" % (f, "OK" if fp.exists() else "缺失"))
        for d in ("cache", "runtime"):
            dp = APP_DIR / d
            add("  %-18s %s" % (d + "/", "存在" if dp.exists() else "缺失"))
        add("")

        add("【配置（已脱敏）】")
        try:
            cfg = load_config()
            for k in sorted(cfg):
                if k.startswith("_"):
                    continue
                v = cfg[k]
                if k in _SENSITIVE_KEYS:
                    v = "***" if v else ""
                elif isinstance(v, (dict, list)):
                    v = "<%d 项>" % len(v)
                elif any(t in k.lower() for t in ("dir", "path", "file")):
                    v = _mask_path(v) if v else ""
                add("  %-22s %s" % (k, v))
        except Exception as exc:
            add("  读取配置失败: %s" % exc)
        add("")

        add("【术语表】")
        try:
            import glossary_hook as _gh
            add("  词条数: %d" % _gh.load_glossary())
            st = _gh.read_stats_file()
            add("  上次统计: calls=%s hits=%s" % (st.get("calls"), st.get("hits")))
        except Exception as exc:
            add("  读取失败: %s" % exc)
        add("")

        add("【最近日志（末尾 120 行）】")
        tail = [l for l in (log_text or "").splitlines() if l.strip()][-120:]
        if tail:
            lines.extend("  " + l for l in tail)
        else:
            add("  （无）")
        add("")
        add("=" * 46)
        add("反馈问题时请把这个文件一起发出（已自动去除密钥）。")

        dest = Path(out_path) if out_path else (
            APP_DIR / ("diagnostic-%s.txt" %
                       datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        dest.write_text("\n".join(lines), encoding="utf-8")
        return dest
    except Exception:
        return None



# ================================================================
# 错误翻译：把技术报错变成用户能看懂、能处理的话
# ================================================================
#
# 用户看到的原始报错往往是：
#   "❌ 发生错误：[WinError 2] 系统找不到指定的文件。"
#   "ConnectionError: HTTPSConnectionPool(host='cn.bing.com', port=443)"
# 他不知道这是缺文件、网络问题、还是被限流，也不知道下一步该干什么。
# 这里按关键字归类，给一条【能照着做】的建议。

_ERROR_RULES = [
    # (匹配关键字, 标题, 建议, 是否可自助解决)
    (("winerror 2", "系统找不到指定的文件", "filenotfounderror"),
     "缺少必要的文件",
     "通常是解压不完整，或被杀毒软件隔离了文件。\n\n"
     "请依次确认：\n"
     "1. 压缩包是【完整解压】的，不是只拖出了几个文件\n"
     "2. 程序目录下有 runtime\\ 文件夹，里面有 python.exe\n"
     "3. 杀毒软件没有把 runtime 或 cache 目录隔离\n\n"
     "如果都正常，请点「设置 → 关于 → 生成诊断报告」发给我。",
     True),

    (("connectionerror", "connection refused", "max retries",
      "failed to establish", "ssl", "certificate"),
     "网络连接失败",
     "翻译需要联网（PDF 编辑、术语表等功能不需要）。\n\n"
     "请确认：\n"
     "1. 能正常打开 bing.com\n"
     "2. 如果用了代理，确认代理软件正在运行\n"
     "3. 公司网络/校园网可能拦截，试试切换网络\n\n"
     "也可以在「设置 → 翻译引擎」里换一个引擎试试。",
     True),

    (("timeout", "timed out", "read timed out"),
     "请求超时",
     "网络太慢或对方服务器响应慢。\n\n"
     "建议：\n"
     "1. 把「设置 → 性能 → 并发数」从 20 降到 8~10\n"
     "2. 稍等一会儿重试（免费引擎偶尔会慢）\n"
     "3. 换用其他引擎",
     True),

    (("429", "rate limit", "too many requests", "quota"),
     "请求太频繁，被限流了",
     "翻译接口限制了请求速度。\n\n"
     "建议：\n"
     "1. 「设置 → 性能 → 并发数」调到 5~8\n"
     "2. 等待 1~2 分钟再重试\n"
     "3. 如果用的是需要 Key 的引擎，检查账户额度是否用完",
     True),

    (("401", "403", "unauthorized", "invalid api key", "authentication"),
     "API Key 无效或没有权限",
     "请检查「设置 → 翻译引擎」里的 API Key：\n\n"
     "1. 是否粘贴完整（前后没有多余空格）\n"
     "2. 是否已过期或被禁用\n"
     "3. 该 Key 是否有此模型的权限\n\n"
     "免费引擎（Bing / Google）不需要 Key，可以先用它们。",
     True),

    (("permission denied", "access is denied", "拒绝访问"),
     "文件被占用或没有写入权限",
     "可能的原因：\n\n"
     "1. 输出的 PDF 正在被其他程序打开（比如 PDF 阅读器）\n"
     "   → 关掉它再重试\n"
     "2. 保存位置是只读的（如 Program Files）\n"
     "   → 「设置 → 输出」换一个位置，比如「文档」目录\n"
     "3. 杀毒软件拦截了写入",
     True),

    (("no space", "磁盘空间", "disk full"),
     "磁盘空间不足",
     "请清理磁盘后重试。\n\n"
     "翻译一个 100 页的 PDF 大约需要 50~200 MB 临时空间。",
     True),

    (("memoryerror", "out of memory", "内存"),
     "内存不足",
     "文档太大或并发太高。\n\n"
     "建议：\n"
     "1. 「设置 → 性能 → 并发数」降到 5 左右\n"
     "2. 关掉其他占用内存的程序\n"
     "3. 超大文档可以先用「指定页码」分批翻译",
     True),

    (("encrypted", "password", "加密"),
     "PDF 已加密",
     "这份 PDF 设置了打开密码，无法直接翻译。\n\n"
     "请先用其他工具去掉密码保护，再试一次。",
     True),

    (("no such file", "文件不存在"),
     "找不到要翻译的文件",
     "文件可能被移动、重命名或删除了。\n\n"
     "请重新选择文件。如果是批量翻译，队列里失效的文件会被自动跳过。",
     True),
]


def explain_error(err) -> dict:
    """把报错文本归类，返回 {title, advice, solvable, matched}。

    没匹配到任何规则时，返回一个通用建议（含原始报错）。
    """
    raw = str(err or "").strip()
    low = raw.lower()
    for keys, title, advice, solvable in _ERROR_RULES:
        for k in keys:
            if k in low:
                return {"title": title, "advice": advice,
                        "solvable": solvable, "matched": k,
                        "raw": raw}
    return {
        "title": "翻译失败",
        "advice": "这是一次未预期的错误。\n\n"
                  "可以先试：\n"
                  "1. 重试一次（偶发问题通常重试就好）\n"
                  "2. 「设置 → 性能」把并发数调低\n"
                  "3. 换一个翻译引擎\n\n"
                  "如果反复出现，请点「设置 → 关于 → 生成诊断报告」，"
                  "把生成的 txt 发给我，里面已自动去掉密钥。",
        "solvable": False,
        "matched": "",
        "raw": raw,
    }



# ================================================================
# 翻译前预检：在动手之前就把「必然失败」的情况拦下来
# ================================================================
#
# 这几种情况以前要跑到一半才失败，用户白等几分钟：
#   · 源文件被移动/删除
#   · PDF 有打开密码
#   · 保存目录不可写（只读盘、Program Files）
# 现在开头几毫秒就能判断，直接给出能照着做的提示。

def check_source_pdf(pdf) -> dict:
    """检查源 PDF 是否可翻译。

    返回 {"ok": bool, "title": str, "advice": str}
    """
    try:
        p = Path(str(pdf))
    except Exception:
        return {"ok": False, "title": "路径无效",
                "advice": "这个路径无法解析，请重新选择文件。"}

    if not p.exists():
        return {"ok": False, "title": "找不到文件",
                "advice": "文件可能已被移动、重命名或删除。\n\n"
                          "请确认它还在原来的位置，或重新选择。"}
    if not p.is_file():
        return {"ok": False, "title": "不是文件",
                "advice": "拖入的是文件夹。\n\n"
                          "请拖入 PDF 文件，或先展开文件夹选择里面的 PDF。"}
    if p.suffix.lower() != ".pdf":
        return {"ok": False, "title": "不是 PDF",
                "advice": "只能翻译 PDF 文件（当前是 %s）。" % (p.suffix or "无扩展名")}

    # 加密检测（PyMuPDF 的 needs_pass 是毫秒级的）
    try:
        import fitz
        with fitz.open(str(p)) as d:
            if getattr(d, "needs_pass", False):
                return {"ok": False, "title": "PDF 有打开密码",
                        "advice": "这份 PDF 设置了密码保护，无法直接翻译。\n\n"
                                  "请先用其他工具（如 PDF 阅读器的「另存为」）"
                                  "去掉密码，再试一次。"}
            pages = int(d.page_count)
            if pages <= 0:
                return {"ok": False, "title": "PDF 没有页面",
                        "advice": "这个文件看起来是空的，或已损坏。"}
    except Exception:
        # 打不开就交给后续流程报具体错误，这里不阻断
        return {"ok": True, "title": "", "advice": ""}

    return {"ok": True, "title": "", "advice": "", "pages": pages}


def check_output_dir(out_dir) -> dict:
    """检查保存目录是否可写（真正写一个探针文件验证）。"""
    try:
        d = Path(str(out_dir))
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".pdf2zh_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "title": "", "advice": ""}
    except PermissionError:
        return {"ok": False, "title": "保存位置没有写入权限",
                "advice": "这个目录不允许写入（常见于 C:\\Program Files）。\n\n"
                          "请在「设置 → 输出」里换一个位置，"
                          "比如「文档」或桌面。"}
    except OSError as exc:
        return {"ok": False, "title": "保存位置不可用",
                "advice": "无法在这个目录创建文件：%s\n\n"
                          "请换一个保存位置，或检查磁盘是否已满。" % exc}
    except Exception as exc:
        return {"ok": False, "title": "保存位置不可用",
                "advice": str(exc)}


def preflight(pdf, out_dir) -> dict:
    """翻译前的完整预检。ok=False 时 title/advice 可直接展示给用户。"""
    r = check_source_pdf(pdf)
    if not r.get("ok"):
        return r
    r2 = check_output_dir(out_dir)
    if not r2.get("ok"):
        return r2
    return {"ok": True, "pages": r.get("pages", 0)}


def estimate_workload(pages: int, cfg: dict | None = None) -> str:
    """按页数粗估翻译耗时（基于实测：14 页约 2.5 分钟）。"""
    try:
        n = int(pages or 0)
    except Exception:
        return ""
    if n <= 0:
        return ""
    # 实测 SGM.pdf 14 页 152 秒 ≈ 11 秒/页，8~15 秒/页是合理区间
    if n <= 3:
        return "%d 页 · 预计 1 分钟内" % n
    lo = max(1, int(round(n * 8 / 60)))
    hi = max(lo + 1, int(round(n * 15 / 60)))
    return "%d 页 · 预计 %d~%d 分钟" % (n, lo, hi)

def notify_done(title: str, message: str, ok: bool = True) -> bool:
    """翻译完成后弹 Windows 系统通知（不依赖任何第三方库）。

    用 Shell_NotifyIcon 的 NIF_INFO（气泡提示），
    比托盘图标更轻量，也不需要应用一直有托盘。
    失败就静默返回 False，不影响翻译流程。
    """
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
        NIF_ICON, NIF_TIP, NIF_INFO = 2, 4, 16
        NIIF_INFO, NIIF_WARNING = 1, 2
        IMAGE_ICON = 1
        LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
            ]

        ico = Path(APP_DIR) / "app.ico"
        hicon = 0
        if ico.exists():
            hicon = user32.LoadImageW(None, str(ico), IMAGE_ICON, 0, 0,
                                      LR_LOADFROMFILE | LR_DEFAULTSIZE)

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = None
        nid.uID = 0x9F01          # 独立 ID，避免和托盘图标冲突
        nid.uFlags = NIF_INFO | NIF_ICON | NIF_TIP
        nid.hIcon = hicon
        nid.szTip = "PDF 翻译助手"
        nid.szInfo = str(message)[:250]
        nid.szInfoTitle = str(title)[:60]
        nid.dwInfoFlags = NIIF_INFO if ok else NIIF_WARNING

        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        # 给系统一点时间显示气泡，然后移除（否则图标会一直留着）
        import threading as _th
        def _cleanup():
            import time as _t
            _t.sleep(8)
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception:
                pass
        _th.Thread(target=_cleanup, daemon=True).start()
        return True
    except Exception:
        return False
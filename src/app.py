# -*- coding: utf-8 -*-
"""
PDF 翻译助手 —— 独立桌面应用

用法：双击打开 → 把 PDF 拖进窗口（或点“选择 PDF”）→ 自动翻译。

不依赖 Zotero、不依赖系统 Python。所有运行环境都在程序自带目录里。

翻译在 worker.py 子进程里执行：pdf2zh_next 的 CLI 被管道捕获时只输出
最终行、拿不到增量进度，而 Python API 会 yield overall_progress 事件，
所以用 worker 转发 JSON 进度，界面据此显示真实百分比。
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

    last_err = ""
    for idx, th in enumerate(attempts):
        c = dict(cfg)
        c["threads"] = th
        if idx > 0:
            log("")
            log("⚠️ 上次失败，正在降低并发到 %d 重试（第 %d 次）…" % (th, idx + 1))
        ok, err = _run_translate_once(pdf, c, out_dir, log, on_progress, on_done,
                                      on_stage_cb)
        if ok:
            return
        last_err = err or "翻译失败"
        # 文件锁跟限流无关，降并发没用 —— 直接停下并说清楚怎么办
        if _is_lock_error(last_err):
            log("")
            log("❌ 失败原因：输出文件被其它程序占用（不是限流）。")
            log("   请关闭正在查看该 PDF 的程序（WPS / Adobe / 浏览器）后重试。")
            on_done(False, None, last_err)
            return
    log("")
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
                    on_progress(float(ev.get("overall") or 0.0), str(ev.get("stage") or ""))
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


# ---------------------------------------------------------------- 界面
# 重建界面时要保留的临时状态（主题热切换用）
_UI_STATE: dict = {}

# 需要跟随主题重绘的纯 tk 控件（ttk 会自动跟，tk 的必须手动记）
_TK_THEME_WIDGETS: list = []


def reg_theme_widget(widget, role: str = "card") -> None:
    """登记一个纯 tk 控件，让它在主题切换时自动重绘。

    ttk 控件的配色由 Style 管，主题一改就跟着变；
    但 tk.Text / tk.Frame / tk.Canvas 这些是【自己带颜色】的，
    不登记的话切到夜间模式后它们仍是白底 —— 这就是"还有大片白色"的原因。
    """
    try:
        _TK_THEME_WIDGETS.append((widget, role))
    except Exception:
        pass


def apply_theme_to_widgets(C: dict) -> None:
    """把主题色应用到所有登记过的纯 tk 控件。"""
    role_map = {
        "card":    {"bg": C["card"],    "fg": C["text"]},
        "bg":      {"bg": C["bg"],      "fg": C["text"]},
        "bg_alt":  {"bg": C["bg_alt"],  "fg": C["text"]},
        "log":     {"bg": C["log_bg"],  "fg": C["log_fg"]},
        "sidebar": {"bg": C["sidebar"], "fg": C["text"]},
        "entry":   {"bg": C["entry_bg"], "fg": C["text"]},
        "canvas":  {"bg": C["bg_alt"]},
    }
    alive = []
    for w, role in _TK_THEME_WIDGETS:
        try:
            if not w.winfo_exists():
                continue
            spec = role_map.get(role, role_map["card"])
            for opt, val in spec.items():
                try:
                    w.configure(**{opt: val})
                except Exception:
                    pass
            # Text 还要改插入光标与选中色
            try:
                if isinstance(w, __import__("tkinter").Text):
                    w.configure(insertbackground=C["log_fg"],
                                selectbackground=C["accent"])
            except Exception:
                pass
            alive.append((w, role))
        except Exception:
            continue
    _TK_THEME_WIDGETS[:] = alive


# 界面「代次」：每次 build_ui 自增。
# 主题热切换会销毁并重建整个界面，但旧的 root.after 循环（进度轮询 pump、
# 步骤计时 _tick_step）仍在排队执行，一旦访问已销毁的控件就会报
# "invalid command name ...pump"。让每个循环记住自己的代次，
# 发现代次变了就自己停下。
_BUILD_GEN = {"n": 0}


def remember_log_text(text: str) -> None:
    """记下日志内容，重建界面后恢复。"""
    _UI_STATE["log"] = text or ""


def build_ui(root=None, restore: bool = False):
    """构建界面。

    root  传入已有窗口时复用它（主题热切换会走这条路，避免开新窗口）
    restore=True 表示这是重建，尽量恢复上次的日志内容与选中项
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    # ---- 高 DPI：必须在 Tk() 之前开启，否则界面会被系统拉伸而发虚 ----
    _dpi_mode = enable_dpi_awareness()
    _dpi = get_real_dpi()

    # 新一轮构建 → 代次 +1（旧的 after 循环会因此自行停止）
    _BUILD_GEN["n"] += 1

    # 复用已有窗口（主题热切换重建时传入），否则新建
    if root is None:
        root = tk.Tk()

    # （布局选择见 build_ui 末尾：侧边栏版复用同一批处理函数，只换布局）
    root.title("PDF 翻译助手")
    if not restore:
        root.geometry("840x660")
    root.minsize(700, 560)

    # 开启感知后，Tk 的默认缩放还是按 96dpi 算的，界面会显得很小；
    # 把 scaling 设成 真实DPI/72 才能既清晰、大小又正常。
    try:
        root.tk.call("tk", "scaling", _dpi / 72.0)
    except Exception:
        pass

    # ---- 统一界面字体 ----
    # 之前各处混用「Microsoft YaHei UI 9」和各种默认字体，中英文走两套字形，
    # 看着不统一也显糊。这里统一成雅黑，正文 10 号，标题 11 号。
    try:
        import tkinter.font as _tkfont
        _ui_family = "Microsoft YaHei UI"
        try:
            if _ui_family not in set(_tkfont.families()):
                _ui_family = "Microsoft YaHei"
        except Exception:
            pass
        for _name, _size, _weight in (
            ("TkDefaultFont", 10, "normal"),
            ("TkTextFont", 10, "normal"),
            ("TkMenuFont", 10, "normal"),
            ("TkHeadingFont", 10, "bold"),
            ("TkTooltipFont", 9, "normal"),
            ("TkCaptionFont", 10, "bold"),
            ("TkIconFont", 10, "normal"),
        ):
            try:
                _tkfont.nametofont(_name).configure(
                    family=_ui_family, size=_size, weight=_weight)
            except Exception:
                pass
        # 等宽字体（日志区用）
        try:
            _tkfont.nametofont("TkFixedFont").configure(
                family="Consolas", size=10)
        except Exception:
            pass
    except Exception:
        pass

    # ---- 配色方案（白天 / 夜间两套）----
    # 定义在模块级（见文件上方的 THEMES），这样「侧边栏版」界面也能复用。
    # 关键教训：ttk 的 vista / winnative 是【系统原生主题】，
    # Windows 自己绘制按钮，configure(background=...) 会被忽略 ——
    # 结果就是「白字配白底、悬停无反应」。必须用 clam 主题才能完全掌控配色。
    _mode = str(load_config().get("ui_theme") or "light").lower()
    if _mode not in THEMES:
        _mode = "light"
    C = dict(THEMES[_mode])

    # 注：侧边栏版是独立入口（app_sidebar.py），不在这里分支，
    # 避免在这个已经很长的函数里再插条件，降低出错风险。

    # 兼容旧变量名（下面还有大量引用）
    C_BG = C["bg"]; C_BG_ALT = C["bg_alt"]; C_CARD = C["card"]
    C_HEAD_BG = C["head_bg"]; C_TEXT = C["text"]; C_TEXT_SUB = C["text_sub"]
    C_MUTED = C["muted"]; C_BORDER = C["border"]; C_ACCENT = C["accent"]
    C_ACCENT_HOVER = C["accent_hv"]; C_ACCENT_SOFT = C["accent_sf"]
    C_ROW_ALT = C["row_alt"]; C_OK = C["ok"]; C_WARN = C["warn"]
    C_DANGER = C["danger"]; C_HEADER = C["card"]

    try:
        _style = ttk.Style()
        # 必须用 clam：只有它允许完全自定义配色
        try:
            _style.theme_use("clam")
        except Exception:
            pass

        try:
            root.configure(bg=C_BG)
        except Exception:
            pass

        _row_h = int(24 * _dpi / 96.0)
        try:
            _style.configure(".", background=C_BG, foreground=C_TEXT,
                             font=("Microsoft YaHei UI", 10))
            _style.configure("TFrame", background=C_BG)
            _style.configure("TLabel", background=C_BG, foreground=C_TEXT)
            _style.configure("TCheckbutton", background=C_BG)
            _style.configure("TRadiobutton", background=C_BG)
            _style.configure("TLabelframe", background=C_BG)
            _style.configure("TLabelframe.Label", background=C_BG,
                             foreground=C_ACCENT)
            # 按钮：留白加大 + 悬停变色（见下面的 _bind_hover）
            _style.configure("TButton", padding=(12, 6))
            _style.map("TButton",
                       background=[("active", "#e8eefb"), ("pressed", "#d6e2f7")],
                       relief=[("pressed", "sunken")])
            _style.configure("TNotebook", background=C_BG, borderwidth=0)
            _style.configure("TNotebook.Tab", padding=(16, 7),
                             background=C_HEAD_BG, foreground=C_TEXT_SUB)
            _style.map("TNotebook.Tab",
                       background=[("selected", C_CARD), ("active", C_ACCENT_SOFT)],
                       foreground=[("selected", C_ACCENT), ("active", C_ACCENT)],
                       expand=[("selected", (0, 0, 0, 1))])
            _style.configure("Treeview", rowheight=_row_h,
                             background=C_CARD, fieldbackground=C_CARD,
                             borderwidth=0)
            _style.configure("Treeview.Heading", padding=(8, 7),
                             background=C_HEAD_BG, foreground=C_HEADER,
                             relief="flat", font=("Microsoft YaHei UI", 10, "bold"))
            _style.map("Treeview.Heading",
                       background=[("active", C_ACCENT_SOFT)],
                       foreground=[("active", C_ACCENT)])
            _style.map("Treeview",
                       background=[("selected", C_ACCENT)],
                       foreground=[("selected", "#ffffff")])
            # 分隔线 / 滚动条也统一色
            _style.configure("TSeparator", background=C_BORDER)
            _style.configure("Vertical.TScrollbar", background=C_BORDER,
                             troughcolor=C_BG_ALT, borderwidth=0, arrowsize=13,
                             arrowcolor=C_TEXT_SUB)
            _style.configure("Horizontal.TScrollbar", background=C_BORDER,
                             troughcolor=C_BG_ALT, borderwidth=0, arrowsize=13,
                             arrowcolor=C_TEXT_SUB)
            # 进度条：主色填充 + 浅底
            _style.configure("Horizontal.TProgressbar",
                             thickness=int(18 * _dpi / 96.0),
                             background=C_ACCENT, troughcolor=C_HEAD_BG,
                             borderwidth=0)
            # 分组框
            _style.configure("TLabelframe", background=C_BG,
                             bordercolor=C_BORDER, relief="solid", borderwidth=1)
            _style.configure("TLabelframe.Label", background=C_BG,
                             foreground=C_ACCENT,
                             font=("Microsoft YaHei UI", 10, "bold"))
            # 输入框 / 下拉框：统一底色与描边，悬停/聚焦有反馈
            _style.configure("TEntry", padding=(8, 6),
                             fieldbackground=C["entry_bg"], foreground=C_TEXT,
                             bordercolor=C_BORDER, lightcolor=C_BORDER,
                             darkcolor=C_BORDER, insertcolor=C_TEXT)
            _style.map("TEntry",
                       bordercolor=[("focus", C_ACCENT)],
                       lightcolor=[("focus", C_ACCENT)],
                       darkcolor=[("focus", C_ACCENT)])
            _style.configure("TCombobox", padding=(8, 5),
                             fieldbackground=C["entry_bg"], background=C_CARD,
                             foreground=C_TEXT, arrowcolor=C_TEXT_SUB,
                             bordercolor=C_BORDER,
                             lightcolor=C_BORDER, darkcolor=C_BORDER)
            _style.map("TCombobox",
                       fieldbackground=[("readonly", C["entry_bg"]),
                                        ("disabled", C_BG_ALT)],
                       foreground=[("disabled", C_MUTED)],
                       bordercolor=[("focus", C_ACCENT), ("hover", C_ACCENT)],
                       arrowcolor=[("hover", C_ACCENT)],
                       lightcolor=[("focus", C_ACCENT)],
                       darkcolor=[("focus", C_ACCENT)])
            # 下拉列表（popdown）也要跟着主题走
            try:
                root.option_add("*TCombobox*Listbox.background", C["entry_bg"])
                root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
                root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
                root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
                root.option_add("*TCombobox*Listbox.font",
                                ("Microsoft YaHei UI", 10))
            except Exception:
                pass
            # 复选/单选
            _style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT,
                             focuscolor=C_BG)
            _style.map("TCheckbutton",
                       background=[("active", C_BG)],
                       foreground=[("active", C_ACCENT)])
            _style.configure("TRadiobutton", background=C_BG, foreground=C_TEXT,
                             focuscolor=C_BG)
            _style.map("TRadiobutton",
                       background=[("active", C_BG)],
                       foreground=[("active", C_ACCENT)])
        except Exception:
            pass
    except Exception:
        pass

    # ---- 按钮悬停效果 ----
    # 关键教训：上一版用「改变 padding」做动画，按钮尺寸会跟着变，
    # 一变就触发重排、鼠标可能离开控件，于是反复触发 enter/leave —— 看着就是抖。
    # 现在只改【颜色】（尺寸恒定），既没有震荡，过渡也更顺。
    #
    # 按钮分三种角色，配色各有侧重：
    #   primary  主操作（选择 PDF）—— 实心蓝，最显眼
    #   accent   功能入口（打开/工具）—— 白底蓝字，描边
    #   danger   破坏性（删除/清空）—— 红字，提醒谨慎
    #   normal   普通（设置等）—— 浅灰
    # 按钮配色跟随主题。注意 primary 一定是「深底 + 白字」，
    # 之前用 vista 主题时 background 被系统忽略，白字落在白底上 = 看不见。
    _BTN_STYLE = {
        "primary": dict(bg=C_ACCENT, fg="#ffffff",
                        hover=C_ACCENT_HOVER, active=C_ACCENT_HOVER),
        "accent":  dict(bg=C_CARD, fg=C_ACCENT,
                        hover=C_ACCENT_SOFT, active=C_ACCENT_SOFT),
        "danger":  dict(bg=C_CARD, fg=C_DANGER,
                        hover="#fdecea" if _mode == "light" else "#3a2a2c",
                        active="#fadbd8" if _mode == "light" else "#452f31"),
        "normal":  dict(bg=C_HEAD_BG, fg=C_TEXT,
                        hover=C_ACCENT_SOFT, active=C_ACCENT_SOFT),
    }
    _btn_seq = {"n": 0}

    def _style_button(btn, role="normal"):
        """给按钮套一套配色，并注册 hover/press 效果（只改颜色）。"""
        cfg = _BTN_STYLE.get(role, _BTN_STYLE["normal"])
        _btn_seq["n"] += 1
        name = "Btn%d.TButton" % _btn_seq["n"]
        try:
            _style.configure(name,
                             background=cfg["bg"], foreground=cfg["fg"],
                             borderwidth=0, focuscolor=cfg["bg"],
                             padding=(12, 6), relief="flat")
            _style.map(name,
                       background=[("disabled", "#f0f2f5"),
                                   ("pressed", cfg["active"]),
                                   ("active", cfg["hover"])],
                       foreground=[("disabled", "#b8bec8")])
            btn.configure(style=name)
        except Exception:
            pass

        def on_enter(_e=None):
            try:
                btn.state(["active"])          # 触发上面的 active 配色
                btn.configure(cursor="hand2")
            except Exception:
                pass

        def on_leave(_e=None):
            try:
                btn.state(["!active"])
                btn.configure(cursor="")
            except Exception:
                pass

        for ev, fn in (("<Enter>", on_enter), ("<Leave>", on_leave)):
            try:
                btn.bind(ev, fn, add="+")
            except Exception:
                pass
        return btn

    def bind_hover(btn, role="normal"):
        """兼容旧调用：等同 _style_button。"""
        return _style_button(btn, role)

    def mkbtn(parent, text, command=None, role="normal", **kw):
        """建一个带悬停配色的按钮（统一入口）。"""
        b = ttk.Button(parent, text=text, command=command, **kw)
        _style_button(b, role)
        return b

    try:
        ico = APP_DIR / "app.ico"
        if ico.exists():
            root.iconbitmap(str(ico))
    except Exception:
        pass

    # 记下 DPI 信息，等日志控件建好后由 refresh_info() 附近的代码输出
    _dpi_info = (_dpi, _dpi_mode)

    # 注意：这里【不要】再调 theme_use("vista")！
    # vista 是系统原生主题，会忽略 configure 的配色 ——
    # 之前就是它把上面设好的 clam 覆盖回去，导致按钮白字白底、悬停无反应。
    style = ttk.Style()

    # 顶部只留一行紧凑说明（原来的大标题 + 长说明太占地方）
    head = ttk.Frame(root, padding=(16, 10, 16, 2))
    head.pack(fill="x")
    ttk.Label(head, text="把 PDF 拖到下方，或点「选择 PDF」",
              foreground=C_TEXT_SUB).pack(anchor="w")

    # ---------- 拖放区 ----------
    # 原来是个占 ~150px 的大方块（3 行文字 + 上下各 26px 留白），
    # 把下面的日志/表格挤到只剩 50px 高。改成一条紧凑的横向提示带，
    # 拖放功能不变，竖向空间让给日志和表格。
    drop = tk.Frame(root, bg="#f2f6fb", highlightbackground="#9db8d6",
                    highlightthickness=1, bd=0)
    drop.pack(fill="x", expand=False, padx=16, pady=(4, 6))
    drop_lbl = tk.Label(
        drop, text="⬇  把 PDF 拖到这里，或点击选择文件（可多选批量翻译）",
        bg="#f2f6fb", fg="#3a6ea5", font=("Microsoft YaHei UI", 10),
        justify="center", cursor="hand2",
    )
    drop_lbl.pack(pady=8, fill="x")

    # ---------- 当前设置概览 ----------
    info = ttk.Frame(root, padding=(16, 6, 16, 4))
    info.pack(fill="x")
    info_lbl = ttk.Label(info, text="", foreground=C_TEXT_SUB)
    info_lbl.pack(anchor="w")

    def refresh_info():
        c = load_config()
        mode = str(c.get("output_mode") or "mono").lower()
        try:
            th = int(c.get("threads") or 10)
        except Exception:
            th = 10
        gl = "开" if c.get("glossary_enabled", True) else "关"
        info_lbl.configure(
            text="引擎：%s    版本：%s    并发：%d    术语表：%s    保存到：%s"
            % (ENGINE_LABEL.get(str(c.get("engine")), str(c.get("engine"))),
               MODE_LABELS.get(mode, mode), th, gl, resolve_output_dir(c))
        )

    # ---------- 操作按钮 ----------
    # 分成「主操作」和「工具」两组，中间用分隔线隔开，视觉上更清楚
    bar = ttk.Frame(root, padding=(16, 0, 16, 8))
    bar.pack(fill="x")

    btn_pick = mkbtn(bar, "选择 PDF", role="primary")
    btn_pick.pack(side="left")

    btn_recent = mkbtn(bar, "上次的文件夹", role="normal", state="disabled")
    btn_recent.pack(side="left", padx=(8, 0))

    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)

    btn_view = mkbtn(bar, "打开 PDF", role="accent")
    btn_view.pack(side="left")

    btn_edit = mkbtn(bar, "PDF 工具", role="accent")
    btn_edit.pack(side="left", padx=(8, 0))

    btn_inspect = mkbtn(bar, "检查工具", role="accent")
    btn_inspect.pack(side="left", padx=(8, 0))

    btn_set = mkbtn(bar, "设置", role="normal")
    btn_set.pack(side="right")

    def _remember_ui_state():
        """重建界面前把要保留的状态存下来。"""
        try:
            _UI_STATE["log"] = logtxt.get("1.0", "end").rstrip("\n")
        except Exception:
            pass
        try:
            _UI_STATE["tab"] = nb.index(nb.select())
        except Exception:
            pass

    # 夜间/白天切换 —— 即时生效，不用重启
    def _toggle_theme():
        c = load_config()
        cur = str(c.get("ui_theme") or "light").lower()
        new_mode = "dark" if cur == "light" else "light"
        c["ui_theme"] = new_mode
        save_config(c)
        # 记下当前状态，重建界面后恢复
        _remember_ui_state()
        # 关键：先卸载拖放钩子，否则销毁窗口后旧回调会读到野指针而崩溃
        try:
            disable_drop()
        except Exception:
            pass
        # 销毁旧界面、按新主题重建（Tk 没有全局换肤 API，
        # 但 ttk 的 configure 是运行时可改的 —— 重建是最省事又最彻底的做法）
        for child in list(root.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        root.configure(bg=THEMES[new_mode]["bg"])
        build_ui(root=root, restore=True)

    btn_theme = mkbtn(bar, "☀ 白天" if _mode == "dark" else "🌙 夜间",
                      role="normal", command=_toggle_theme)
    btn_theme.pack(side="right", padx=(0, 8))

    def _recent_folder() -> str:
        """从历史里找最近一个仍然存在的输出目录。"""
        for it in load_history():
            d = str(it.get("out_dir") or "")
            if d and Path(d).is_dir():
                return d
            # 没有 out_dir 就从产物路径推
            for f in (it.get("files") or []):
                p = Path(str(f))
                if p.parent.is_dir():
                    return str(p.parent)
        return ""

    def use_recent_folder():
        d = _recent_folder()
        if not d:
            # 退回当前设置里的输出目录
            d = str(resolve_output_dir(load_config()))
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            os.startfile(d)  # type: ignore[attr-defined]
            status.configure(text="已打开：" + d, foreground="#2a7d2a")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    btn_recent.configure(command=use_recent_folder)

    status = ttk.Label(bar, text="就绪", foreground="#2a7d2a")
    status.pack(side="right")

    # ---------- 进度条（真实百分比） ----------
    pf = ttk.Frame(root, padding=(16, 0, 16, 8))
    pf.pack(fill="x")
    prog = ttk.Progressbar(pf, mode="determinate", maximum=100.0, value=0)
    prog.pack(side="left", fill="x", expand=True)
    pct = ttk.Label(pf, text="0%", width=6, anchor="e")
    pct.pack(side="left", padx=(8, 0))
    stage_lbl = ttk.Label(pf, text="", foreground="#666", width=22, anchor="w")
    stage_lbl.pack(side="left", padx=(8, 0))
    eta_lbl = ttk.Label(pf, text="", foreground="#888", width=14, anchor="w")
    eta_lbl.pack(side="left", padx=(4, 0))

    # ---------- 日志 / 历史（选项卡） ----------
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    logf = ttk.Frame(nb, padding=6)
    nb.add(logf, text="运行日志")
    logtxt = tk.Text(logf, height=12, wrap="word",
                     bg=C["log_bg"], fg=C["log_fg"],
                     insertbackground=C["log_fg"],
                     selectbackground=C_ACCENT,
                     font=("Consolas", 10), relief="flat",
                     padx=8, pady=6)
    sb = ttk.Scrollbar(logf, command=logtxt.yview)
    logtxt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    logtxt.pack(side="left", fill="both", expand=True)
    # 重建界面时把之前的日志内容放回去，不让用户觉得"日志被清空了"
    if restore and _UI_STATE.get("log"):
        try:
            logtxt.insert("1.0", _UI_STATE["log"])
            logtxt.see("end")
        except Exception:
            pass

    # ---- 步骤耗时（在「运行日志」和「历史记录」之间）----
    stepf = ttk.Frame(nb, padding=6)
    nb.add(stepf, text="步骤耗时")

    step_top = ttk.Frame(stepf)
    step_top.pack(side="top", fill="x", pady=(0, 6))
    step_sum = ttk.Label(step_top, text="还没有翻译记录", foreground="#666")
    step_sum.pack(side="left")
    mkbtn(step_top, "清空", role="danger",
          command=lambda: _clear_steps()).pack(side="right")

    step_body = ttk.Frame(stepf)
    step_body.pack(side="top", fill="both", expand=True)
    step_cols = ("stage", "secs", "pct", "bar")
    step_tree = ttk.Treeview(step_body, columns=step_cols, show="headings", height=11)
    for c, txt, w, anc in (("stage", "步骤", 300, "w"), ("secs", "耗时", 90, "e"),
                           ("pct", "占比", 70, "e"), ("bar", "分布", 220, "w")):
        step_tree.heading(c, text=txt)
        step_tree.column(c, width=w, anchor=anc)
    step_sb = ttk.Scrollbar(step_body, command=step_tree.yview)
    step_tree.configure(yscrollcommand=step_sb.set)
    step_sb.pack(side="right", fill="y")
    step_tree.pack(side="left", fill="both", expand=True)
    ttk.Label(stepf,
              text="显示每个阶段实际花费的时间，用来判断瓶颈在哪（可据此调并发）。",
              foreground="#777").pack(side="bottom", anchor="w", pady=(4, 0))

    def _clear_steps():
        for i in step_tree.get_children():
            step_tree.delete(i)
        step_sum.configure(text="还没有翻译记录")

    def _show_steps(stages: list, total: float):
        """把 worker 报上来的阶段耗时画成表格 + 文本条形图。"""
        for i in step_tree.get_children():
            step_tree.delete(i)
        if not stages:
            step_sum.configure(text="这次没有采集到阶段信息")
            return
        stage_times_local["stages"] = stages
        stage_times_local["total"] = total
        # 按耗时倒序，瓶颈一眼可见
        rows = sorted(stages, key=lambda s: -(s.get("seconds") or 0))
        for r in rows:
            name = stage_cn(str(r.get("name") or "?"))
            secs = float(r.get("seconds") or 0)
            pct = float(r.get("percent") or 0)
            n = max(1, int(round(pct / 2.5)))          # 40 格 = 100%
            bar = "█" * n
            step_tree.insert("", "end", values=(
                name, "%.1f 秒" % secs, "%.1f%%" % pct, bar))
        step_sum.configure(
            text="共 %.1f 秒，%d 个阶段（按耗时排序）" % (total, len(stages)),
            foreground="#2a7d2a")

    def _step_begin(name: str):
        """实时显示：当前正在跑哪个阶段（含已用时间）。

        每 0.5 秒刷新一次计时，让人看到「确实在动」而不是卡住了。
        """
        stage_times_local["live_name"] = name
        stage_times_local["live_start"] = time.time()
        # 若该阶段之前已出现过，保留累计值
        prev = {}
        for r in (stage_times_local.get("stages") or []):
            prev[stage_cn(str(r.get("name")))] = float(r.get("seconds") or 0)
        stage_times_local["prev"] = prev
        _tick_step()

    # 记下本代界面的编号；重建界面后旧循环会发现自己过期而停止
    _my_gen = _BUILD_GEN["n"]

    def _tick_step():
        # 代次守卫：界面被重建过就直接退出，避免访问已销毁的控件
        if _my_gen != _BUILD_GEN["n"]:
            return
        name = stage_times_local.get("live_name")
        if not name:
            return
        start = float(stage_times_local.get("live_start") or time.time())
        elapsed = time.time() - start
        cn = stage_cn(name)
        prev = stage_times_local.get("prev") or {}

        # 重建列表：已完成阶段 + 当前进行中（带秒表）
        for i in step_tree.get_children():
            step_tree.delete(i)
        for k, v in sorted(prev.items(), key=lambda x: -x[1]):
            step_tree.insert("", "end", values=(k, "%.1f 秒" % v, "", "█" * max(1, int(v))))
        step_tree.insert("", "end", values=(
            "▶ " + cn, "%.1f 秒…" % elapsed, "进行中", "▌"))
        step_tree.see(step_tree.get_children()[-1])
        step_sum.configure(
            text="正在执行：%s（已用 %.1f 秒）" % (cn, elapsed),
            foreground="#b8860b")
        # 继续刷新
        try:
            stage_times_local["tick_job"] = root.after(500, _tick_step)
        except Exception:
            pass

    def _step_end():
        """阶段结束：停掉计时器。"""
        job = stage_times_local.get("tick_job")
        if job:
            try:
                root.after_cancel(job)
            except Exception:
                pass
        stage_times_local["tick_job"] = None
        stage_times_local["live_name"] = None

    def _fmt_steps_text() -> str:
        """把阶段耗时拼成可粘贴的文本，方便贴到别处讨论。"""
        stages = stage_times_local.get("stages") or []
        if not stages:
            return ""
        lines = ["阶段耗时统计（共 %.1f 秒）：" % float(stage_times_local.get("total") or 0)]
        for r in sorted(stages, key=lambda s: -(s.get("seconds") or 0)):
            lines.append("  %-38s %7.1fs  %5.1f%%" % (
                str(r.get("name"))[:38], float(r.get("seconds") or 0),
                float(r.get("percent") or 0)))
        return "\n".join(lines)

    mkbtn(step_top, "复制统计", role="accent",
          command=lambda: _copy_steps()).pack(side="right", padx=(0, 6))

    def _copy_steps():
        t = _fmt_steps_text()
        if not t:
            messagebox.showinfo("暂无数据", "先翻译一次才有阶段耗时。")
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(t)
            status.configure(text="阶段耗时已复制", foreground="#2a7d2a")
        except Exception:
            pass
    stage_times_local: dict = {}

    # ---- 历史记录 ----
    histf = ttk.Frame(nb, padding=8)
    nb.add(histf, text="历史记录")

    # 底部提示先占位（side=bottom 不受后续影响）
    ttk.Label(histf, text="双击一行即可打开；也可右键选择打开方式",
              foreground="#8a9099").pack(side="bottom", anchor="w", pady=(6, 0))

    # 表格容器：先建好，后面工具栏都 pack 到 histf，表格放进 hbody
    hbody = ttk.Frame(histf)
    hbody.pack(side="top", fill="both", expand=True)
    cols = ("time", "file", "kind", "engine", "mode", "group", "warn")
    hist = ttk.Treeview(hbody, columns=cols, show="headings", height=11)
    # 「提示」列显示的是【质量检查的告警条数】，例如 ⚠3 表示这一页有 3 处可疑。
    # 悬停表头能看到说明。
    for c, txt, w, anc in (("time", "时间", 118, "w"), ("file", "文件", 240, "w"),
                           ("kind", "产出", 74, "center"), ("engine", "引擎", 82, "w"),
                           ("mode", "版本", 78, "w"), ("group", "分组", 92, "w"),
                           ("warn", "质量告警", 80, "center")):
        hist.heading(c, text=txt)
        hist.column(c, width=w, anchor=anc)

    # 表头悬停提示（说明每一列是什么）
    _COL_TIP = {
        "time": "这条记录的时间",
        "file": "翻译的源文件",
        "kind": "产出类型：PDF / Word / PDF+Word",
        "engine": "使用的翻译引擎",
        "mode": "输出版本（中文版 / 双语对照）",
        "group": "你给它分的组（可在下面「设置分组」里改）",
        "warn": ("质量检查发现的告警条数。⚠3 = 有 3 处可疑"
                 "（如漏翻、英文残留、数字丢失）。\n"
                 "双击记录后在「运行日志」里看详情。"),
    }
    _tip_win = {"w": None}

    def _show_tip(text, x, y):
        _hide_tip()
        try:
            t = tk.Toplevel(root)
            t.wm_overrideredirect(True)
            t.attributes("-topmost", True)
            tk.Label(t, text=text, justify="left", background="#ffffe0",
                     foreground="#333333", relief="solid", borderwidth=1,
                     font=("Microsoft YaHei UI", 9), padx=8, pady=5).pack()
            t.wm_geometry("+%d+%d" % (x + 12, y + 22))
            _tip_win["w"] = t
        except Exception:
            pass

    def _hide_tip(_e=None):
        w = _tip_win.get("w")
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            _tip_win["w"] = None

    def _on_head_motion(ev):
        """鼠标移到表头 → 显示该列说明。"""
        try:
            region = hist.identify_region(ev.x, ev.y)
            if region != "heading":
                _hide_tip()
                return
            col = hist.identify_column(ev.x)
            idx = int(col.replace("#", "")) - 1
            cols_order = ("time", "file", "kind", "engine", "mode", "group", "warn")
            if 0 <= idx < len(cols_order):
                tip = _COL_TIP.get(cols_order[idx], "")
                if tip:
                    _show_tip(tip, ev.x_root, ev.y_root)
        except Exception:
            pass

    hist.bind("<Motion>", _on_head_motion, add="+")
    hist.bind("<Leave>", _hide_tip, add="+")
    # 隔行底色 + 选中色，长列表更容易看
    try:
        hist.tag_configure("odd", background="#fafafa")
        hist.tag_configure("warnrow", foreground="#b8860b")
    except Exception:
        pass
    # ---- 工具栏：分两行，避免挤成一条 ----
    # 之前所有控件塞在一行里，窗口一窄就互相压扁。
    # 现在按用途分两行：
    #   第一行 = 筛选（打开方式 / 分组）
    #   第二行 = 操作（打开 / 文件夹 / 分组 / 删除 / 清空）
    # 工具栏插到表格上方（before=hbody 保证顺序，不受创建先后影响）
    hbar = ttk.Frame(histf)
    hbar.pack(side="top", fill="x", pady=(0, 6), before=hbody)
    hbar2 = ttk.Frame(histf)
    hbar2.pack(side="top", fill="x", pady=(0, 8), before=hbody)

    ttk.Label(hbar, text="打开方式").pack(side="left")
    ow_var = tk.StringVar(value=str(load_config().get("open_with") or "auto"))
    ow_combo = ttk.Combobox(hbar, textvariable=ow_var, state="readonly", width=14)
    _OW_LABEL = {"auto": "系统默认", "wps": "WPS", "edge": "Microsoft Edge",
                 "folder": "打开所在文件夹"}
    _OW_VALUE = {v: k for k, v in _OW_LABEL.items()}
    ow_combo.configure(values=list(_OW_LABEL.values()))
    ow_combo.set(_OW_LABEL.get(ow_var.get(), "系统默认"))
    ow_combo.pack(side="left", padx=(6, 0))

    def _save_open_with(_e=None):
        c = load_config()
        c["open_with"] = _OW_VALUE.get(ow_combo.get(), "auto")
        save_config(c)
    ow_combo.bind("<<ComboboxSelected>>", _save_open_with)

    ttk.Label(hbar, text="分组").pack(side="left", padx=(18, 0))
    grp_var = tk.StringVar(value="全部")
    grp_combo = ttk.Combobox(hbar, textvariable=grp_var, state="readonly", width=14)
    grp_combo.pack(side="left", padx=(6, 0))
    grp_combo.bind("<<ComboboxSelected>>", lambda e: refresh_history())

    ttk.Label(hbar, text="搜索").pack(side="left", padx=(18, 0))
    q_var = tk.StringVar()
    q_entry = ttk.Entry(hbar, textvariable=q_var, width=18)
    q_entry.pack(side="left", padx=(6, 0))
    q_entry.bind("<KeyRelease>", lambda e: refresh_history())
    ttk.Button(hbar, text="清除", width=6,
               command=lambda: (q_var.set(""), refresh_history())).pack(
        side="left", padx=(4, 0))

    def _refresh_group_options():
        # 「Word」是内置分组：凡是产出了 Word 文件的记录都归到这里
        vals = ["全部", "未分组", "Word"] + list_groups()
        grp_combo.configure(values=vals)
        if grp_var.get() not in vals:
            grp_var.set("全部")
    _refresh_group_options()

    def _assign_group():
        """把选中的记录放进某个分组（可新建）。"""
        sel = hist.selection()
        if not sel:
            messagebox.showinfo("请先选择", "请先在列表里选中要分组的记录（可多选）。")
            return
        dlg = tk.Toplevel(root)
        dlg.title("设置分组")
        dlg.geometry("360x170")
        dlg.transient(root)
        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="把 %d 条记录归入分组：" % len(sel)).pack(anchor="w")
        gv = tk.StringVar(value="")
        box = ttk.Combobox(f, textvariable=gv, width=32,
                           values=["（不分组）"] + list_groups())
        box.pack(fill="x", pady=(8, 0))
        ttk.Label(f, text="可直接输入新分组名，如「STM32 项目」",
                  foreground="#777").pack(anchor="w", pady=(4, 0))

        def do_it():
            g = gv.get().strip()
            if g == "（不分组）":
                g = ""
            for iid in sel:
                it = hist_map.get(iid)
                if it:
                    set_history_group(str(it.get("time")), g)
            _refresh_group_options()
            refresh_history()
            status.configure(text="已分组", foreground="#2a7d2a")
            dlg.destroy()

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="确定", command=do_it).pack(side="right")
        ttk.Button(row, text="取消", command=dlg.destroy).pack(side="right", padx=6)

    # ---- 操作按钮：一次建齐 ----
    # 注意：必须在这里全部建完，再去 pack 下面的表格。
    # 之前把「删除选中/清空全部」留到文件后面才创建，结果表格先 pack 上去、
    # 按钮后加，布局就错位了（表头跑到按钮下面）。
    # 用 lambda 包一层即可延迟绑定（函数体在后面才定义也没关系）。
    mkbtn(hbar2, "打开选中", role="primary",
          command=lambda: open_hist_item()).pack(side="left")
    mkbtn(hbar2, "打开文件夹", role="accent",
          command=lambda: open_hist_folder()).pack(side="left", padx=(6, 0))
    mkbtn(hbar2, "设置分组…", role="accent",
          command=lambda: _assign_group()).pack(side="left", padx=(6, 0))
    mkbtn(hbar2, "删除选中", role="danger",
          command=lambda: delete_selected_history()).pack(side="left", padx=(6, 0))
    mkbtn(hbar2, "清空全部", role="danger",
          command=lambda: clear_history()).pack(side="right")

    # 表格填满 hbody（hbody 已在上面创建，工具栏都挂在 histf 上）
    hsb = ttk.Scrollbar(hbody, command=hist.yview)
    hist.configure(yscrollcommand=hsb.set)
    hsb.pack(side="right", fill="y")
    hist.pack(side="left", fill="both", expand=True)

    def _collect_files_of(items: list) -> list:
        """把若干条历史记录涉及的产物文件收集出来。"""
        out = []
        for it in items:
            for key in ("files", "word_files"):
                for f in (it.get(key) or []):
                    p = Path(str(f))
                    if p.is_file():
                        out.append(p)
        return out

    def delete_selected_history():
        """删除选中的历史记录（可选同时删掉对应文件）。"""
        sel = hist.selection()
        if not sel:
            messagebox.showinfo("请先选择", "请先在列表里选中要删除的记录（可按住 Ctrl 多选）。")
            return
        items = [hist_map.get(i) for i in sel]
        items = [it for it in items if it]
        if not items:
            return

        files = _collect_files_of(items)
        # 弹窗让用户决定要不要连文件一起删
        dlg = tk.Toplevel(root)
        dlg.title("删除历史记录")
        dlg.geometry("440x260")
        dlg.transient(root)
        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="将删除 %d 条历史记录" % len(items),
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        names = [str(it.get("source_name") or "") for it in items]
        ttk.Label(f, text="\n".join("· " + n for n in names[:5])
                  + ("\n…还有 %d 条" % (len(names) - 5) if len(names) > 5 else ""),
                  foreground="#555", justify="left").pack(anchor="w", pady=(6, 0))

        ttk.Label(f, text="这些记录对应 %d 个输出文件：" % len(files),
                  foreground="#555").pack(anchor="w", pady=(10, 0))
        ttk.Label(f, text="\n".join("· " + p.name for p in files[:4])
                  + ("\n…还有 %d 个" % (len(files) - 4) if len(files) > 4 else "")
                  or "（没有可删的文件）",
                  foreground="#777", justify="left").pack(anchor="w")

        del_files = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="同时删除这些输出文件（不可恢复）",
                        variable=del_files).pack(anchor="w", pady=(12, 0))

        def do_delete():
            removed_files = 0
            failed = []
            if del_files.get():
                for p in files:
                    try:
                        p.unlink()
                        removed_files += 1
                    except Exception:
                        failed.append(p.name)
            # 从 history.json 里剔除
            keep = []
            times = {str(it.get("time")) for it in items}
            for it in load_history():
                if str(it.get("time")) in times:
                    continue
                keep.append(it)
            try:
                HISTORY_FILE.write_text(
                    json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            _refresh_group_options()
            refresh_history()
            msg = "已删除 %d 条记录" % len(items)
            if del_files.get():
                msg += "，同时删除 %d 个文件" % removed_files
                if failed:
                    msg += "（%d 个删除失败，可能被占用）" % len(failed)
            status.configure(text=msg, foreground="#2a7d2a")
            log("")
            log("🗑 " + msg)
            dlg.destroy()

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="删除", command=do_delete).pack(side="right")
        ttk.Button(row, text="取消", command=dlg.destroy).pack(side="right", padx=8)

    def clear_history():
        if not messagebox.askyesno(
                "清空历史",
                "确定清空【全部】翻译历史记录吗？\n（不会删除已翻译的文件）\n\n"
                "只想删几条的话，请用「删除选中」。"):
            return
        try:
            HISTORY_FILE.write_text("[]", encoding="utf-8")
        except Exception:
            pass
        refresh_history()
        status.configure(text="历史已清空", foreground="#2a7d2a")

    # 到这里两个处理函数都已定义，可以安全创建按钮了
    # 按钮已在上面一次建齐（见「操作按钮」注释）

    hist_map: dict[str, dict] = {}

    def refresh_history():
        for i in hist.get_children():
            hist.delete(i)
        hist_map.clear()
        # 分组过滤
        try:
            want = grp_var.get()
        except Exception:
            want = "全部"
        for idx, it in enumerate(load_history()):
            # 关键词过滤（文件名 / 引擎 / 分组都能搜）
            try:
                kw = q_var.get().strip().lower()
            except Exception:
                kw = ""
            if kw:
                hay = " ".join([
                    str(it.get("source_name") or ""),
                    str(it.get("engine") or ""),
                    str(it.get("group") or ""),
                    str(it.get("time") or ""),
                ]).lower()
                if kw not in hay:
                    continue
            g = str(it.get("group") or "").strip()
            has_word = bool(it.get("word_files"))
            if want == "未分组" and g:
                continue
            elif want == "Word":
                # 内置 Word 分组：产出了 Word 的都算，不管自定义分组
                if not has_word:
                    continue
                g = g or "Word"
            elif want not in ("全部", "未分组") and g != want:
                continue
            mode = str(it.get("mode") or "")
            iid = "h%d" % idx
            hist_map[iid] = it
            # 产出类型：PDF / Word / 两者
            if mode == "word":
                kind = "Word"
            else:
                has_w = bool(it.get("word_files"))
                kind = "PDF+Word" if has_w else "PDF"
            issues = it.get("issues") or []
            warn = ("⚠%d" % len(issues)) if issues else ""
            tags = []
            if idx % 2:
                tags.append("odd")
            if issues:
                tags.append("warnrow")
            hist.insert("", "end", iid=iid, values=(
                it.get("time", ""),
                it.get("source_name", ""),
                kind,
                ENGINE_LABEL.get(str(it.get("engine")), str(it.get("engine") or "")),
                MODE_LABELS.get(mode, mode) if mode != "word" else "—",
                g or "未分组",
                warn,
            ), tags=tuple(tags))
        # 「上次的文件夹」按钮：有可用记录才亮
        try:
            btn_recent.configure(state="normal" if _recent_folder() else "disabled")
        except Exception:
            pass

    def _hist_target():
        """返回 (记录, 要打开的路径)。若该记录只产出了 Word，就返回 Word。"""
        sel = hist.selection()
        if not sel:
            return None, None
        it = hist_map.get(sel[0])
        if not it:
            return None, None
        files = it.get("files") or []
        words = it.get("word_files") or []
        if files:
            target = files[0]
        elif words:
            # 纯 Word 转换的记录：打开第一个 Word 文件
            target = words[0]
        else:
            target = it.get("out_dir")
        return it, target

    def _hist_word_target():
        """该记录的 Word 文件（若有）。"""
        sel = hist.selection()
        if not sel:
            return None
        it = hist_map.get(sel[0]) or {}
        words = it.get("word_files") or []
        if not words:
            return None
        for w in words:
            if Path(str(w)).exists():
                return str(w)
        return str(words[0])

    def open_hist_item(_evt=None):
        it, target = _hist_target()
        if not target:
            # 没选中行时给个明确提示，而不是静默无反应
            if hist.get_children():
                messagebox.showinfo("请先选择", "请先在列表里选中一条记录，再点「打开选中」。")
            else:
                messagebox.showinfo("暂无记录", "还没有翻译记录。\n可点「打开文件夹」查看输出目录。")
            return
        try:
            if Path(target).exists():
                used = open_file_with(Path(target), load_config())
                status.configure(text="已用%s打开" % used, foreground="#2a7d2a")
            else:
                messagebox.showinfo("文件不存在", "该结果已被移动或删除：\n%s" % target)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_hist_folder(_evt=None):
        """打开文件夹。

        没选中行、或历史为空时，退回「设置里的输出目录」——
        这样任何时候点它都能打开，而不是静默无反应。
        """
        it, target = _hist_target()
        d = None
        if target:
            p = Path(target)
            d = p.parent if p.is_file() else p
        if d is None:
            d = resolve_output_dir(load_config())
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            if d.exists():
                os.startfile(str(d))  # type: ignore[attr-defined]
                status.configure(text="已打开：" + str(d), foreground="#2a7d2a")
            else:
                messagebox.showinfo("文件夹不存在", str(d))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def copy_hist_path(_evt=None):
        it, target = _hist_target()
        if not target:
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(str(target))
            status.configure(text="路径已复制", foreground="#2a7d2a")
        except Exception:
            pass

    # 右键菜单：可选打开方式
    hist_menu = tk.Menu(root, tearoff=0)
    hist_menu.add_command(label="打开（按设置的打开方式）", command=open_hist_item)
    hist_menu.add_command(label="用 WPS 打开", command=lambda: _hist_open_wps())

    def _open_word_file():
        w = _hist_word_target()
        if not w:
            messagebox.showinfo("没有 Word", "这条记录没有 Word 文件。")
            return
        try:
            os.startfile(w)  # type: ignore[attr-defined]
            status.configure(text="已打开 Word", foreground="#2a7d2a")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    hist_menu.add_command(label="打开 Word 文件", command=_open_word_file)

    def _retranslate_issues():
        """只重翻有质量提示的页，快速修正问题页。"""
        sel = hist.selection()
        if not sel:
            messagebox.showinfo("请先选择", "请先在列表里选中一条记录。")
            return
        it = hist_map.get(sel[0]) or {}
        src = str(it.get("source") or "")
        if not src or not Path(src).is_file():
            messagebox.showinfo("源文件不在", "找不到原始 PDF：\n%s" % src)
            return
        # 从历史里找出问题页（记录里存的是类型，没有页码时提示手动填）
        pages = str(it.get("issue_pages") or "").strip()
        if not pages:
            pages = simpledialog.askstring(
                "重翻指定页",
                "要重翻哪些页？（如 3,7,12；留空取消）\n\n"
                "提示：可在「运行日志」的质量检查结果里看到可疑页号。",
                parent=root) or ""
        if not pages.strip():
            return
        cfg = load_config()
        cfg["pages"] = pages.strip()
        cfg["ignore_cache"] = True          # 强制重翻，否则会命中旧缓存
        save_config(cfg)
        log("")
        log("🔁 重翻指定页：%s（已自动开启「忽略缓存」）" % pages.strip())
        start_file(src)

    hist_menu.add_command(label="重翻指定页…", command=_retranslate_issues)
    hist_menu.add_command(label="打开所在文件夹", command=open_hist_folder)
    hist_menu.add_separator()
    hist_menu.add_command(label="设置分组…", command=_assign_group)
    hist_menu.add_command(label="复制文件路径", command=copy_hist_path)

    def _hist_open_wps():
        it, target = _hist_target()
        if not target:
            return
        p = Path(target)
        if not p.exists():
            messagebox.showinfo("文件不存在", str(p))
            return
        exe = _find_wps()
        if not exe:
            messagebox.showinfo("未找到 WPS", "本机未检测到 WPS。\n可在「设置 → 打开方式」里指定 WPS 路径。")
            return
        try:
            subprocess.Popen([exe, str(p)], creationflags=0x08000000)
            status.configure(text="已用 WPS 打开", foreground="#2a7d2a")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def _hist_popup(evt):
        row = hist.identify_row(evt.y)
        if row:
            hist.selection_set(row)
            hist_menu.tk_popup(evt.x_root, evt.y_root)

    hist.bind("<Button-3>", _hist_popup)

    hist.bind("<Double-1>", open_hist_item)

    ui_queue: "queue.Queue[tuple]" = queue.Queue()
    state = {"running": False, "last_dir": None, "last_src_dir": None}

    def log(line: str):
        ui_queue.put(("log", line))

    def append_log(line: str):
        logtxt.insert("end", line + "\n")
        logtxt.see("end")

    # ---------- 拖放支持（Windows WM_DROPFILES） ----------
    # 注意：ctypes 必须完整声明 restype/argtypes。
    # 否则 64 位下句柄与指针会被按 int32 截断，CallWindowProcW 会触发
    # access violation，破坏窗口消息链 —— 表现为界面点不动（曾出现的卡死 bug）。
    def enable_drop():
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)

            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM,
            )

            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM,
            ]
            user32.DefWindowProcW.restype = LRESULT
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            ]
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [
                wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT,
            ]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            WM_DROPFILES = 0x0233
            GWL_WNDPROC = -4

            # 顶层窗口（带标题栏的那个）才是 shell 投递 WM_DROPFILES 的目标
            frame = root.frame()
            try:
                hwnd = int(frame, 16) if str(frame).startswith("0x") else int(frame)
            except Exception:
                hwnd = int(root.winfo_id())
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
                        for p in paths:
                            if p.lower().endswith(".pdf"):
                                ui_queue.put(("file", p))
                                break
                        return 0
                except Exception:
                    pass
                # 其余消息交还原窗口过程；取不到就退回默认处理
                if old_proc:
                    return user32.CallWindowProcW(
                        ctypes.c_void_p(old_proc), h, msg, wparam, lparam
                    )
                return user32.DefWindowProcW(h, msg, wparam, lparam)

            cb = WNDPROC(handler)
            user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
            root._drop_cb = cb        # 防止回调被 GC 回收
            root._drop_hwnd = hwnd
            root._drop_old = old_proc
            return True
        except Exception:
            return False

    def disable_drop():
        """卸载拖放钩子。

        主题热切换要销毁并重建整个界面，如果旧窗口过程的回调还挂着，
        窗口一销毁回调就会读到野指针 → access violation（实测崩过）。
        所以重建前必须先还原原来的窗口过程。
        """
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = getattr(root, "_drop_hwnd", None)
            old = getattr(root, "_drop_old", None)
            if hwnd is not None and old:
                user32 = ctypes.windll.user32
                user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                                     ctypes.c_void_p]
                user32.SetWindowLongPtrW.restype = ctypes.c_void_p
                user32.SetWindowLongPtrW(hwnd, -4, ctypes.c_void_p(old))
            for attr in ("_drop_cb", "_drop_hwnd", "_drop_old"):
                try:
                    delattr(root, attr)
                except Exception:
                    pass
        except Exception:
            pass

    dropped_ok = enable_drop()

    def start_file(path: str, on_finish=None):
        """选好文件后先弹「确认」窗口，让用户确认版本与文件名，再开始翻译。

        批量模式下（on_finish 不为空）跳过确认窗口，直接用当前设置跑，
        跑完回调 on_finish 继续下一个。
        """
        if state["running"]:
            messagebox.showinfo("请稍候", "正在翻译中，请等待当前任务完成。")
            return
        pdf = Path(path)
        if not pdf.exists() or pdf.suffix.lower() != ".pdf":
            messagebox.showwarning("文件无效", "请选择 PDF 文件。")
            return

        cfg = load_config()
        if on_finish is not None:
            # 批量：不问，直接用设置里的选项开跑
            _launch_translate(pdf, cfg, on_finish=on_finish)
        else:
            confirm_and_start(pdf, cfg)

    def confirm_and_start(pdf: Path, cfg: dict):
        """翻译前的确认窗口：输出版本 + 输出文件名（下拉可选）。"""
        dlg = tk.Toplevel(root)
        dlg.title("确认翻译设置")
        dlg.geometry("620x560")
        dlg.minsize(520, 340)          # 允许缩放，内容多时也不会被裁掉
        dlg.transient(root)

        base_name = pdf.stem
        mode_labels = [("mono", "中文版（仅译文）"),
                       ("dual", "双语对照版"),
                       ("both", "中文版 + 双语对照版")]
        mode_now = str(cfg.get("output_mode") or "mono").lower()
        if mode_now not in ("mono", "dual", "both"):
            mode_now = "mono"
        mode_var = tk.StringVar(value=mode_now)

        # 内容放进可滚动容器：小窗口下也能滚到下面的选项
        outer2 = ttk.Frame(dlg)
        outer2.pack(fill="both", expand=True)
        cv2 = tk.Canvas(outer2, highlightthickness=0, borderwidth=0)
        vsb2 = ttk.Scrollbar(outer2, orient="vertical", command=cv2.yview)
        cv2.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side="right", fill="y")
        cv2.pack(side="left", fill="both", expand=True)

        frm2 = ttk.Frame(cv2, padding=16)
        frm2.columnconfigure(1, weight=1)
        wid2 = cv2.create_window((0, 0), window=frm2, anchor="nw")

        frm2.bind("<Configure>", lambda e: cv2.configure(scrollregion=cv2.bbox("all")))
        cv2.bind("<Configure>", lambda e: cv2.itemconfigure(wid2, width=e.width))

        def _wheel2(e):
            try:
                cv2.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        def _bind2(_e=None):
            cv2.bind_all("<MouseWheel>", _wheel2)
        def _unbind2(_e=None):
            try:
                cv2.unbind_all("<MouseWheel>")
            except Exception:
                pass
        cv2.bind("<Enter>", _bind2)
        cv2.bind("<Leave>", _unbind2)

        # 底部按钮固定（不随内容滚动）
        bar2 = ttk.Frame(dlg, padding=(16, 8, 16, 12))
        bar2.pack(side="bottom", fill="x")

        rr = 0

        ttk.Label(frm2, text="要翻译的文件").grid(row=rr, column=0, sticky="w", pady=(0, 2))
        ttk.Label(frm2, text=pdf.name, foreground="#3a6ea5",
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=rr, column=1, sticky="w", pady=(0, 2))
        rr += 1

        # ---- 输出类型（PDF / Word / 两个都要）----
        ttk.Label(frm2, text="输出类型").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        fmt_labels = [("pdf", "PDF（默认）"),
                      ("word", "Word 文档"),
                      ("both", "PDF + Word 都要")]
        fmt_box = ttk.Combobox(frm2, values=[x[1] for x in fmt_labels],
                               state="readonly", width=22)
        fmt_now = str(cfg.get("output_format") or "pdf").lower()
        fmt_box.set(next((lab for v, lab in fmt_labels if v == fmt_now), fmt_labels[0][1]))
        fmt_box.grid(row=rr, column=1, sticky="w", pady=(12, 0))
        rr += 1
        fmt_hint = ttk.Label(frm2, text="", foreground="#777", wraplength=360, justify="left")
        fmt_hint.grid(row=rr, column=1, sticky="w")
        rr += 1

        def _upd_fmt_hint(*_a):
            v = next((val for val, lab in fmt_labels if lab == fmt_box.get()), "pdf")
            fmt_hint.configure(text={
                "pdf": "输出翻译后的 PDF",
                "word": "把译文导出成 Word（适合改内容；版式不保证 100% 一致）",
                "both": "PDF 和 Word 都生成",
            }.get(v, ""))
        fmt_box.bind("<<ComboboxSelected>>", _upd_fmt_hint)
        _upd_fmt_hint()

        # ---- 输出版本 ----
        ttk.Label(frm2, text="输出版本").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        mf2 = ttk.Frame(frm2)
        mf2.grid(row=rr, column=1, sticky="w", pady=(12, 0))
        for val, lab in mode_labels:
            ttk.Radiobutton(mf2, text=lab, value=val, variable=mode_var).pack(anchor="w")
        rr += 1

        # ---- 输出文件名（下拉：原名+版本 / 原名 / 自定义） ----
        ttk.Label(frm2, text="输出文件名").grid(row=rr, column=0, sticky="w", pady=(12, 0))

        def default_name_for(mode: str) -> str:
            suffix = {"mono": "中文版", "dual": "双语对照版", "both": ""}.get(mode, "")
            return base_name + suffix if suffix else base_name

        name_var = tk.StringVar(value=default_name_for(mode_now))
        # 记录当前名字是不是「自动生成的」，用于判断切换版本时要不要跟着改。
        # 用户手动改过就不再覆盖。
        name_state = {"auto": True}
        nf = ttk.Frame(frm2)
        nf.grid(row=rr, column=1, sticky="we", pady=(12, 0))
        nf.columnconfigure(0, weight=1)
        name_box = ttk.Combobox(nf, textvariable=name_var, state="normal")
        name_box.grid(row=0, column=0, sticky="we")
        rr += 1
        hint = ttk.Label(frm2, text="", foreground="#777", wraplength=360, justify="left")
        hint.grid(row=rr, column=1, sticky="w")
        rr += 1

        def refresh_choices(*_a):
            """下拉选项随「输出版本」变化。"""
            m = mode_var.get()
            base_opts = [base_name + "中文版", base_name + "双语对照版", base_name]
            if m == "mono":
                opts = [base_name + "中文版", base_name]
            elif m == "dual":
                opts = [base_name + "双语对照版", base_name]
            else:
                opts = base_opts
            opts = list(dict.fromkeys(opts))          # 去重保序
            name_box.configure(values=opts)
            hint.configure(
                text="留空则自动命名：原文件名 + 版本后缀（如 %s）。"
                     "也可从下拉里选，或直接输入自定义名称。" % (
                         base_name + (".mono" if m == "mono" else ".dual" if m == "dual" else ".mono/.dual"))
            )

        def on_mode_change(*_a):
            """切换输出版本时，让输出文件名跟着变。

            原先只在名字为空时才更新，导致切换版本后输入框里还是旧名字，
            用户必须自己去下拉里再选一次。现在改成：
            只要名字仍是「自动生成的那一个」，就跟着版本走；
            用户手动改过（变成自定义名）则保留不动。
            """
            m = mode_var.get()
            new_name = default_name_for(m)
            cur_name = name_var.get().strip()
            auto_names = {default_name_for("mono"), default_name_for("dual"),
                          default_name_for("both"), base_name, ""}
            if name_state["auto"] or cur_name in auto_names:
                _updating["on"] = True
                try:
                    name_var.set(new_name)
                finally:
                    _updating["on"] = False
                name_state["auto"] = True
            refresh_choices()

        mode_var.trace_add("write", on_mode_change)
        refresh_choices()

        # 用变量追踪代替 <KeyRelease>：程序化赋值也能被感知，
        # 但要用 _updating 旗标区分「我们自己在改」和「用户在手打」。
        _updating = {"on": False}

        def _watch_name(*_a):
            if _updating["on"]:
                return
            cur = name_var.get().strip()
            if cur and cur != default_name_for(mode_var.get()):
                name_state["auto"] = False

        name_var.trace_add("write", _watch_name)

        # ---- 保存位置 ----
        ttk.Label(frm2, text="保存位置").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        dir_var2 = tk.StringVar(value=str(resolve_output_dir(cfg)))
        df2 = ttk.Frame(frm2)
        df2.grid(row=rr, column=1, sticky="we", pady=(12, 0))
        df2.columnconfigure(0, weight=1)
        ttk.Entry(df2, textvariable=dir_var2).grid(row=0, column=0, sticky="we")
        ttk.Button(df2, text="浏览…", command=lambda: _pick_dir(dir_var2)).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Button(df2, text="打开", command=lambda: _open_dir(dir_var2.get())).grid(
            row=0, column=2, padx=(6, 0))
        rr += 1

        # ---- 排版方式 ----
        ttk.Label(frm2, text="排版方式").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        lay_var = tk.StringVar(value="auto")
        lf2 = ttk.Frame(frm2)
        lf2.grid(row=rr, column=1, sticky="w", pady=(12, 0))
        for val, lab, tip in (
            ("auto", "自动（推荐）", "默认设置，适合大多数文档"),
            ("split", "拆分短行", "目录/表格列表挤成一坨时用"),
            ("compat", "兼容模式", "版面错乱、文字重叠时试"),
        ):
            ttk.Radiobutton(lf2, text=lab, value=val, variable=lay_var).pack(anchor="w")
        rr += 1
        lay_hint = ttk.Label(frm2, text="", foreground="#777", wraplength=360, justify="left")
        lay_hint.grid(row=rr, column=1, sticky="w")
        rr += 1

        def _upd_lay_hint(*_a):
            tips = {"auto": "默认设置，适合大多数文档",
                    "split": "把过短的文本行拆成独立段落——目录、表格列表挤成一坨时选它",
                    "compat": "跳过清理与富文本处理——版面错乱、文字重叠时试；文件会变大"}
            lay_hint.configure(text=tips.get(lay_var.get(), ""))
        lay_var.trace_add("write", _upd_lay_hint)
        _upd_lay_hint()

        # ---- 目录页处理 ----
        ttk.Label(frm2, text="目录页").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        toc_var = tk.BooleanVar(value=bool(cfg.get("skip_toc", False)))
        toc_frame = ttk.Frame(frm2)
        toc_frame.grid(row=rr, column=1, sticky="w", pady=(12, 0))
        ttk.Checkbutton(toc_frame, text="跳过目录页（保留原样，避免条目被合并）",
                        variable=toc_var).pack(anchor="w")
        rr += 1
        toc_hint = ttk.Label(frm2, text="目录/表格列表/图表列表这类页面会被合并成一段，"
                                        "勾选后可保留原样",
                             foreground="#777", wraplength=360, justify="left")
        toc_hint.grid(row=rr, column=1, sticky="w")
        rr += 1

        # ---- 只翻译指定页 ----
        ttk.Label(frm2, text="翻译范围").grid(row=rr, column=0, sticky="w", pady=(12, 0))
        pf3 = ttk.Frame(frm2)
        pf3.grid(row=rr, column=1, sticky="we", pady=(12, 0))
        pf3.columnconfigure(0, weight=1)
        pages_var2 = tk.StringVar(value=str(cfg.get("pages") or ""))
        ttk.Entry(pf3, textvariable=pages_var2).grid(row=0, column=0, sticky="we")
        ttk.Button(pf3, text="全部", width=6,
                   command=lambda: pages_var2.set("")).grid(row=0, column=1, padx=(6, 0))
        rr += 1
        ttk.Label(frm2, text="留空 = 全部；也可填 1-10,15,20-（末页用 - 表示）",
                  foreground="#777", wraplength=360, justify="left").grid(
            row=rr, column=1, sticky="w")
        rr += 1

        result = {"go": False}

        def do_start():
            # 把选择写回配置
            c = load_config()
            c["output_mode"] = mode_var.get()
            nm = name_var.get().strip()
            # 若与默认名相同，就视为「不自定义」，让程序用标准后缀命名
            if nm == default_name_for(mode_var.get()):
                c["output_name"] = ""
            else:
                c["output_name"] = nm
            # 保存位置
            nd = dir_var2.get().strip()
            c["output_dir"] = "" if nd == str(DEFAULT_OUTPUT_DIR) else nd
            # 排版方式
            lv = lay_var.get()
            c["split_short_lines"] = (lv == "split")
            c["enhance_compatibility"] = (lv == "compat")
            # 翻译范围
            c["pages"] = pages_var2.get().strip()
            # 目录页处理
            c["skip_toc"] = bool(toc_var.get())
            if c["skip_toc"] and not c["pages"]:
                # 自动识别目录页，填进 pages 的排除列表
                try:
                    import qa as _qa
                    spec = _qa.toc_pages_spec(pdf)
                    if spec:
                        # 用「全部页 - 目录页」表达为排除
                        _all = list(range(1, 4000))
                        _skip = {int(x) for x in spec.split(",") if x.strip().isdigit()}
                        try:
                            import fitz as _fz
                            with _fz.open(str(pdf)) as _d:
                                _all = list(range(1, len(_d) + 1))
                        except Exception:
                            pass
                        keep = [str(n) for n in _all if n not in _skip]
                        c["pages"] = ",".join(keep) if keep else ""
                except Exception:
                    pass
            # 输出类型：pdf / word / both
            c["output_format"] = next(
                (v for v, lab in fmt_labels if lab == fmt_box.get()), "pdf")
            save_config(c)
            refresh_info()
            result["go"] = True
            try:
                if dlg.grab_current() is dlg:
                    dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()
            _launch_translate(pdf, c)

        def do_cancel():
            try:
                if dlg.grab_current() is dlg:
                    dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", do_cancel)
        # 按钮放在固定的底部栏（不随内容滚动）
        ttk.Button(bar2, text="开始翻译", command=do_start).pack(side="right")
        ttk.Button(bar2, text="取消", command=do_cancel).pack(side="right", padx=8)
        ttk.Label(bar2, text="确认后开始翻译", foreground="#777").pack(side="left")
        dlg.after(80, lambda: _safe_grab(dlg))

    def _safe_grab(w):
        try:
            if w.winfo_exists():
                w.grab_set()
        except Exception:
            pass

    def _launch_translate(pdf: Path, cfg: dict, on_finish=None):
        """真正启动翻译。on_finish 用于批量模式串下一个文件。"""
        out_dir = resolve_output_dir(cfg)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存位置不可用", "无法创建目录：\n%s\n\n%s" % (out_dir, exc))
            return

        # 批量模式下保留上一轮的日志，方便回看
        if on_finish is None:
            logtxt.delete("1.0", "end")
        state["running"] = True
        prog.configure(value=0)
        pct.configure(text="0%")
        stage_lbl.configure(text="准备中…")
        status.configure(text="翻译中…", foreground="#b8860b")
        btn_pick.configure(state="disabled")

        state["t_start"] = time.time()
        # 切到「步骤耗时」页，让用户实时看到在跑哪一步
        try:
            nb.select(stepf)
            _clear_steps()
            step_sum.configure(text="准备中…", foreground="#b8860b")
        except Exception:
            pass

        def on_prog(v: float, stage: str):
            ui_queue.put(("prog", v, stage))

        def on_stage(name: str):
            ui_queue.put(("stage", name))

        def done(ok, out_file, err, timing=None):
            ui_queue.put(("done", ok, out_file, err, str(out_dir),
                          on_finish, timing or {}))

        threading.Thread(
            target=run_translate,
            args=(pdf, cfg, out_dir, log, on_prog, done, on_stage),
            daemon=True,
        ).start()

    def pick():
        """选 PDF。多选 = 批量翻译。"""
        last = state.get("last_src_dir") or ""
        paths = filedialog.askopenfilenames(
            title="选择要翻译的 PDF（可按住 Ctrl / Shift 多选，批量翻译）",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            initialdir=_existing_dir(last, Path.home() / "Documents", APP_DIR),
        )
        if not paths:
            return
        files = [str(x) for x in paths]
        state["last_src_dir"] = str(Path(files[0]).parent)
        if len(files) == 1:
            start_file(files[0])
        else:
            batch_files(files)

    def batch_files(files: list[str]):
        """批量翻译：按顺序排队，逐个跑（避免同时开太多进程抢网络）。"""
        total = len(files)
        if total == 0:
            return
        if not messagebox.askyesno(
                "批量翻译",
                "共选中 %d 个文件，将依次翻译。\n\n现在开始吗？" % total):
            return
        state["batch"] = list(files)
        state["batch_total"] = total
        state["batch_done"] = 0
        log("")
        log("═══ 批量翻译：共 %d 个文件 ═══" % total)
        _next_batch()

    def _next_batch():
        """跑队列里的下一个文件。"""
        q = state.get("batch") or []
        if not q:
            log("")
            log("═══ 批量翻译全部完成 ═══")
            status.configure(text="批量完成", foreground="#2a7d2a")
            return
        nxt = q.pop(0)
        state["batch"] = q
        done_n = int(state.get("batch_done") or 0) + 1
        state["batch_done"] = done_n
        log("")
        log("──── [%d/%d] %s ────" % (done_n, state.get("batch_total") or 0,
                                      Path(nxt).name))
        start_file(nxt, on_finish=_next_batch)

    # 「打开输出文件夹」已移到「历史记录」页（那里有打开方式与文件夹按钮），
    # 主界面不再重复。


    def open_glossary_editor():
        """可视化增删术语（改的是 glossary.csv）。"""
        # 已开着就聚焦，避免开出多个
        existing = glossary_win.get("win")
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
            glossary_win["win"] = None

        gef = tk.Toplevel(root)
        gef.title("术语表管理")
        gef.geometry("760x600")
        gef.minsize(600, 400)
        gef.transient(root)
        glossary_win["win"] = gef

        def close_glossary():
            """关闭术语窗口（设置窗口已不再持有模态锁，这里只需清理自身）。"""
            try:
                if gef.grab_current() is gef:
                    gef.grab_release()
            except Exception:
                pass
            try:
                glossary_win["win"] = None
                gef.destroy()
            except Exception:
                pass

        gef.protocol("WM_DELETE_WINDOW", close_glossary)

        gpath = APP_DIR / "glossary.csv"

        # ---- 顶部：搜索 + 导入导出 + 关闭 ----
        top = ttk.Frame(gef, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="搜索").pack(side="left")
        q_var = tk.StringVar()
        q_entry = ttk.Entry(top, textvariable=q_var, width=24)
        q_entry.pack(side="left", padx=(6, 4))
        ttk.Button(top, text="清除", width=6,
                   command=lambda: q_var.set("")).pack(side="left")
        ttk.Label(top, text="（中英文均可）", foreground="#888").pack(side="left", padx=(4, 0))
        ttk.Button(top, text="关闭", command=close_glossary).pack(side="right")
        ttk.Button(top, text="导出 CSV", command=lambda: _gl_export()).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="导入 CSV", command=lambda: _gl_import()).pack(side="right", padx=(6, 0))

        # 状态栏（显示条数 / 搜索结果）
        statusbar = ttk.Frame(gef, padding=(12, 0, 12, 8))
        statusbar.pack(side="bottom", fill="x")
        info = ttk.Label(statusbar, text="", foreground="#666")
        info.pack(side="left")

        body = ttk.Frame(gef, padding=(12, 0, 12, 6))
        body.pack(fill="both", expand=True)
        cols = ("source", "target")
        tree = ttk.Treeview(body, columns=cols, show="headings")
        tree.heading("source", text="英文（原文）")
        tree.heading("target", text="中文（译文）")
        tree.column("source", width=320, anchor="w")
        tree.column("target", width=260, anchor="w")
        tsb = ttk.Scrollbar(body, command=tree.yview)
        tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        rows: list[list[str]] = []

        def load_rows():
            rows.clear()
            try:
                with gpath.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        s = (row.get("source") or "").strip()
                        t = (row.get("target") or "").strip()
                        if s and t:
                            rows.append([s, t])
            except Exception:
                pass
            refresh_tree()

        def refresh_tree(*_a):
            """按搜索词过滤后显示（中英文都能搜）。"""
            q = q_var.get().strip().lower()
            for i in tree.get_children():
                tree.delete(i)
            shown = 0
            for idx, (s, t) in enumerate(rows):
                if q and q not in s.lower() and q not in t.lower():
                    continue
                tree.insert("", "end", iid="g%d" % idx, values=(s, t))
                shown += 1
            if q:
                info.configure(text="找到 %d / %d 条（关键词：%s）" % (shown, len(rows), q))
            else:
                info.configure(text="共 %d 条术语" % len(rows))

        # 输入即时过滤
        q_var.trace_add("write", lambda *a: refresh_tree())
        q_entry.bind("<Escape>", lambda e: q_var.set(""))

        def save_rows():
            try:
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                w.writerow(["source", "target", "tgt_lng"])
                for s, t in rows:
                    w.writerow([s, t, "zh_cn"])
                gpath.write_text(buf.getvalue(), encoding="utf-8-sig")
                return True
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))
                return False

        # 编辑区
        ef = ttk.LabelFrame(gef, text="新增 / 修改", padding=10)
        ef.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Label(ef, text="英文").grid(row=0, column=0, sticky="w")
        src_var = tk.StringVar()
        ttk.Entry(ef, textvariable=src_var, width=34).grid(row=0, column=1, padx=6, sticky="we")
        ttk.Label(ef, text="中文").grid(row=0, column=2, sticky="w")
        dst_var = tk.StringVar()
        ttk.Entry(ef, textvariable=dst_var, width=26).grid(row=0, column=3, padx=6, sticky="we")
        ef.columnconfigure(1, weight=1)
        ef.columnconfigure(3, weight=1)

        def add_or_update():
            s = src_var.get().strip()
            t = dst_var.get().strip()
            if not s or not t:
                messagebox.showwarning("请填写", "英文和中文都要填。")
                return
            low = s.lower()
            for i, (a, _b) in enumerate(rows):
                if a.lower() == low:
                    rows[i] = [s, t]
                    break
            else:
                rows.append([s, t])
            if save_rows():
                refresh_tree()
            src_var.set(""); dst_var.set("")

        def delete_sel():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("请先选择", "请先在列表里选中要删除的术语。")
                return
            idxs = sorted((int(x[1:]) for x in sel), reverse=True)
            for i in idxs:
                if 0 <= i < len(rows):
                    rows.pop(i)
            if save_rows():
                refresh_tree()

        def on_pick(_e=None):
            sel = tree.selection()
            if sel:
                idx = int(sel[0][1:])
                if 0 <= idx < len(rows):
                    src_var.set(rows[idx][0])
                    dst_var.set(rows[idx][1])
        tree.bind("<<TreeviewSelect>>", on_pick)

        bf = ttk.Frame(ef)
        bf.grid(row=0, column=4, padx=(10, 0))
        ttk.Button(bf, text="添加/更新", command=add_or_update).pack(side="left")
        ttk.Button(bf, text="删除选中", command=delete_sel).pack(side="left", padx=6)

        def _gl_import():
            f = filedialog.askopenfilename(
                title="导入术语表 CSV（source,target 两列）",
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
                initialdir=_existing_dir(APP_DIR))
            if not f:
                return
            try:
                n = 0
                with open(f, encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        s = (row.get("source") or "").strip()
                        t = (row.get("target") or "").strip()
                        if s and t:
                            rows.append([s, t]); n += 1
                if save_rows():
                    refresh_tree()
                messagebox.showinfo("导入完成", "已导入 %d 条。" % n)
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc))

        def _gl_export():
            f = filedialog.asksaveasfilename(
                title="导出术语表", defaultextension=".csv",
                initialfile="glossary_export.csv",
                filetypes=[("CSV 文件", "*.csv")],
                initialdir=_existing_dir(APP_DIR))
            if not f:
                return
            try:
                with open(f, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.writer(fh, lineterminator="\n")
                    w.writerow(["source", "target", "tgt_lng"])
                    for s, t in rows:
                        w.writerow([s, t, "zh_cn"])
                messagebox.showinfo("导出完成", "已导出 %d 条到：\n%s" % (len(rows), f))
            except Exception as exc:
                messagebox.showerror("导出失败", str(exc))

        load_rows()

    # ---------- 检查工具（术语表自检 / 扫描检测 / 译法一致性 / 段落对照）----------
    def open_inspect_tools():
        """一个窗口集中放几项检查，避免主界面按钮太多。"""
        qa_mod = _lazy_import("qa")
        if qa_mod is None:
            messagebox.showerror("模块缺失", "找不到 qa.py，程序包可能不完整。")
            return
        win = tk.Toplevel(root)
        win.title("检查工具")
        win.geometry("680x540")
        win.minsize(580, 400)
        win.transient(root)

        nb3 = ttk.Notebook(win)
        nb3.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        bar = ttk.Frame(win, padding=(14, 8, 14, 12))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, text="结果同时写入「运行日志」", foreground="#888").pack(side="left")
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

        def _text_tab(title: str):
            """建一个带文本框的页。

            返回 (页容器, 底部按钮栏, 文本框)。

            关键点：按钮必须 pack 到【按钮栏或页容器】上，绝不能 pack 到 Text 上。
            Text 是文本控件，往里塞按钮不会显示 —— 之前就是这么写的，
            导致「检查工具」四个页里的按钮全部看不见、也点不到。
            """
            pg = ttk.Frame(nb3)
            nb3.add(pg, text=title)
            body = ttk.Frame(pg, padding=(10, 10, 10, 4))
            body.pack(side="top", fill="both", expand=True)
            t = tk.Text(body, wrap="word", bg="#fbfbfb", fg="#222",
                        font=("Microsoft YaHei UI", 9), relief="flat")
            sb = ttk.Scrollbar(body, command=t.yview)
            t.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            t.pack(side="left", fill="both", expand=True)
            foot = ttk.Frame(pg, padding=(10, 0, 10, 10))
            foot.pack(side="bottom", fill="x")
            return pg, foot, t

        def _write(t, lines):
            t.configure(state="normal")
            t.delete("1.0", "end")
            for ln in lines:
                t.insert("end", ln + "\n")
            t.configure(state="disabled")

        # ---- 术语表自检 ----
        _pg1, t1_bar, t1 = _text_tab("术语表自检")
        def run_audit():
            r = qa_mod.audit_glossary(APP_DIR / "glossary.csv")
            lines = ["术语表共 %d 条" % r.get("total", 0), ""]
            conf = r.get("conflicts") or []
            lines.append("■ 同一英文有多个译法：%d 处" % len(conf))
            for s, vs in conf[:20]:
                lines.append("   %s → %s" % (s, " / ".join(vs)))
            lines.append("")
            dup = r.get("duplicate") or []
            lines.append("■ 重复条目（译法不同）：%d 处" % len(dup))
            for s, a, b in dup[:20]:
                lines.append("   %s → %s vs %s" % (s, a, b))
            lines.append("")
            lines.append("■ 缺一边：%d 处" % len(r.get("empty") or []))
            lines.append("■ 过短（易误替换）：%d 个" % len(r.get("too_short") or []))
            for s in (r.get("too_short") or [])[:10]:
                lines.append("   %s" % s)
            if not (conf or dup or r.get("empty")):
                lines.append("")
                lines.append("✅ 没有发现明显问题")
            _write(t1, lines)
            log("")
            log("【术语表自检】" + ("发现问题" if (conf or dup) else "OK")
                + "（共 %d 条）" % r.get("total", 0))
        ttk.Button(t1_bar, text="重新检查", command=lambda: run_audit()).pack(side="left")
        run_audit()

        # ---- 扫描版检测 ----
        _pg2, t2_bar, t2 = _text_tab("扫描版检测")
        def run_scan():
            f = filedialog.askopenfilename(
                title="选择要检测的 PDF", filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(state.get("last_src_dir"),
                                         Path.home() / "Documents", APP_DIR))
            if not f:
                return
            r = qa_mod.detect_scanned(f)
            lines = ["文件：%s" % Path(f).name,
                     "页数：%d" % r.get("pages", 0),
                     "平均每页字符数：%d" % r.get("chars_per_page", 0),
                     "抽样页图片数：%d" % r.get("images_sampled", 0), ""]
            if r.get("scanned"):
                lines += ["⚠️ 这份 PDF 很可能是扫描件（没有文字层）。",
                          "  翻译引擎读不到文字，结果会是空白。",
                          "  建议先用 OCR 工具转一遍（如 WPS / Adobe 的「识别文字」）。"]
            elif r.get("definitely_text"):
                lines.append("✅ 有正常文字层，可以放心翻译。")
            else:
                lines.append("文字层偏少，翻译效果可能不完整，建议先翻几页看看。")
            _write(t2, lines)
            log("")
            log("【扫描检测】%s → %s" % (Path(f).name,
                "扫描件" if r.get("scanned") else "有文字层"))
        ttk.Button(t2_bar, text="选择 PDF 检测…", command=run_scan).pack(side="left")

        # ---- 译法一致性 ----
        _pg3, t3_bar, t3 = _text_tab("译法一致性")
        def run_consist():
            sp = filedialog.askopenfilename(
                title="选择原文 PDF", filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(state.get("last_src_dir"),
                                         Path.home() / "Documents", APP_DIR))
            if not sp:
                return
            op = filedialog.askopenfilename(
                title="选择译文 PDF", filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(Path(sp).parent, APP_DIR))
            if not op:
                return
            iss = qa_mod.check_consistency(sp, op)
            lines = ["原文：%s" % Path(sp).name, "译文：%s" % Path(op).name, ""]
            if not iss:
                lines.append("✅ 没发现明显的译法问题")
            for i in iss:
                lines.append("· " + i.get("detail", ""))
            _write(t3, lines)
            log("")
            log("【译法一致性】发现 %d 条提示" % len(iss))
        ttk.Button(t3_bar, text="选择原文与译文…", command=run_consist).pack(side="left")

        # ---- 段落对照 ----
        _pg4, t4_bar, t4 = _text_tab("段落对照")
        def run_pairs():
            sp = filedialog.askopenfilename(
                title="选择原文 PDF", filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(state.get("last_src_dir"),
                                         Path.home() / "Documents", APP_DIR))
            if not sp:
                return
            op = filedialog.askopenfilename(
                title="选择译文 PDF", filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(Path(sp).parent, APP_DIR))
            if not op:
                return
            try:
                n = int(pg_var.get())
            except Exception:
                n = 1
            pairs = qa_mod.extract_pairs(sp, op, page=n)
            lines = ["第 %d 页，共配对 %d 段" % (n, len(pairs)), ""]
            for p in pairs:
                lines.append("[%d] 原文：%s" % (p["n"], p["src"]))
                lines.append("     译文：%s" % p["dst"])
                lines.append("")
            _write(t4, lines)
            log("")
            log("【段落对照】第 %d 页配对 %d 段" % (n, len(pairs)))

        pg_var = tk.StringVar(value="1")
        ttk.Label(t4_bar, text="页码").pack(side="left")
        ttk.Entry(t4_bar, textvariable=pg_var, width=6).pack(side="left", padx=6)
        ttk.Button(t4_bar, text="选择原文与译文…", command=run_pairs).pack(side="left")

    # ---------- 应用内 PDF 编辑器（像阅读器一样打开、直接标注）----------
    def open_pdf_viewer(initial: str = ""):
        """在软件里直接打开 PDF：翻页浏览 + 加文字/批注/高亮/旋转。"""
        pv = _lazy_import("pdf_viewer")
        if pv is None:
            messagebox.showerror("模块缺失", "找不到 pdf_viewer.py，程序包可能不完整。")
            return
        path = initial or filedialog.askopenfilename(
            title="打开要编辑的 PDF", filetypes=[("PDF 文件", "*.pdf")],
            initialdir=_existing_dir(state.get("last_src_dir"),
                                     Path.home() / "Documents", APP_DIR))
        if not path or not Path(path).is_file():
            return

        try:
            doc = pv.PdfDoc(path)
        except Exception as exc:
            messagebox.showerror("打不开", "无法打开该 PDF：\n%s" % exc)
            return

        win = tk.Toplevel(root)
        win.title("PDF 编辑器 — " + Path(path).name)
        win.geometry("1000x760")
        win.minsize(760, 560)
        win.transient(root)

        # ---- 顶部工具栏 ----
        top = ttk.Frame(win, padding=(10, 8, 10, 6))
        top.pack(side="top", fill="x")

        page_var = tk.IntVar(value=1)
        zoom_var = tk.DoubleVar(value=1.4)
        status_l = ttk.Label(top, text="", foreground="#666")

        def _refresh_page_label():
            status_l.configure(text="第 %d / %d 页    缩放 %.1fx"
                                    % (page_var.get(), doc.total, zoom_var.get()))

        def goto(delta: int):
            n = max(1, min(doc.total, page_var.get() + delta))
            page_var.set(n)
            redraw()

        ttk.Button(top, text="◀ 上一页", command=lambda: goto(-1)).pack(side="left")
        ttk.Button(top, text="下一页 ▶", command=lambda: goto(1)).pack(side="left", padx=(4, 10))
        ttk.Label(top, text="跳转").pack(side="left")
        pg_entry = ttk.Entry(top, textvariable=page_var, width=6)
        pg_entry.pack(side="left", padx=(4, 0))
        pg_entry.bind("<Return>", lambda e: redraw())
        ttk.Button(top, text="跳", width=3, command=redraw).pack(side="left", padx=(4, 10))

        ttk.Label(top, text="缩放").pack(side="left")
        for z, lab in ((1.0, "100%"), (1.4, "140%"), (2.0, "200%")):
            ttk.Button(top, text=lab, width=6,
                       command=lambda zz=z: (zoom_var.set(zz), redraw())).pack(
                side="left", padx=(4, 0))

        ttk.Button(top, text="保存为新文件…", command=lambda: _save()).pack(side="right")
        ttk.Button(top, text="关闭", command=lambda: _close()).pack(side="right", padx=(0, 8))

        # ---- 中间画布 ----
        mid = ttk.Frame(win)
        mid.pack(side="top", fill="both", expand=True, padx=10)
        canvas = tk.Canvas(mid, bg="#3a3a3a", highlightthickness=0)
        vsb = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        hsb = ttk.Scrollbar(mid, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)

        # ---- 底部操作栏 ----
        bot = ttk.Frame(win, padding=(10, 6, 10, 10))
        bot.pack(side="bottom", fill="x")

        _img_ref = {"img": None}     # 必须持引用，否则图片会被回收不显示

        def redraw():
            """渲染当前页到画布。"""
            try:
                png, w, h, scale = doc.render(page_var.get() - 1, zoom_var.get())
            except Exception as exc:
                messagebox.showerror("渲染失败", str(exc), parent=win)
                return
            try:
                img = tk.PhotoImage(data=png)
            except Exception as exc:
                messagebox.showerror("显示失败", str(exc), parent=win)
                return
            _img_ref["img"] = img
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=img)
            canvas.configure(scrollregion=(0, 0, w, h))
            _refresh_page_label()

        # 点击画布 → 按当前工具执行
        tool_var = tk.StringVar(value="text")
        text_var = tk.StringVar(value="")
        color_var = tk.StringVar(value="红色")

        def on_click(ev):
            """把画布坐标换算成 PDF 点坐标，再执行当前工具。"""
            cx = canvas.canvasx(ev.x)
            cy = canvas.canvasy(ev.y)
            scale = zoom_var.get()
            x_pt, y_pt = cx / scale, cy / scale
            tool = tool_var.get()
            try:
                if tool == "text":
                    t = text_var.get().strip()
                    if not t:
                        messagebox.showinfo("请输入文字", "先在下面输入要加的文字。", parent=win)
                        return
                    pv.add_text_at(doc, page_var.get() - 1, x_pt, y_pt, t,
                                   size=12, color=color_var.get())
                elif tool == "note":
                    t = text_var.get().strip() or "批注"
                    pv.add_note(doc, page_var.get() - 1, x_pt, y_pt, t)
                elif tool == "highlight":
                    pv.add_highlight(doc, page_var.get() - 1,
                                     (x_pt - 60, y_pt - 8, x_pt + 60, y_pt + 8),
                                     color="yellow")
                elif tool == "redact":
                    pv.redact_area(doc, page_var.get() - 1,
                                   (x_pt - 50, y_pt - 10, x_pt + 50, y_pt + 10))
                redraw()
                status.configure(text="已应用：" + tool, foreground="#2a7d2a")
            except Exception as exc:
                messagebox.showerror("操作失败", str(exc), parent=win)

        canvas.bind("<Button-1>", on_click)

        ttk.Label(bot, text="工具").pack(side="left")
        for val, lab in (("text", "加文字"), ("note", "加批注"),
                         ("highlight", "高亮"), ("redact", "涂黑")):
            ttk.Radiobutton(bot, text=lab, value=val, variable=tool_var).pack(
                side="left", padx=(6, 0))

        ttk.Label(bot, text="内容").pack(side="left", padx=(14, 0))
        ttk.Entry(bot, textvariable=text_var, width=22).pack(side="left", padx=(4, 0))
        ttk.Label(bot, text="颜色").pack(side="left", padx=(10, 0))
        ttk.Combobox(bot, textvariable=color_var, width=6, state="readonly",
                     values=["黑色", "红色", "蓝色", "绿色", "灰色"]).pack(side="left", padx=(4, 0))

        ttk.Button(bot, text="旋转本页 90°",
                   command=lambda: (pv.rotate_current(doc, page_var.get() - 1, 90),
                                    redraw())).pack(side="left", padx=(14, 0))
        ttk.Button(bot, text="删除本页",
                   command=lambda: _del_page()).pack(side="left", padx=(6, 0))

        def _del_page():
            if doc.total <= 1:
                messagebox.showinfo("不能删", "至少要保留一页。", parent=win)
                return
            if not messagebox.askyesno("确认", "删除第 %d 页？" % page_var.get(), parent=win):
                return
            pv.delete_page(doc, page_var.get() - 1)
            page_var.set(min(page_var.get(), doc.total))
            redraw()

        def _save():
            dst = filedialog.asksaveasfilename(
                title="保存编辑后的 PDF", defaultextension=".pdf",
                initialfile=Path(path).stem + "_已编辑.pdf",
                filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(Path(path).parent, APP_DIR))
            if not dst:
                return
            try:
                doc.save_as(dst)
                messagebox.showinfo("已保存", "已保存到：\n%s" % dst, parent=win)
                log("")
                log("📝 PDF 编辑器已保存：" + str(dst))
                status.configure(text="PDF 已保存", foreground="#2a7d2a")
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)

        def _close():
            try:
                doc.close()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _close)
        redraw()

    # ---------- PDF 编辑 ----------
    def open_pdf_editor():
        """PDF 工具箱：合并 / 拆分 / 提取页 / 删页 / 旋转 / 压缩。"""
        pdf_editor = _lazy_import("pdf_editor")
        if pdf_editor is None:
            messagebox.showerror("模块缺失", "找不到 pdf_editor.py，程序包可能不完整。")
            return
        win = tk.Toplevel(root)
        win.title("PDF 工具箱")
        win.geometry("660x560")
        win.minsize(560, 420)
        win.transient(root)

        nb2 = ttk.Notebook(win)
        nb2.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        # 关闭按钮固定在底部
        bar = ttk.Frame(win, padding=(14, 8, 14, 12))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, text="所有操作都生成新文件，不会改动原文件",
                  foreground="#888").pack(side="left")
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

        def _out_dir():
            d = resolve_output_dir(load_config()) / "edit"
            d.mkdir(parents=True, exist_ok=True)
            return d

        def _pick_one(title):
            f = filedialog.askopenfilename(
                title=title, filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(state.get("last_src_dir"),
                                         Path.home() / "Documents", APP_DIR))
            return f or ""

        def _pick_many(title):
            fs = filedialog.askopenfilenames(
                title=title, filetypes=[("PDF 文件", "*.pdf")],
                initialdir=_existing_dir(state.get("last_src_dir"),
                                         Path.home() / "Documents", APP_DIR))
            return [str(x) for x in (fs or [])]

        def _run(label: str, fn):
            """统一跑一个编辑操作，结果显示在日志。"""
            try:
                log("")
                log("【PDF 工具】" + label)
                t0 = time.time()
                r = fn()
                log("   ✅ 完成，用时 %.2fs" % (time.time() - t0))
                log("   " + str(r))
                status.configure(text=label + " 完成", foreground="#2a7d2a")
            except Exception as exc:
                log("   ❌ 失败：" + str(exc)[:160])
                messagebox.showerror("操作失败", str(exc), parent=win)

        # ===== 提取页 =====
        pg1 = ttk.Frame(nb2, padding=14)
        nb2.add(pg1, text="提取 / 删除页")
        ttk.Label(pg1, text="选择 PDF").grid(row=0, column=0, sticky="w")
        f1 = tk.StringVar()
        ttk.Entry(pg1, textvariable=f1, width=46).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(pg1, text="浏览…", command=lambda: f1.set(_pick_one("选择 PDF"))).grid(row=0, column=2)
        ttk.Label(pg1, text="页码").grid(row=1, column=0, sticky="w", pady=(10, 0))
        p1 = tk.StringVar(value="1-3")
        ttk.Entry(pg1, textvariable=p1, width=46).grid(row=1, column=1, sticky="we", padx=6, pady=(10, 0))
        ttk.Label(pg1, text="格式：1-3,5,8-（留空 = 全部页）",
                  foreground="#777").grid(row=2, column=1, sticky="w")
        pg1.columnconfigure(1, weight=1)

        def do_extract():
            src, spec = f1.get().strip(), p1.get().strip()
            if not src:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            info = pdf_editor.pdf_info(src)
            dst = _out_dir() / (Path(src).stem + "_提取页.pdf")
            _run("提取页 %s" % spec, lambda: pdf_editor.extract_pages(src, dst, spec))
            _offer_open(dst)

        def do_delete():
            src, spec = f1.get().strip(), p1.get().strip()
            if not src or not spec:
                messagebox.showwarning("请填写", "需要选文件和页码。", parent=win); return
            dst = _out_dir() / (Path(src).stem + "_删页.pdf")
            _run("删除页 %s" % spec, lambda: pdf_editor.delete_pages(src, dst, spec))
            _offer_open(dst)

        br = ttk.Frame(pg1)
        br.grid(row=3, column=1, sticky="w", pady=(12, 0))
        ttk.Button(br, text="提取这些页", command=do_extract).pack(side="left")
        ttk.Button(br, text="删除这些页", command=do_delete).pack(side="left", padx=8)

        # ===== 旋转 =====
        pg2 = ttk.Frame(nb2, padding=14)
        nb2.add(pg2, text="旋转")
        ttk.Label(pg2, text="选择 PDF").grid(row=0, column=0, sticky="w")
        f2 = tk.StringVar()
        ttk.Entry(pg2, textvariable=f2, width=46).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(pg2, text="浏览…", command=lambda: f2.set(_pick_one("选择 PDF"))).grid(row=0, column=2)
        ttk.Label(pg2, text="页码").grid(row=1, column=0, sticky="w", pady=(10, 0))
        p2 = tk.StringVar(value="")
        ttk.Entry(pg2, textvariable=p2, width=46).grid(row=1, column=1, sticky="we", padx=6, pady=(10, 0))
        ttk.Label(pg2, text="留空 = 全部页", foreground="#777").grid(row=2, column=1, sticky="w")
        ttk.Label(pg2, text="角度").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ang = tk.StringVar(value="90")
        ttk.Combobox(pg2, textvariable=ang, values=["90", "180", "270"],
                     state="readonly", width=10).grid(row=3, column=1, sticky="w", padx=6, pady=(10, 0))
        pg2.columnconfigure(1, weight=1)

        def do_rotate():
            src = f2.get().strip()
            if not src:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            dst = _out_dir() / (Path(src).stem + "_旋转.pdf")
            _run("旋转 %s 度" % ang.get(),
                 lambda: pdf_editor.rotate_pages(src, dst, p2.get().strip(), int(ang.get())))
            _offer_open(dst)

        ttk.Button(pg2, text="旋转", command=do_rotate).grid(row=4, column=1, sticky="w", pady=(12, 0))

        # ===== 合并 =====
        pg3 = ttk.Frame(nb2, padding=14)
        nb2.add(pg3, text="合并")
        ttk.Label(pg3, text="按顺序选择多个 PDF，合并成一个",
                  foreground="#777").pack(anchor="w")
        lst = tk.Listbox(pg3, height=9)
        lst.pack(fill="both", expand=True, pady=(8, 6))
        mg_files: list = []

        def add_files():
            for x in _pick_many("选择要合并的 PDF（按 Ctrl 多选）"):
                mg_files.append(x); lst.insert("end", Path(x).name)
        def del_file():
            for i in reversed(lst.curselection()):
                lst.delete(i); mg_files.pop(i)
        def up_file():
            sel = lst.curselection()
            if not sel or sel[0] == 0: return
            i = sel[0]
            mg_files[i-1], mg_files[i] = mg_files[i], mg_files[i-1]
            v = lst.get(i); lst.delete(i); lst.insert(i-1, v); lst.selection_set(i-1)
        def do_merge():
            if len(mg_files) < 2:
                messagebox.showwarning("至少两个", "合并需要至少 2 个 PDF。", parent=win); return
            dst = _out_dir() / "合并结果.pdf"
            _run("合并 %d 个文件" % len(mg_files),
                 lambda: pdf_editor.merge_pdfs(list(mg_files), dst))
            _offer_open(dst)

        mb2 = ttk.Frame(pg3)
        mb2.pack(fill="x")
        ttk.Button(mb2, text="添加文件…", command=add_files).pack(side="left")
        ttk.Button(mb2, text="移除选中", command=del_file).pack(side="left", padx=6)
        ttk.Button(mb2, text="上移", command=up_file).pack(side="left")
        ttk.Button(mb2, text="开始合并", command=do_merge).pack(side="right")

        # ===== 压缩 =====
        pg4 = ttk.Frame(nb2, padding=14)
        nb2.add(pg4, text="压缩")
        ttk.Label(pg4, text="选择 PDF").grid(row=0, column=0, sticky="w")
        f4 = tk.StringVar()
        ttk.Entry(pg4, textvariable=f4, width=46).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(pg4, text="浏览…", command=lambda: f4.set(_pick_one("选择 PDF"))).grid(row=0, column=2)
        ttk.Label(pg4, text="强度").grid(row=1, column=0, sticky="w", pady=(10, 0))
        lv = tk.StringVar(value="medium")
        lv_labels = [("light", "轻度（几乎无损）"), ("medium", "中等（图片降采样）"),
                     ("strong", "强力（体积最小）")]
        lv_box = ttk.Combobox(pg4, values=[x[1] for x in lv_labels],
                              state="readonly", width=24)
        lv_box.set(lv_labels[1][1])
        lv_box.grid(row=1, column=1, sticky="w", padx=6, pady=(10, 0))
        pg4.columnconfigure(1, weight=1)
        ttk.Label(pg4, text="文字类 PDF 压缩空间有限；扫描件/图片多的效果明显",
                  foreground="#777").grid(row=2, column=1, sticky="w")

        def do_compress():
            src = f4.get().strip()
            if not src:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            key = next((v for v, lab in lv_labels if lab == lv_box.get()), "medium")
            dst = _out_dir() / (Path(src).stem + "_压缩.pdf")
            _run("压缩（%s）" % key, lambda: pdf_editor.compress_pdf(src, dst, key))
            _offer_open(dst)

        ttk.Button(pg4, text="开始压缩", command=do_compress).grid(row=3, column=1, sticky="w", pady=(12, 0))

        # ===== 编辑（页码 / 水印 / 文字 / 元数据）=====
        pg5 = ttk.Frame(nb2, padding=14)
        nb2.add(pg5, text="编辑")
        ttk.Label(pg5, text="选择 PDF").grid(row=0, column=0, sticky="w")
        f5 = tk.StringVar()
        ttk.Entry(pg5, textvariable=f5, width=44).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(pg5, text="浏览…", command=lambda: f5.set(_pick_one("选择 PDF"))).grid(row=0, column=2)
        pg5.columnconfigure(1, weight=1)

        ttk.Label(pg5, text="页码格式").grid(row=1, column=0, sticky="w", pady=(12, 0))
        pn_fmt = tk.StringVar(value="第 {n} 页 / 共 {total} 页")
        ttk.Entry(pg5, textvariable=pn_fmt, width=30).grid(row=1, column=1, sticky="w", padx=6, pady=(12, 0))
        ttk.Label(pg5, text="{n}=当前页  {total}=总页数", foreground="#777").grid(
            row=2, column=1, sticky="w")
        pn_pos = tk.StringVar(value="底部居中")
        pos_map = {"底部居中": "bottom-center", "底部右侧": "bottom-right",
                   "底部左侧": "bottom-left", "顶部居中": "top-center"}
        pos_box = ttk.Combobox(pg5, values=list(pos_map), state="readonly", width=12)
        pos_box.set("底部居中")
        pos_box.grid(row=1, column=2, sticky="w", padx=6, pady=(12, 0))

        ttk.Label(pg5, text="水印文字").grid(row=3, column=0, sticky="w", pady=(12, 0))
        wm_text = tk.StringVar(value="内部资料")
        ttk.Entry(pg5, textvariable=wm_text, width=30).grid(row=3, column=1, sticky="w", padx=6, pady=(12, 0))

        def do_pagenum():
            s = f5.get().strip()
            if not s:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            dst = _out_dir() / (Path(s).stem + "_页码.pdf")
            _run("加页码", lambda: pdf_editor.add_page_numbers(
                s, dst, pn_fmt.get(), pos_map.get(pos_box.get(), "bottom-center")))
            _offer_open(dst)

        def do_watermark():
            s = f5.get().strip()
            if not s:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            t = wm_text.get().strip()
            if not t:
                messagebox.showwarning("请填文字", "水印文字不能为空。", parent=win); return
            dst = _out_dir() / (Path(s).stem + "_水印.pdf")
            _run("加水印", lambda: pdf_editor.add_watermark(s, dst, t))
            _offer_open(dst)

        ebar = ttk.Frame(pg5)
        ebar.grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(ebar, text="给每页加页码", command=do_pagenum).pack(side="left")
        ttk.Button(ebar, text="加斜向水印", command=do_watermark).pack(side="left", padx=8)

        # 插入文字
        ttk.Label(pg5, text="插入文字").grid(row=5, column=0, sticky="w", pady=(16, 0))
        ins_text = tk.StringVar()
        ttk.Entry(pg5, textvariable=ins_text, width=30).grid(row=5, column=1, sticky="w", padx=6, pady=(16, 0))
        ins_pg = tk.StringVar(value="1")
        ttk.Label(pg5, text="第").grid(row=5, column=2, sticky="e")
        ttk.Entry(pg5, textvariable=ins_pg, width=5).grid(row=5, column=2, sticky="e", padx=(30, 0))
        ttk.Label(pg5, text="页").grid(row=5, column=2, sticky="e", padx=(0, 4))

        def do_insert_text():
            s = f5.get().strip()
            t = ins_text.get().strip()
            if not s or not t:
                messagebox.showwarning("请填写", "需要选文件和填写文字。", parent=win); return
            try:
                pgn = int(ins_pg.get())
            except Exception:
                pgn = 1
            dst = _out_dir() / (Path(s).stem + "_加字.pdf")
            _run("插入文字", lambda: pdf_editor.insert_text(
                s, dst, t, page_no=pgn, x=72, y=72, size=12, color="红色"))
            _offer_open(dst)

        ttk.Button(pg5, text="插入到左上角", command=do_insert_text).grid(
            row=6, column=1, sticky="w", pady=(10, 0))

        # 元数据
        ttk.Label(pg5, text="文档标题").grid(row=7, column=0, sticky="w", pady=(16, 0))
        md_title = tk.StringVar()
        ttk.Entry(pg5, textvariable=md_title, width=30).grid(row=7, column=1, sticky="w", padx=6, pady=(16, 0))

        def do_meta():
            s = f5.get().strip()
            if not s:
                messagebox.showwarning("请选择文件", "先选一个 PDF。", parent=win); return
            dst = _out_dir() / (Path(s).stem + "_元数据.pdf")
            _run("改元数据", lambda: pdf_editor.set_metadata(s, dst, title=md_title.get().strip()))
            _offer_open(dst)

        ttk.Button(pg5, text="保存标题", command=do_meta).grid(row=8, column=1, sticky="w", pady=(10, 0))

        def _offer_open(p: Path):
            """编辑完成后问一下要不要打开。"""
            try:
                if not Path(p).exists():
                    return
                af = str(load_config().get("after_translate") or "ask").lower()
                if af == "open":
                    os.startfile(str(p))  # type: ignore[attr-defined]
                elif af == "ask":
                    root.after(150, lambda q=str(p): ask_open_result(q))
            except Exception:
                pass

    # ---------- 术语自动学习（免费引擎也能用）----------
    def open_term_learner():
        """从一份 PDF 里提取候选术语，供你确认后加入术语表。

        不依赖 LLM：用「全大写缩写 / 首字母大写词组 / 型号样式」+
        词频统计挑候选，再和现有术语表比对去掉已知的。
        """
        src = filedialog.askopenfilename(
            title="选择要学习的 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            initialdir=_existing_dir(state.get("last_src_dir"),
                                     Path.home() / "Documents", APP_DIR))
        if not src:
            return

        win = tk.Toplevel(root)
        win.title("术语自动学习")
        win.geometry("760x600")
        win.minsize(620, 420)
        win.transient(root)

        top = ttk.Frame(win, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        info = ttk.Label(top, text="正在分析…", foreground="#666")
        info.pack(side="left")
        ttk.Button(top, text="关闭", command=win.destroy).pack(side="right")

        body = ttk.Frame(win, padding=(12, 0, 12, 6))
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=("term", "count", "cn", "ctx"),
                            show="headings", height=14)
        for c, t, w in (("term", "候选术语", 170), ("count", "次数", 50),
                        ("cn", "中文（可编辑）", 150), ("ctx", "上下文", 320)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tsb = ttk.Scrollbar(body, command=tree.yview)
        tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        # 编辑区
        ef = ttk.LabelFrame(win, text="填中文后「加入术语表」", padding=10)
        ef.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(ef, text="术语").grid(row=0, column=0, sticky="w")
        t_var = tk.StringVar()
        ttk.Entry(ef, textvariable=t_var, width=26).grid(row=0, column=1, padx=6, sticky="we")
        ttk.Label(ef, text="中文").grid(row=0, column=2, sticky="w")
        c_var = tk.StringVar()
        ttk.Entry(ef, textvariable=c_var, width=24).grid(row=0, column=3, padx=6, sticky="we")
        ef.columnconfigure(1, weight=1)
        ef.columnconfigure(3, weight=1)

        added: list[tuple[str, str]] = []

        def on_pick(_e=None):
            sel = tree.selection()
            if sel:
                v = tree.item(sel[0], "values")
                t_var.set(v[0])
                c_var.set(v[2] if len(v) > 2 else "")
        tree.bind("<<TreeviewSelect>>", on_pick)

        def add_one():
            term = t_var.get().strip()
            cn = c_var.get().strip()
            if not term or not cn:
                messagebox.showwarning("请填写", "术语和中文都要填。", parent=win)
                return
            added.append((term, cn))
            # 从列表里移除
            for iid in tree.get_children():
                if tree.item(iid, "values")[0] == term:
                    tree.delete(iid)
                    break
            t_var.set(""); c_var.set("")
            info.configure(text="已准备 %d 条，关闭窗口时写入术语表" % len(added))

        bf = ttk.Frame(ef)
        bf.grid(row=0, column=4, padx=(10, 0))
        ttk.Button(bf, text="加入术语表", command=add_one).pack(side="left")

        def _auto_suggest():
            """对没填中文的候选，用免费引擎翻一下作参考。"""
            sel = tree.selection()
            targets = sel if sel else tree.get_children()[:10]
            if not targets:
                return
            info.configure(text="正在自动翻译 %d 个候选…" % len(targets),
                           foreground="#b8860b")

            def job():
                import learn_check as _lc
                done_n = 0
                for iid in targets:
                    vals = tree.item(iid, "values")
                    term = str(vals[0]) if vals else ""
                    cur = str(vals[2]) if len(vals) > 2 else ""
                    if not term or cur:
                        continue
                    s = _lc.suggest_translation(term)
                    if s:
                        def upd(i=iid, v=s):
                            try:
                                old = tree.item(i, "values")
                                tree.item(i, values=(old[0], old[1], v,
                                                     old[3] if len(old) > 3 else ""))
                            except Exception:
                                pass
                        win.after(0, upd)
                        done_n += 1
                def fin():
                    info.configure(text="已为 %d 个候选填入翻译建议，请核对后加入"
                                        % done_n, foreground="#2a7d2a")
                win.after(0, fin)

            threading.Thread(target=job, daemon=True).start()

        bf2 = ttk.Frame(ef)
        bf2.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Button(bf2, text="自动翻译建议（选中项或前 10 个）",
                   command=_auto_suggest).pack(side="left")

        def save_all():
            if not added:
                win.destroy()
                return
            gpath = APP_DIR / "glossary.csv"
            try:
                rows = []
                if gpath.exists():
                    with gpath.open(encoding="utf-8-sig", newline="") as fh:
                        for r in csv.DictReader(fh):
                            s = (r.get("source") or "").strip()
                            t = (r.get("target") or "").strip()
                            dom = (r.get("domain") or "").strip()
                            if s and t:
                                rows.append((s, t, dom))
                exist = {s.lower() for s, _t, _d in rows}
                n_new = 0
                for s, t in added:
                    if s.lower() not in exist:
                        rows.append((s, t, ""))
                        exist.add(s.lower())
                        n_new += 1
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                w.writerow(["source", "target", "tgt_lng", "domain"])
                for s, t, d in rows:
                    w.writerow([s, t, "zh_cn", d])
                gpath.write_text(buf.getvalue(), encoding="utf-8-sig")
                messagebox.showinfo("已保存",
                                    "新增 %d 条术语，术语表共 %d 条。\n下次翻译生效。"
                                    % (n_new, len(rows)), parent=win)
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", save_all)

        def work():
            try:
                import learn_check
                # 现有术语（用于去重）
                existing = set()
                try:
                    with (APP_DIR / "glossary.csv").open(encoding="utf-8-sig", newline="") as fh:
                        for r in csv.DictReader(fh):
                            existing.add((r.get("source") or "").strip())
                except Exception:
                    pass

                # 找一份配套译文，用来推断中文
                tpdf = None
                try:
                    stem = Path(src).stem
                    cfg0 = load_config()
                    od = resolve_output_dir(cfg0)
                    best = None
                    for cand in od.rglob("*.pdf"):
                        if cand.stem.startswith(stem) and "zh" in cand.name.lower():
                            if best is None or cand.stat().st_mtime > best.stat().st_mtime:
                                best = cand
                    tpdf = best
                except Exception:
                    tpdf = None

                cands = learn_check.learn_from_pdf(
                    src, existing, top=80, translated_pdf=tpdf)

                def show():
                    n_cn = 0
                    for c in cands:
                        sug = c.get("suggestion") or ""
                        if sug:
                            n_cn += 1
                        tree.insert("", "end", values=(
                            c["term"], c["count"], sug, c["context"]))
                    msg = ("从 %s 提取到 %d 个候选术语（按频次排序）"
                           % (Path(src).name, len(cands)))
                    if n_cn:
                        msg += "；其中 %d 个已自动填好中文建议" % n_cn
                    if tpdf:
                        msg += "（参考译文：%s）" % Path(tpdf).name[:24]
                    else:
                        msg += "；没找到配套译文，中文需手填"
                    info.configure(text=msg)
                win.after(0, show)
            except Exception as exc:
                def err():
                    info.configure(text="提取失败：" + str(exc)[:80], foreground="#b00020")
                try:
                    win.after(0, err)
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    # ---------- 设置窗口 ----------
    settings_win = {"win": None}

    def open_settings():
        # 已开着就直接聚焦，避免开出多个模态窗口互相抢 grab
        existing = settings_win.get("win")
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
            settings_win["win"] = None

        win = tk.Toplevel(root)
        win.title("设置")
        win.geometry("620x480")
        win.transient(root)
        settings_win["win"] = win
        cur = load_config()

        def close_settings():
            """关闭设置窗口（顺手清掉可能残留的 grab）。"""
            try:
                if win.grab_current() is win:
                    win.grab_release()
            except Exception:
                pass
            # 术语窗口若还开着，一并关掉，避免留下孤儿窗口
            try:
                gw = glossary_win.get("win")
                if gw is not None and gw.winfo_exists():
                    gw.destroy()
                glossary_win["win"] = None
            except Exception:
                pass
            try:
                settings_win["win"] = None
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", close_settings)

        # 注意：设置窗口【故意不调用 grab_set】。
        # grab_set 是「模态锁」，会拦截其它所有窗口的事件 ——
        # 之前它导致「术语表管理」窗口打开后完全点不动（设置窗口抢走了事件）。
        # 设置只是个配置面板，不需要模态；让它和术语窗口、主窗口都能自由切换。
        # （曾经为了修「保存后主窗口卡死」而加过 grab，那个问题的真因是
        #   ctypes 指针截断破坏了窗口消息链，已经另行修掉，不再需要模态锁。）

        # ---------- 布局 ----------
        # 顺序很重要：先把「底部按钮」占好位置，再让 Notebook 填满剩余空间。
        # 反之（先前见过）会出现两个问题：
        #   1) 残留的空 Canvas 先 pack 上去，顶部留一大块空白
        #   2) 按钮最后 pack，窗口变小时会被挤到可视区域外，点不到保存/取消
        win.geometry("700x620")
        win.minsize(560, 420)

        btns = ttk.Frame(win, padding=(14, 10, 14, 12))
        btns.pack(side="bottom", fill="x")

        # ============ 分类标签页 ============
        # 设置项变多了，按用途分成几类，避免一屏塞不下。
        nb_set = ttk.Notebook(win)
        nb_set.pack(fill="both", expand=True, padx=(10, 10), pady=(10, 0))

        def _make_page(title: str):
            """建一个可滚动的设置页。"""
            page = ttk.Frame(nb_set)
            nb_set.add(page, text=title)
            cv = tk.Canvas(page, highlightthickness=0, borderwidth=0)
            sb = ttk.Scrollbar(page, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            cv.pack(side="left", fill="both", expand=True)
            inner = ttk.Frame(cv, padding=14)
            inner.columnconfigure(1, weight=1)
            wid = cv.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
            cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))

            def _wheel(e, _cv=cv):
                try:
                    _cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
                except Exception:
                    pass
            cv.bind("<Enter>", lambda e, _cv=cv: _cv.bind_all("<MouseWheel>", _wheel))
            cv.bind("<Leave>", lambda e: cv.unbind_all("<MouseWheel>"))
            return inner

        vars_: dict[str, "tk.StringVar"] = {}

        # ---------- 页1：翻译引擎 ----------
        frm = _make_page("翻译引擎")
        r = 0

        def add_entry(label: str, key: str, hint: str = ""):
            nonlocal r
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=(8, 0))
            v = tk.StringVar(value=str(cur.get(key, "")))
            vars_[key] = v
            ttk.Entry(frm, textvariable=v, width=46).grid(row=r, column=1, sticky="we", pady=(8, 0))
            if hint:
                r += 1
                ttk.Label(frm, text=hint, foreground="#777").grid(row=r, column=1, sticky="w")
            r += 1

        # ---- 翻译引擎：下拉选择 + 允许自己输入 ----
        ttk.Label(frm, text="翻译引擎").grid(row=r, column=0, sticky="w", pady=(8, 0))
        eng_labels = [c[0] for c in ENGINE_CHOICES]
        cur_engine = str(cur.get("engine") or "bing").lower()
        cur_label = ENGINE_LABEL.get(cur_engine, eng_labels[0])
        eng_var = tk.StringVar(value=cur_label)
        eng_box = ttk.Combobox(frm, textvariable=eng_var, values=eng_labels,
                               state="normal", width=43)
        eng_box.grid(row=r, column=1, sticky="we", pady=(8, 0))
        r += 1
        ttk.Label(frm, text="从列表选择；也可直接输入引擎名（如 bing / deepseek / ollama）",
                  foreground="#777").grid(row=r, column=1, sticky="w")
        r += 1

        # 自定义输入框：当用户输入的不是列表项时使用
        ttk.Label(frm, text="自定义引擎").grid(row=r, column=0, sticky="w", pady=(6, 0))
        custom_eng = tk.StringVar(value="" if cur_engine in ENGINE_LABEL else cur_engine)
        ttk.Entry(frm, textvariable=custom_eng, width=46).grid(row=r, column=1, sticky="we", pady=(6, 0))
        r += 1
        ttk.Label(frm, text="留空则用上面的下拉选择", foreground="#777").grid(row=r, column=1, sticky="w")
        r += 1

        # 测试连接（省得填完 Key 才发现不通）
        ttk.Label(frm, text="").grid(row=r, column=0)
        test_btn = ttk.Button(frm, text="测试连接")
        test_btn.grid(row=r, column=1, sticky="w")
        r += 1
        test_msg = ttk.Label(frm, text="", foreground="#777", wraplength=420, justify="left")
        test_msg.grid(row=r, column=1, sticky="w")
        r += 1

        add_entry("源语言", "lang_in", "如 en（英文）")
        add_entry("目标语言", "lang_out", "如 zh-CN（简体中文）")
        add_entry("API Key", "api_key", "免费引擎（bing / google）可留空")
        add_entry("API 地址", "base_url", "可留空")
        add_entry("模型名", "model", "可留空")

        # ---- 并发数（滑动条 + 数字） ----
        ttk.Label(frm, text="并发数").grid(row=r, column=0, sticky="w", pady=(12, 0))
        try:
            _cur_th = int(cur.get("threads") or 10)
        except Exception:
            _cur_th = 10
        _cur_th = max(1, min(30, _cur_th))
        th_var = tk.IntVar(value=_cur_th)
        tf = ttk.Frame(frm)
        tf.grid(row=r, column=1, sticky="we", pady=(12, 0))
        tf.columnconfigure(0, weight=1)
        th_scale = ttk.Scale(tf, from_=1, to=30, orient="horizontal",
                             variable=th_var, command=lambda v: th_lbl.configure(text=str(int(float(v)))))
        th_scale.grid(row=0, column=0, sticky="we")
        th_lbl = ttk.Label(tf, text=str(_cur_th), width=4, anchor="e")
        th_lbl.grid(row=0, column=1, padx=(6, 0))
        r += 1
        def _do_test():
            """测试当前填写的引擎是否能连通（后台线程，避免卡界面）。"""
            ce = custom_eng.get().strip().lower()
            if ce:
                eng = ce
            else:
                sel = eng_var.get().strip()
                eng = next((k for lab, k, _n, _g in ENGINE_CHOICES if lab == sel), "bing")
            key = vars_.get("api_key")
            burl = vars_.get("base_url")
            mdl = vars_.get("model")
            test_msg.configure(text="正在测试…", foreground="#b8860b")
            test_btn.configure(state="disabled")

            def worker_test():
                ok, msg = test_engine_connection(
                    eng,
                    key.get().strip() if key else "",
                    burl.get().strip() if burl else "",
                    mdl.get().strip() if mdl else "",
                )
                def show():
                    test_msg.configure(text=("✅ " if ok else "❌ ") + msg,
                                       foreground=("#2a7d2a" if ok else "#b00020"))
                    test_btn.configure(state="normal")
                try:
                    win.after(0, show)
                except Exception:
                    pass

            threading.Thread(target=worker_test, daemon=True).start()

        test_btn.configure(command=_do_test)

        ttk.Label(frm, text="翻译以网络往返为主，并发越高越快；实测 20 比 10 再快约 1.6 倍。"
                            "太高可能被接口限流（建议 10–20）",
                  foreground="#777", wraplength=420, justify="left").grid(
            row=r, column=1, sticky="w")
        r += 1

        # ---------- 页2：术语表 ----------
        frm = _make_page("术语表")
        r = 0

        ttk.Label(frm, text="内置术语表").grid(row=r, column=0, sticky="w")
        gloss_var = tk.BooleanVar(value=bool(cur.get("glossary_enabled", True)))
        ttk.Checkbutton(frm, text="启用（1046 条电子工程术语，免费引擎也生效）",
                        variable=gloss_var).grid(row=r, column=1, sticky="w")
        r += 1

        # 领域分组
        ttk.Label(frm, text="启用领域").grid(row=r, column=0, sticky="w", pady=(10, 0))
        _domains = [("all", "全部领域"), ("power", "电源与转换器"),
                    ("analog", "模拟与信号链"), ("digital", "数字与接口"),
                    ("device", "半导体器件"), ("package", "封装与可靠性"),
                    ("rf", "射频与无线")]
        domain_var = tk.StringVar(value=str(cur.get("glossary_domain") or "all"))
        dom_labels = [d[1] for d in _domains]
        dom_map = {d[1]: d[0] for d in _domains}
        dom_box = ttk.Combobox(frm, values=dom_labels, state="readonly", width=30)
        dom_box.set(next((lab for val, lab in _domains
                          if val == domain_var.get()), dom_labels[0]))
        dom_box.grid(row=r, column=1, sticky="w", pady=(10, 0))
        r += 1
        ttk.Label(frm, text="只启用某个领域的术语，减少误替换",
                  foreground="#777").grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frm, text="自定义术语表").grid(row=r, column=0, sticky="w", pady=(10, 0))
        gf = ttk.Frame(frm)
        gf.grid(row=r, column=1, sticky="we", pady=(10, 0))
        gf.columnconfigure(0, weight=1)
        gfile_var = tk.StringVar(value=str(cur.get("glossary_file") or ""))
        ttk.Entry(gf, textvariable=gfile_var).grid(row=0, column=0, sticky="we")
        ttk.Button(gf, text="浏览…", command=lambda: _browse_glossary(gfile_var)).grid(
            row=0, column=1, padx=(6, 0))
        r += 1
        ttk.Label(frm, text="可选。可用逗号分隔多个 CSV", foreground="#777").grid(
            row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frm, text="").grid(row=r, column=0)
        ttk.Button(frm, text="管理术语表（增删改·搜索）",
                   command=open_glossary_editor).grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frm, text="").grid(row=r, column=0)
        ttk.Button(frm, text="从 PDF 学习新术语…",
                   command=open_term_learner).grid(row=r, column=1, sticky="w")
        r += 1

        # 自动学习
        ttk.Label(frm, text="自动学习").grid(row=r, column=0, sticky="w", pady=(12, 0))
        learn_var = tk.BooleanVar(value=bool(cur.get("auto_learn_glossary", True)))
        ttk.Checkbutton(frm, text="翻译后提取高频专业词，供你确认后加入术语表",
                        variable=learn_var).grid(row=r, column=1, sticky="w", pady=(12, 0))
        r += 1
        ttk.Label(frm, text="免费引擎同样有效（不依赖 LLM，用词频+术语表比对实现）",
                  foreground="#777", wraplength=430, justify="left").grid(
            row=r, column=1, sticky="w")
        r += 1

        # ---------- 页3：输出 ----------
        frm = _make_page("输出")
        r = 0

        ttk.Label(frm, text="输出版本").grid(row=r, column=0, sticky="w")
        mode_var = tk.StringVar(value=str(cur.get("output_mode") or "mono").lower())
        mode_labels = [("mono", "中文版（仅译文）"), ("dual", "双语对照版"), ("both", "两者都要")]
        mode_box = ttk.Combobox(frm, values=[m[1] for m in mode_labels],
                                state="readonly", width=30)
        mode_box.set(next((lab for val, lab in mode_labels
                           if val == mode_var.get()), mode_labels[0][1]))
        mode_box.grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frm, text="保存位置").grid(row=r, column=0, sticky="w", pady=(10, 0))
        dir_var = tk.StringVar(value=str(cur.get("output_dir") or ""))
        df = ttk.Frame(frm)
        df.grid(row=r, column=1, sticky="we", pady=(10, 0))
        df.columnconfigure(0, weight=1)
        ttk.Entry(df, textvariable=dir_var).grid(row=0, column=0, sticky="we")
        ttk.Button(df, text="浏览…", command=lambda: _browse_dir(dir_var)).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(df, text="打开", command=lambda: _open_dir(dir_var.get())).grid(row=0, column=2, padx=(6, 0))
        r += 1
        ttk.Label(frm, text="留空 = 程序目录下的 output 文件夹", foreground="#777").grid(
            row=r, column=1, sticky="w")
        r += 1

        # 翻译完成后如何打开
        # ---- 预设 ----
        ttk.Label(frm, text="预设").grid(row=r, column=0, sticky="w", pady=(12, 0))
        pfrm = ttk.Frame(frm)
        pfrm.grid(row=r, column=1, sticky="we", pady=(12, 0))
        pfrm.columnconfigure(0, weight=1)
        preset_var = tk.StringVar(value=str(cur.get("preset_active") or ""))
        preset_names = list((cur.get("presets") or {}).keys())
        preset_box = ttk.Combobox(pfrm, textvariable=preset_var, state="readonly",
                                  values=["（不使用）"] + preset_names, width=22)
        preset_box.grid(row=0, column=0, sticky="we")
        r += 1

        def _apply_preset(_e=None):
            name = preset_var.get()
            if name == "（不使用）":
                c2 = load_config(); c2["preset_active"] = ""; save_config(c2)
                return
            data = (load_config().get("presets") or {}).get(name)
            if not data:
                return
            c2 = load_config()
            c2.update(data)
            c2["preset_active"] = name
            save_config(c2)
            messagebox.showinfo("已套用预设",
                                "已套用「%s」，关闭设置后生效。\n（部分选项需重新打开设置查看）" % name,
                                parent=win)
        preset_box.bind("<<ComboboxSelected>>", _apply_preset)

        def _save_preset():
            d = tk.Toplevel(win); d.title("保存为预设"); d.geometry("360x150")
            d.transient(win)
            f2 = ttk.Frame(d, padding=16); f2.pack(fill="both", expand=True)
            ttk.Label(f2, text="预设名称（如「快速预览」「精修」）").pack(anchor="w")
            nv = tk.StringVar()
            ttk.Entry(f2, textvariable=nv, width=32).pack(fill="x", pady=(8, 0))
            def do():
                nm = nv.get().strip()
                if not nm:
                    return
                c3 = load_config()
                # 存一份当前设置（去掉预设字段本身）
                snap = {k: v for k, v in c3.items()
                        if k not in ("presets", "preset_active")}
                c3.setdefault("presets", {})[nm] = snap
                c3["preset_active"] = nm
                save_config(c3)
                messagebox.showinfo("已保存", "预设「%s」已保存。" % nm, parent=d)
                d.destroy()
            row = ttk.Frame(f2); row.pack(fill="x", pady=(12, 0))
            ttk.Button(row, text="保存", command=do).pack(side="right")
            ttk.Button(row, text="取消", command=d.destroy).pack(side="right", padx=6)

        pbtns = ttk.Frame(frm)
        pbtns.grid(row=r, column=1, sticky="w")
        ttk.Button(pbtns, text="另存为预设…", command=_save_preset).pack(side="left")
        ttk.Label(pbtns, text="把当前整套设置存下来，以后一键切换",
                  foreground="#777").pack(side="left", padx=(8, 0))
        r += 1

        ttk.Label(frm, text="完成后").grid(row=r, column=0, sticky="w", pady=(12, 0))
        _open_modes = [("ask", "每次询问我"), ("open", "自动打开结果"),
                       ("none", "不打开（我自己在历史记录里开）")]
        open_var = tk.StringVar(value=str(cur.get("after_translate") or "ask"))
        open_box = ttk.Combobox(frm, values=[m[1] for m in _open_modes],
                                state="readonly", width=30)
        open_box.set(next((lab for val, lab in _open_modes
                           if val == open_var.get()), _open_modes[0][1]))
        open_box.grid(row=r, column=1, sticky="w", pady=(12, 0))
        r += 1
        ttk.Label(frm, text="选「每次询问我」，翻译完会弹窗让你决定是否打开、用什么打开",
                  foreground="#777", wraplength=430, justify="left").grid(
            row=r, column=1, sticky="w")
        r += 1

        # ---------- 页4：高级 ----------
        frm = _make_page("高级")
        r = 0

        ttk.Label(frm, text="性能").grid(row=r, column=0, sticky="w")
        perf = ttk.Frame(frm)
        perf.grid(row=r, column=1, sticky="w")
        cache_var = tk.BooleanVar(value=bool(cur.get("ignore_cache")))
        ttk.Checkbutton(perf, text="忽略译文缓存（强制重新翻译）", variable=cache_var).pack(anchor="w")
        r += 1

        ttk.Label(frm, text="失败重试").grid(row=r, column=0, sticky="w", pady=(10, 0))
        retry_var = tk.BooleanVar(value=bool(cur.get("auto_degrade", True)))
        ttk.Checkbutton(frm, text="限流时自动降低并发重试",
                        variable=retry_var).grid(row=r, column=1, sticky="w", pady=(10, 0))
        r += 1

        ttk.Label(frm, text="质量检查").grid(row=r, column=0, sticky="w", pady=(10, 0))
        qc_var = tk.BooleanVar(value=bool(cur.get("quality_check", True)))
        ttk.Checkbutton(frm, text="翻译后检测可疑译文（英文残留、数字错位等）",
                        variable=qc_var).grid(row=r, column=1, sticky="w", pady=(10, 0))
        r += 1

        def save():
            """保存设置。变量名与上方控件一一对应（照原实现复原）。"""
            # 引擎：自定义输入优先
            ce = custom_eng.get().strip().lower()
            if ce:
                cur["engine"] = ce
            else:
                cur["engine"] = next(
                    (k for lab, k, _n, _g in ENGINE_CHOICES if lab == eng_var.get()),
                    cur.get("engine") or "bing")
            # 其余文本项（源语言/目标语言/API Key/API 地址/模型名）
            for k, v in vars_.items():
                if k == "engine":
                    continue
                cur[k] = v.get().strip() if hasattr(v, "get") else v
            cur["threads"] = max(1, min(30, int(th_var.get() or 10)))
            cur["glossary_enabled"] = bool(gloss_var.get())
            cur["glossary_domain"] = dom_map.get(dom_box.get(), "all")
            cur["glossary_file"] = gfile_var.get().strip()
            cur["auto_learn_glossary"] = bool(learn_var.get())
            cur["output_mode"] = mode_var.get()
            cur["output_dir"] = dir_var.get().strip()
            cur["after_translate"] = next(
                (v for v, lab in _open_modes if lab == open_box.get()), "ask")
            cur["ignore_cache"] = bool(cache_var.get())
            cur["auto_degrade"] = bool(retry_var.get())
            cur["quality_check"] = bool(qc_var.get())
            save_config(cur)
            refresh_info()
            messagebox.showinfo("已保存", "设置已保存，下次翻译生效。", parent=win)
            close_settings()

        # 填充底部按钮栏（栏本身在上面已 pack 好）
        ttk.Label(btns, text="设置按类别分页，切换上方标签查看",
                  foreground="#888").pack(side="left")
        ttk.Button(btns, text="保存", command=save).pack(side="right")
        ttk.Button(btns, text="取消", command=close_settings).pack(side="right", padx=8)

    def _pick_dir(var):
        """选择目录（确认窗口里的「浏览…」）。

        注：缓存重定向已改到 sitecustomize（不再改 HOME），
        但这里仍显式传 initialdir，双保险避免「位置不可用」。
        """
        d = filedialog.askdirectory(
            title="选择保存翻译结果的文件夹",
            initialdir=_existing_dir(var.get(), state.get("last_dir"), APP_DIR),
        )
        if d:
            var.set(d)

    def _browse_dir(var):
        _pick_dir(var)

    def _browse_exe(var):
        f = filedialog.askopenfilename(
            title="选择要用来打开的程序（.exe）",
            filetypes=[("程序", "*.exe"), ("所有文件", "*.*")],
            initialdir=_existing_dir(var.get(), r"D:\\WPS", APP_DIR),
        )
        if f:
            var.set(f)

    def _browse_glossary(var):
        f = filedialog.askopenfilename(
            title="选择术语表 CSV（source,target 两列）",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialdir=_existing_dir(var.get(), APP_DIR),
        )
        if f:
            var.set(f)

    def _open_dir(path: str):
        p = Path(path.strip()) if path.strip() else DEFAULT_OUTPUT_DIR
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    btn_set.configure(command=open_settings)

    # ---------- 翻译完成后询问是否打开 ----------
    def ask_open_result(path: str):
        """弹窗询问：是否打开刚翻好的文件，用哪种方式。"""
        p = Path(path)
        if not p.exists():
            return
        dlg = tk.Toplevel(root)
        dlg.title("翻译完成")
        dlg.geometry("440x230")
        dlg.transient(root)
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="✅ 翻译完成", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(f, text=p.name, foreground="#3a6ea5").pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text="要现在打开吗？", foreground="#666").pack(anchor="w", pady=(10, 0))

        def close_dlg():
            try:
                if dlg.grab_current() is dlg:
                    dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()

        def do_open(mode: str):
            try:
                c = load_config()
                c["open_with"] = mode
                save_config(c)
                if mode == "folder":
                    os.startfile(str(p.parent))  # type: ignore[attr-defined]
                else:
                    open_file_with(p, c)
                status.configure(text="已打开结果", foreground="#2a7d2a")
            except Exception as exc:
                messagebox.showerror("打开失败", str(exc), parent=dlg)
            close_dlg()

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="系统默认", command=lambda: do_open("auto")).pack(side="left")
        if _find_wps():
            ttk.Button(row, text="WPS", command=lambda: do_open("wps")).pack(side="left", padx=6)
        if _find_edge():
            ttk.Button(row, text="Edge", command=lambda: do_open("edge")).pack(side="left", padx=6)
        if _find_office():
            ttk.Button(row, text="Office", command=lambda: do_open("office")).pack(
                side="left", padx=6)
        ttk.Button(row, text="打开文件夹", command=lambda: do_open("folder")).pack(side="left", padx=6)

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(12, 0))
        ttk.Button(row2, text="不打开", command=close_dlg).pack(side="right")

        dlg.protocol("WM_DELETE_WINDOW", close_dlg)
        try:
            dlg.attributes("-topmost", True)
            dlg.after(300, lambda: dlg.attributes("-topmost", False))
        except Exception:
            pass

    # ---------- 事件循环 ----------
    def pump():
        # 代次守卫：界面重建后旧循环自行退出
        if _my_gen != _BUILD_GEN["n"]:
            return
        try:
            while True:
                item = ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    append_log(item[1])
                elif kind == "file":
                    start_file(item[1])
                elif kind == "prog":
                    v = max(0.0, min(100.0, float(item[1])))
                    prog.configure(value=v)
                    pct.configure(text=("%.0f%%" % v))
                    if item[2]:
                        stage_lbl.configure(text=str(item[2])[:22])
                    # 预计剩余时间（用已用时间 / 已完成比例推算）
                    try:
                        t0 = state.get("t_start") or 0
                        if t0 and 2.0 < v < 99.5:
                            elapsed = time.time() - t0
                            remain = elapsed * (100.0 - v) / v
                            if remain > 90:
                                eta_lbl.configure(text="剩余约 %.0f 分钟" % (remain / 60))
                            else:
                                eta_lbl.configure(text="剩余约 %.0f 秒" % remain)
                        elif v >= 99.5:
                            eta_lbl.configure(text="")
                    except Exception:
                        pass
                elif kind == "stage":
                    # 实时阶段更新 → 切到「步骤耗时」页并开始计时
                    try:
                        _nm = str(item[1])
                        _step_begin(_nm)
                        # 同时写进日志，回看时能看到完整阶段序列
                        log("▶ " + stage_cn(_nm))
                    except Exception:
                        pass
                elif kind == "done":
                    # 兼容不同长度的元组（逐轮加过字段）
                    nxt_cb = None
                    timing = {}
                    _, ok, out_file, err, out_dir = item[:5]
                    if len(item) >= 6:
                        nxt_cb = item[5]
                    if len(item) >= 7:
                        timing = item[6] or {}
                    state["running"] = False
                    btn_pick.configure(state="normal")
                    # 刷新「步骤耗时」页（停掉实时计时器）
                    try:
                        _step_end()
                        if timing:
                            _show_steps(timing.get("stages") or [],
                                        float(timing.get("total") or 0.0))
                    except Exception:
                        pass
                    if ok and out_file:
                        state["last_dir"] = out_dir
                        prog.configure(value=100)
                        pct.configure(text="100%")
                        stage_lbl.configure(text="完成")
                        status.configure(text="完成", foreground="#2a7d2a")
                        # 切到历史记录并选中最新一条
                        try:
                            refresh_history()
                            if hist.get_children():
                                first = hist.get_children()[0]
                                hist.selection_set(first)
                                hist.focus(first)
                                hist.see(first)
                            nb.select(histf)
                        except Exception:
                            pass
                        # 按设置决定是否打开：ask=询问 / open=自动 / none=不打开
                        _cfg = load_config()
                        _after = str(_cfg.get("after_translate") or "ask").lower()
                        if _after == "open":
                            try:
                                open_file_with(Path(out_file), _cfg)
                                status.configure(text="已打开结果", foreground="#2a7d2a")
                            except Exception:
                                pass
                        elif _after == "ask":
                            root.after(200, lambda p=str(out_file): ask_open_result(p))
                    # 批量：继续下一个（延迟一点，让界面先刷新）
                    if nxt_cb is not None:
                        root.after(400, nxt_cb)
                    else:
                        stage_lbl.configure(text="失败")
                        status.configure(text="失败", foreground="#b00020")
        except queue.Empty:
            pass
        # 只在同代界面里继续排队
        if _my_gen == _BUILD_GEN["n"]:
            root.after(120, pump)

    # ---------- 统一接上「后定义」的工具按钮 ----------
    # open_pdf_editor / open_inspect_tools / open_term_learner 定义在上面按钮
    # 创建之后，所以在这里（所有定义都就绪后）再绑定命令。
    try:
        btn_edit.configure(command=open_pdf_editor)
    except Exception:
        pass
    try:
        btn_inspect.configure(command=open_inspect_tools)
    except Exception:
        pass
    try:
        btn_view.configure(command=open_pdf_viewer)
    except Exception:
        pass

    refresh_info()
    refresh_history()
    pump()

    if not dropped_ok:
        append_log("提示：本机不支持拖放时，请用“选择 PDF”按钮。")

    # 启动时提示术语表状态（读实际条数，不写死）
    try:
        _c = load_config()
        if _c.get("glossary_enabled", True):
            _g = APP_DIR / "glossary.csv"
            _n = 0
            if _g.exists():
                with _g.open(encoding="utf-8-sig") as _fh:
                    _n = max(0, sum(1 for _ in _fh) - 1)
            append_log("专业术语表已启用：内置电子工程术语 %d 条（免费引擎同样生效）" % _n)
    except Exception:
        pass

    # 支持命令行传入 PDF：既可拖到 bat 上打开，也方便自动化验证
    for arg in sys.argv[1:]:
        try:
            ap = Path(arg.strip('"'))
            if ap.is_file() and ap.suffix.lower() == ".pdf":
                root.after(300, lambda p=str(ap): start_file(p))
                break
        except Exception:
            pass

    root.mainloop()


def main() -> int:
    if not WORKER.exists():
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "文件缺失",
                "找不到 worker.py：\n" + str(WORKER) +
                "\n\n请重新解压完整程序包（不要只复制部分文件）。",
            )
            r.destroy()
        except Exception:
            pass
        return 1
    if not PYTHON.exists():
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "文件缺失",
                "找不到自带运行环境：\n" + str(PYTHON) +
                "\n\n请重新解压完整程序包。",
            )
            r.destroy()
        except Exception:
            pass
        return 1
    build_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())

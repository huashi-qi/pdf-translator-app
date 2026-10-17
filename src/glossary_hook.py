# -*- coding: utf-8 -*-
"""
术语注入模块 —— 让「免费引擎」（Bing/Google）也能用专业术语表。

背景
----
pdf2zh_next / babeldoc 的术语表（glossary）**只在 LLM 提示词路径生效**：
非 LLM 引擎（bing / google / deepl）会直接报
"Bing does not support glossary"。

做法
----
绕开官方限制，用「占位符替换法」：
  1. 翻译前，把命中的术语换成 `@@T1@@` 这类占位符
  2. 交给任意引擎翻译（实测 Bing/Google 都会原样保留占位符）
  3. 翻译后，把占位符换回术语表里的中文

实测效果：
    原文: The SGM6130 is a step-down converter with low quiescent current.
    译文: SGM6130 是一款 降压转换器，具有低 静态电流。

生效范围
--------
翻译实际跑在 multiprocessing 子进程里，主进程打的补丁不会传播过去。
所以本模块必须由 `sitecustomize.py` 在每个解释器启动时自动加载。
"""
from __future__ import annotations

import csv
import os
import re
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------- 配置
APP_DIR = Path(__file__).resolve().parent
GLOSSARY_FILE = APP_DIR / "glossary.csv"
CONFIG_FILE = APP_DIR / "config.json"

# 占位符格式：不与 babeldoc 自用标记冲突（它用 {v1} [[...]] %s）
TOKEN_FMT = "@@G{0}@@"
TOKEN_RE = re.compile(r"@@G(\d+)@@")

_lock = threading.Lock()
_entries: list[tuple[str, str]] = []      # [(source_lower, target)]
_pattern: re.Pattern | None = None
_loaded = False
_enabled = True
_STATE: dict = {"patched": False}


def _read_config() -> dict:
    """读 config.json（只取需要的项，避免依赖 json 之外的库）。"""
    import json
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def load_glossary(force: bool = False) -> int:
    """加载术语表。返回条目数。"""
    global _entries, _pattern, _loaded, _enabled

    with _lock:
        if _loaded and not force:
            return len(_entries)

        cfg = _read_config()
        _enabled = bool(cfg.get("glossary_enabled", True))
        custom = str(cfg.get("glossary_file") or "").strip()
        # 领域过滤：all 表示不过滤；CSV 第 4 列（domain）用来分类
        domain = str(cfg.get("glossary_domain") or "all").strip().lower()

        files = []
        # 用户可指定多个（逗号或分号分隔）
        for one in re.split(r"[,;]", custom) if custom else []:
            one = one.strip().strip('"')
            if not one:
                continue
            p = Path(one)
            if not p.is_absolute():
                p = APP_DIR / p
            if p.exists():
                files.append(p)
        if GLOSSARY_FILE.exists():
            files.append(GLOSSARY_FILE)

        entries: list[tuple[str, str]] = []
        seen = set()
        for f in files:
            try:
                with f.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        src = (row.get("source") or "").strip()
                        dst = (row.get("target") or "").strip()
                        if not src or not dst:
                            continue
                        # 领域过滤（内置表用第 4 列 domain；没有该列则视为通用）
                        if domain and domain != "all":
                            row_dom = (row.get("domain") or "").strip().lower()
                            if row_dom and row_dom != domain:
                                continue
                        key = src.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        entries.append((key, dst))
            except Exception:
                continue

        _entries = entries
        # 长词优先匹配（避免 "Buck Converter" 被 "Converter" 抢先）
        #
        # 词边界很重要（踩过坑）：
        #   加边界前，短术语会在别的词内部命中 ——
        #   "PASS" 被 "SS"(软启动) 拆成 "PA软启动"，
        #   "LENGTH" 被 "EN"(使能) 拆成 "L使能GTH"，实测 9/10 误匹配。
        #   加 \b 后，只有当术语作为独立词出现时才替换。
        # 注意：像 "Buck-Boost" 这种带连字符的，\b 也能正确工作
        # （连字符是非单词字符，本身构成边界）。
        if entries:
            alts = sorted((re.escape(s) for s, _ in entries), key=len, reverse=True)
            pat = r"(?<![A-Za-z0-9_])(?:" + "|".join(alts) + r")(?![A-Za-z0-9_])"
            _pattern = re.compile(pat, re.IGNORECASE)
        else:
            _pattern = None
        _loaded = True
        return len(_entries)


def _lookup() -> dict[str, str]:
    return {s: d for s, d in _entries}


def apply_glossary(text: str) -> tuple[str, dict[str, str]]:
    """把命中的术语替换成占位符。返回 (替换后文本, {占位符: 中文})。"""
    if not text or not _enabled:
        return text, {}
    if not _loaded:
        load_glossary()
    if not _pattern or not _entries:
        return text, {}

    lut = _lookup()
    mapping: dict[str, str] = {}
    counter = [0]

    def repl(m: re.Match) -> str:
        key = m.group(0).lower()
        dst = lut.get(key)
        if dst is None:
            return m.group(0)
        counter[0] += 1
        token = TOKEN_FMT.format(counter[0])
        mapping[token] = dst
        # 命中统计（用于「术语表到底有没有用」）
        try:
            th = _STATE.setdefault("term_hits", {})
            th[dst] = int(th.get(dst, 0)) + 1
        except Exception:
            pass
        return token

    try:
        out = _pattern.sub(repl, text)
    except Exception:
        return text, {}
    return out, mapping


def restore_glossary(text: str, mapping: dict[str, str]) -> str:
    """把占位符换回中文术语。"""
    if not text or not mapping:
        return text
    for token, dst in mapping.items():
        if token in text:
            text = text.replace(token, dst)
    # 兜底：清掉任何残留占位符（引擎偶尔会改动大小写/加空格）
    text = TOKEN_RE.sub(lambda m: mapping.get("@@G" + m.group(1) + "@@", ""), text)
    return text


def install() -> bool:
    """给 BaseTranslator.translate 打补丁。

    性能要点（重要）：
        本函数由 sitecustomize 在【每个解释器启动时】调用，
        所以绝不能在这里 import pdf2zh_next —— 那会连带加载
        torch/onnxruntime/cv2 等重依赖，实测让启动多花 4~5 秒
        （表现为双击后几秒无反应，像闪退）。

        做法：用一个 import hook，等 pdf2zh_next.translator.base_translator
        真正被导入时（也就是真的开始翻译时）才打补丁。
    """
    if _STATE["patched"]:
        return True
    try:
        _install_import_hook()
        _STATE["patched"] = True
        return True
    except Exception:
        return False


def _patch_base_translator(BaseTranslator) -> None:
    """真正打补丁（此时 pdf2zh_next 已加载）。

    顺带统计「翻译段数 / 缓存命中段数」：
    babeldoc 自己的计数只在 __del__ 里用 logger.info 打印，
    子进程退出太快时经常来不及输出，所以这里直接数。
    """
    if getattr(BaseTranslator.translate, "_glossary_patched", False):
        return

    _orig_translate = BaseTranslator.translate

    def translate(self, text, ignore_cache=False, rate_limit_params=None):
        try:
            # 先探测是否命中缓存（命中时原实现会直接返回缓存值）
            try:
                hit = False
                if not (getattr(self, "ignore_cache", False) or ignore_cache):
                    hit = self.cache.get(text) is not None
            except Exception:
                hit = False
            _STATE["calls"] = int(_STATE.get("calls", 0)) + 1
            if hit:
                _STATE["hits"] = int(_STATE.get("hits", 0)) + 1

            marked, mapping = apply_glossary(text)
            if mapping:
                out = _orig_translate(
                    self, marked,
                    ignore_cache=ignore_cache,
                    rate_limit_params=rate_limit_params,
                )
                return restore_glossary(str(out), mapping)
            return _orig_translate(
                self, text,
                ignore_cache=ignore_cache,
                rate_limit_params=rate_limit_params,
            )
        except Exception:
            # 任何异常都退回原行为，绝不因为术语表让翻译失败
            return _orig_translate(
                self, text,
                ignore_cache=ignore_cache,
                rate_limit_params=rate_limit_params,
            )

    translate._glossary_patched = True
    BaseTranslator.translate = translate


def get_stats() -> dict:
    """给 worker 读取：本次翻译的段数与缓存命中数。"""
    return {"calls": int(_STATE.get("calls", 0)), "hits": int(_STATE.get("hits", 0))}


def get_term_hits() -> dict:
    """术语命中统计：{中文术语: 命中次数}。

    只统计「真的被替换过」的术语 —— 光在术语表里但文档里没出现的不算。
    用来回答两个问题：术语表有没有起作用？哪些词其实用不上？
    """
    return dict(_STATE.get("term_hits") or {})


# 统计落盘：翻译跑在 multiprocessing 子进程里，父进程读不到子进程内存，
# 所以在子进程退出时把计数写文件，父进程读文件即可。
_STATS_FILE = APP_DIR / "cache" / "last_stats.json"


def _dump_stats() -> None:
    """把当前统计写到文件（供父进程读取）。

    除了进程退出时调用，主进程还会定期调用它做「实时刷新」——
    否则术语表命中要等翻译结束才看得到。
    """
    try:
        if not _STATE.get("calls"):
            return
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(get_stats())
        payload["term_hits"] = get_term_hits()
        # 先写临时文件再原子替换，避免父进程读到写了一半的 JSON
        tmp = _STATS_FILE.with_suffix(".tmp")
        tmp.write_text(
            __import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        try:
            tmp.replace(_STATS_FILE)
        except Exception:
            _STATS_FILE.write_text(
                __import__("json").dumps(payload, ensure_ascii=False),
                encoding="utf-8")
    except Exception:
        pass


def flush_stats() -> None:
    """供外部（如定时器）主动落盘一次统计。"""
    _dump_stats()


try:
    import atexit as _atexit
    _atexit.register(_dump_stats)
except Exception:
    pass


def read_stats_file() -> dict:
    """父进程读取子进程留下的统计。"""
    try:
        import json as _json
        return _json.loads(_STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"calls": 0, "hits": 0}


def clear_stats_file() -> None:
    try:
        _STATS_FILE.unlink()
    except Exception:
        pass


def _install_import_hook() -> None:
    """等 base_translator 被导入时再打补丁（避免启动时加载重依赖）。"""
    import importlib.abc
    import importlib.util

    target = "pdf2zh_next.translator.base_translator"

    class _PatchOnImport(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target_=None):
            if name != target:
                return None
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
            spec = importlib.util.find_spec(name)
            if spec is not None and spec.loader is not None:
                _orig = spec.loader.exec_module

                def _exec(module, *a, **k):
                    _orig(module, *a, **k)
                    try:
                        _patch_base_translator(module.BaseTranslator)
                    except Exception:
                        pass

                spec.loader.exec_module = _exec
            return spec

    if not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())


# 解释器启动即生效（sitecustomize 会 import 本模块）。
# install() 只打补丁、不加载术语表，所以启动很快；
# 术语表在第一次真正翻译时才加载（见 apply_glossary）。
try:
    if os.environ.get("PDF2ZH_NO_GLOSSARY") != "1":
        install()
except Exception:
    pass

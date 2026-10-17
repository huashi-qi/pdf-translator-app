# -*- coding: utf-8 -*-
"""
翻译工作进程 —— 由界面调用，通过 stdout 输出 JSON 进度。

为什么单独开进程：pdf2zh_next 的 CLI 在被管道捕获时只输出最终行，
拿不到增量进度；而 Python API do_translate_async_stream 会 yield
带 overall_progress 的事件。所以用它来驱动真实进度条。

用法：python.exe worker.py <pdf2zh_next 的参数...>
输出：@@P@@ {json}  进度事件；其余行为普通日志
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 缓存重定向：sitecustomize.py 会在解释器启动时改写 babeldoc.CACHE_FOLDER。
# 这里只做兜底（万一 sitecustomize 没生效），且不改 HOME ——
# 改 HOME 会让 Tk 文件对话框去找不存在的 <cache>\Desktop 而报「位置不可用」。
_here = Path(__file__).resolve().parent
_cache = _here / "cache"
os.environ.setdefault("BABELDOC_CACHE_DIR", str(_cache / ".cache" / "babeldoc"))
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

try:
    import babeldoc.const as _bc
    _bc.CACHE_FOLDER = _cache / ".cache" / "babeldoc"
except Exception:
    pass


def emit(kind: str, payload: dict) -> None:
    try:
        sys.stdout.write("@@P@@ " + json.dumps({"kind": kind, **payload}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


# 阶段名中英对照（与 app.py 里的保持一致，日志和界面都显示中文）
_STAGE_CN = {
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


def _stage_cn(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    if n in _STAGE_CN:
        return _STAGE_CN[n]
    for k, v in _STAGE_CN.items():
        if k.lower() in n.lower() or n.lower() in k.lower():
            return v
    return n


class _CacheWatcher:
    """读取「翻译段数 / 缓存命中段数」。

    为什么不解析日志：babeldoc 自己的计数只在 __del__ 里用 logger.info 打印，
    子进程退出太快时经常来不及输出（实测抓不到）。
    所以 glossary_hook 在打补丁时顺手做了真实计数，这里直接读。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.hits = 0

    def install(self) -> None:
        # 触发一次 base_translator 的导入钩子，确保补丁已装上
        try:
            import glossary_hook
            glossary_hook.install()
        except Exception:
            pass

    def report(self) -> None:
        try:
            import glossary_hook
            st = glossary_hook.get_stats()
            self.calls = int(st.get("calls", 0))
            self.hits = int(st.get("hits", 0))
        except Exception:
            pass
        if self.calls or self.hits:
            emit("cache", {"calls": self.calls, "hits": self.hits})


def _stats_from_subprocess() -> dict:
    """翻译实际跑在子进程里，父进程读它留下的统计文件。

    注意时序：子进程是在自己的 atexit 里写文件，可能比 finish 事件稍晚。
    所以这里最多等 2 秒，看文件是否有更新。
    """
    import time as _t
    try:
        import glossary_hook
        p = glossary_hook._STATS_FILE
    except Exception:
        return {"calls": 0, "hits": 0}

    deadline = _t.time() + 2.0
    last = {"calls": 0, "hits": 0}
    while _t.time() < deadline:
        try:
            if p.exists():
                import json as _json
                d = _json.loads(p.read_text(encoding="utf-8"))
                if d.get("calls") or d.get("hits"):
                    last = d
                    # 拿到有效数据就返回
                    return {"calls": int(d.get("calls", 0)), "hits": int(d.get("hits", 0))}
        except Exception:
            pass
        _t.sleep(0.1)
    return last


async def run() -> int:
    from pdf2zh_next.config import ConfigManager
    from pdf2zh_next.high_level import do_translate_async_stream

    watcher = _CacheWatcher()
    watcher.install()
    # 清掉上一次的统计
    try:
        import glossary_hook
        glossary_hook.clear_stats_file()
    except Exception:
        pass

    settings = ConfigManager().initialize_config()
    # 让进度事件密一些，进度条才顺滑
    try:
        settings.report_interval = 0.3
    except Exception:
        pass

    files = list(getattr(settings.basic, "input_files", []) or [])
    if not files:
        emit("error", {"error": "没有输入文件"})
        return 2

    emit("start", {"files": [str(f) for f in files]})

    last_overall = -1.0
    # 阶段耗时统计：同名阶段累加（进度事件会多次进入同一阶段）
    stage_times: dict = {}
    stage_order: list = []
    cur_stage = None
    cur_start = time.time()

    def _close_stage():
        """收尾当前阶段，把耗时累加进去。"""
        nonlocal cur_stage, cur_start
        if cur_stage:
            dt = time.time() - cur_start
            stage_times[cur_stage] = stage_times.get(cur_stage, 0.0) + dt
            if cur_stage not in stage_order:
                stage_order.append(cur_stage)
        cur_stage = None

    for f in files:
        try:
            async for event in do_translate_async_stream(settings, Path(f)):
                etype = event.get("type")
                if etype in ("progress_start", "progress_update", "progress_end"):
                    overall = float(event.get("overall_progress") or 0.0)
                    stage = str(event.get("stage") or "")
                    # 阶段切换 → 结算上一段耗时
                    if stage != cur_stage:
                        _close_stage()
                        cur_stage = stage
                        cur_start = time.time()
                        # 同时发英文原名（供统计）和中文名（供显示）
                        emit("stage", {"name": stage, "state": "begin",
                                       "cn": _stage_cn(stage)})
                    # 只在有意义的变化时输出，避免刷屏
                    if overall >= last_overall + 0.05 or overall >= 100 or etype == "progress_start":
                        last_overall = overall
                        emit("progress", {
                            "overall": round(overall, 2),
                            "stage": stage,
                            "stage_current": event.get("stage_current"),
                            "stage_total": event.get("stage_total"),
                            "part_index": event.get("part_index"),
                            "total_parts": event.get("total_parts"),
                        })
                elif etype == "finish":
                    _close_stage()
                    total = sum(stage_times.values()) or 1.0
                    emit("timing", {
                        "total": round(total, 2),
                        "stages": [
                            {"name": n, "seconds": round(stage_times.get(n, 0.0), 2),
                             "percent": round(stage_times.get(n, 0.0) / total * 100.0, 1)}
                            for n in stage_order
                        ],
                    })
                    emit("progress", {"overall": 100.0, "stage": "完成"})
                    # 翻译在子进程里跑，先读它落盘的统计，读不到再用内存计数
                    st = _stats_from_subprocess()
                    if not (st.get("calls") or st.get("hits")):
                        try:
                            import glossary_hook
                            st = glossary_hook.get_stats()
                        except Exception:
                            st = {"calls": 0, "hits": 0}
                    if st.get("calls") or st.get("hits"):
                        emit("cache", {"calls": int(st.get("calls", 0)),
                                       "hits": int(st.get("hits", 0))})
                    watcher.report()
                    emit("finish", {})
                elif etype == "error":
                    emit("error", {"error": str(event.get("error") or "未知错误")})
                    return 1
        except Exception as exc:
            emit("error", {"error": str(exc)})
            return 1
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        import traceback
        emit("error", {"error": str(exc), "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())

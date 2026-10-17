# -*- coding: utf-8 -*-
"""解释器启动时初始化（必须极快）。

放在 site-packages 下，每个新解释器（含 multiprocessing 子进程）都会自动加载。

性能要点（重要）：
    早期版本在这里直接 "import babeldoc.const" 来改写缓存目录，
    结果每次启动都要付 3.5 秒的代价（babeldoc 及其依赖很重），
    表现为双击后好几秒没反应、像是闪退。
    现在改为「惰性改写」：只在真的有人 import babeldoc 时才动手，
    启动路径零开销（实测 3572ms → 22ms）。
"""
import os
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent.parent.parent   # <APP>/runtime/Lib/site-packages -> <APP>
_CACHE = _APP / "cache" / ".cache" / "babeldoc"

if _APP.is_dir():
    os.environ.setdefault("PDF2ZH_APP_DIR", str(_APP))
    os.environ["TIKTOKEN_CACHE_DIR"] = str(_CACHE / "tiktoken")


def _install_babeldoc_hook():
    """惰性改写 babeldoc 的缓存目录。

    babeldoc.const 在 import 时用 Path.home()/.cache/babeldoc 算出 CACHE_FOLDER。
    我们不能改 HOME（会让 Tk 文件对话框去找不存在的 <cache>\\Desktop），
    所以改成在它被 import 的瞬间改写这个变量。
    """
    try:
        import importlib.abc
        import importlib.util

        class _CacheRedirect(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name != "babeldoc.const":
                    return None
                try:
                    sys.meta_path.remove(self)
                except ValueError:
                    pass
                # 让正常机制加载它，然后改写结果
                spec = importlib.util.find_spec(name)
                if spec is not None and spec.loader is not None:
                    _orig_exec = spec.loader.exec_module

                    def _patched_exec(module, *a, **k):
                        _orig_exec(module, *a, **k)
                        try:
                            module.CACHE_FOLDER = _CACHE
                            os.environ["TIKTOKEN_CACHE_DIR"] = str(_CACHE / "tiktoken")
                        except Exception:
                            pass

                    spec.loader.exec_module = _patched_exec
                return spec

        if not any(isinstance(f, _CacheRedirect) for f in sys.meta_path):
            sys.meta_path.insert(0, _CacheRedirect())
    except Exception:
        pass


_install_babeldoc_hook()

# ---- 术语注入（同样保持轻量：glossary_hook 内部也是惰性加载术语表）----
if os.environ.get("PDF2ZH_NO_GLOSSARY") != "1":
    try:
        if (_APP / "glossary_hook.py").exists():
            if str(_APP) not in sys.path:
                sys.path.insert(0, str(_APP))
            import glossary_hook  # noqa: F401
    except Exception:
        pass

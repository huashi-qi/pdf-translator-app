# -*- coding: utf-8 -*-
"""pytest 公共夹具。"""
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@pytest.fixture(scope="session")
def app_dir():
    return APP_DIR


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    """生成一个两页 PDF，页面上放英文字。"""
    import fitz

    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    doc = fitz.open()
    for i, text in enumerate((
        "The buck converter uses a step-down topology.\nIt has soft-start.",
        "Output voltage is 3.3 V, inductor 4.7 uH, capacitor 22 uF.",
    )):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 120), text, fontsize=12)
        page.insert_text((72, 160), "Page %d" % (i + 1), fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def fresh_glossary(monkeypatch):
    """重置 glossary_hook 的模块状态，避免用例间互相影响。"""
    import glossary_hook as gh

    monkeypatch.setattr(gh, "_loaded", False, raising=False)
    monkeypatch.setattr(gh, "_entries", [], raising=False)
    monkeypatch.setattr(gh, "_pattern", None, raising=False)
    monkeypatch.setattr(gh, "_enabled", True, raising=False)
    return gh

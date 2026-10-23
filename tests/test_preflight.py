# -*- coding: utf-8 -*-
"""预检与预估功能测试（①②③⑥）。"""

import pytest

import core


class TestCheckSourcePdf:
    """① 源文件校验。"""

    def test_missing_file(self, tmp_path):
        r = core.check_source_pdf(tmp_path / "nope.pdf")
        assert r["ok"] is False
        assert "找不到" in r["title"]
        assert "移动" in r["advice"] or "删除" in r["advice"]

    def test_directory_instead_of_file(self, tmp_path):
        d = tmp_path / "folder"
        d.mkdir()
        r = core.check_source_pdf(d)
        assert r["ok"] is False
        assert "文件夹" in r["advice"]

    def test_non_pdf_extension(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        r = core.check_source_pdf(f)
        assert r["ok"] is False
        assert "PDF" in r["title"]

    def test_valid_pdf(self, sample_pdf):
        r = core.check_source_pdf(sample_pdf)
        assert r["ok"] is True
        assert r.get("pages") == 2

    def test_invalid_path_type(self):
        r = core.check_source_pdf(None)
        assert r["ok"] is False

    def test_broken_pdf_does_not_crash(self, tmp_path):
        """损坏的 PDF 不该让预检崩溃（交给后续流程报错）。"""
        f = tmp_path / "broken.pdf"
        f.write_bytes(b"not a real pdf")
        r = core.check_source_pdf(f)
        assert isinstance(r, dict)
        assert "ok" in r


class TestCheckOutputDir:
    """③ 保存目录可写校验。"""

    def test_writable_dir(self, tmp_path):
        r = core.check_output_dir(tmp_path)
        assert r["ok"] is True

    def test_creates_missing_dir(self, tmp_path):
        d = tmp_path / "new" / "deep"
        r = core.check_output_dir(d)
        assert r["ok"] is True
        assert d.exists()

    def test_probe_file_cleaned_up(self, tmp_path):
        core.check_output_dir(tmp_path)
        left = list(tmp_path.glob(".pdf2zh_write_test"))
        assert left == [], "探针文件应被清理"

    def test_invalid_dir(self):
        r = core.check_output_dir("\x00invalid")
        assert r["ok"] is False
        assert r.get("advice")


class TestPreflight:
    """组合预检。"""

    def test_both_ok(self, sample_pdf, tmp_path):
        r = core.preflight(sample_pdf, tmp_path)
        assert r["ok"] is True
        assert r.get("pages") == 2

    def test_missing_source_blocks(self, tmp_path):
        r = core.preflight(tmp_path / "nope.pdf", tmp_path)
        assert r["ok"] is False

    def test_bad_outdir_blocks(self, sample_pdf):
        r = core.preflight(sample_pdf, "\x00invalid")
        assert r["ok"] is False


class TestEstimateWorkload:
    """⑥ 工作量预估。"""

    def test_zero_pages(self):
        assert core.estimate_workload(0) == ""

    def test_none_pages(self):
        assert core.estimate_workload(None) == ""

    def test_small_doc_under_a_minute(self):
        """1~3 页统一说「1 分钟内」。"""
        for n in (1, 2, 3):
            s = core.estimate_workload(n)
            assert "%d 页" % n in s
            assert "1 分钟内" in s, s

    def test_medium_doc_gives_range(self):
        """4 页以上给出区间。"""
        s = core.estimate_workload(20)
        assert "20 页" in s
        assert "~" in s
        assert "分钟" in s

    def test_medium_doc(self):
        s = core.estimate_workload(20)
        assert "20 页" in s
        assert "分钟" in s

    def test_large_doc(self):
        s = core.estimate_workload(144)
        assert "144 页" in s
        assert "分钟" in s

    def test_estimate_grows_with_pages(self):
        import re
        def first_num(s):
            m = re.search(r"(\d+)~", s)
            return int(m.group(1)) if m else 0
        small = first_num(core.estimate_workload(20))
        large = first_num(core.estimate_workload(100))
        assert large > small

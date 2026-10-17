# -*- coding: utf-8 -*-
"""core 新增能力测试：队列持久化 / 断点续译 / ETA / 诊断包。"""

import json
from pathlib import Path

import pytest

import core


class TestPagesSpec:
    def test_simple_range(self):
        assert core.expand_pages_spec("1-5") == [1, 2, 3, 4, 5]

    def test_mixed(self):
        assert core.expand_pages_spec("1-3,7,9-10") == [1, 2, 3, 7, 9, 10]

    def test_single(self):
        assert core.expand_pages_spec("4") == [4]

    def test_empty(self):
        assert core.expand_pages_spec("") == []
        assert core.expand_pages_spec("   ") == []

    def test_open_ended_with_total(self):
        assert core.expand_pages_spec("20-", 24) == [20, 21, 22, 23, 24]

    def test_open_ended_without_total_degrades(self):
        """不知道总页数时，开放区间退化成单页（不能瞎猜）。"""
        assert core.expand_pages_spec("20-") == [20]

    def test_clamp_to_total(self):
        assert core.expand_pages_spec("1-99", 5) == [1, 2, 3, 4, 5]

    def test_chinese_comma(self):
        assert core.expand_pages_spec("1-2，5") == [1, 2, 5]

    def test_dedup_and_sort(self):
        assert core.expand_pages_spec("3,1,2,1") == [1, 2, 3]

    def test_pages_to_spec_roundtrip(self):
        pages = [1, 2, 3, 7, 9, 10]
        spec = core.pages_to_spec(pages)
        assert core.expand_pages_spec(spec) == pages

    def test_remaining_pages(self):
        assert core.remaining_pages(10, [1, 2, 3]) == "4-10"
        assert core.remaining_pages(5, []) == "1-5"
        assert core.remaining_pages(0, []) == ""


class TestQueue:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "QUEUE_FILE", tmp_path / "q.json")
        items = [{"path": "a.pdf", "name": "a.pdf"},
                 {"path": "b.pdf", "name": "b.pdf"}]
        assert core.save_queue(items) is True
        got = core.load_queue()
        assert len(got) == 2
        assert got[0]["path"] == "a.pdf"

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "QUEUE_FILE", tmp_path / "none.json")
        assert core.load_queue() == []

    def test_clear(self, tmp_path, monkeypatch):
        qf = tmp_path / "q.json"
        monkeypatch.setattr(core, "QUEUE_FILE", qf)
        core.save_queue([{"path": "a.pdf"}])
        core.clear_queue()
        assert core.load_queue() == []

    def test_skips_items_without_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "QUEUE_FILE", tmp_path / "q.json")
        core.save_queue([{"path": "a.pdf"}, {"name": "no-path"}])
        assert len(core.load_queue()) == 1


class TestProgress:
    def test_save_and_get(self, tmp_path, monkeypatch, sample_pdf):
        monkeypatch.setattr(core, "PROGRESS_FILE", tmp_path / "p.json")
        core.save_progress(str(sample_pdf), [1], 2)
        rec = core.get_progress(str(sample_pdf))
        assert rec is not None
        assert rec["done_pages"] == [1]
        assert rec["total"] == 2

    def test_all_done_returns_none(self, tmp_path, monkeypatch, sample_pdf):
        """全部翻完后不该再提示续译。"""
        monkeypatch.setattr(core, "PROGRESS_FILE", tmp_path / "p.json")
        core.save_progress(str(sample_pdf), [1, 2], 2)
        assert core.get_progress(str(sample_pdf)) is None

    def test_clear(self, tmp_path, monkeypatch, sample_pdf):
        monkeypatch.setattr(core, "PROGRESS_FILE", tmp_path / "p.json")
        core.save_progress(str(sample_pdf), [1], 2)
        core.clear_progress(str(sample_pdf))
        assert core.get_progress(str(sample_pdf)) is None

    def test_missing_file_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "PROGRESS_FILE", tmp_path / "p.json")
        assert core.get_progress("不存在的.pdf") is None


class TestETA:
    def test_early_returns_none(self):
        """样本太少时不给估算，避免瞎猜。"""
        assert core.estimate_eta(1, 0.5) is None
        assert core.estimate_eta(10, 1) is None

    def test_near_complete_returns_zero(self):
        assert core.estimate_eta(100, 99.9) == 0.0

    def test_returns_positive(self):
        v = core.estimate_eta(60, 45, "Translate Paragraphs",
                              [{"name": "Parse PDF"}])
        assert v is not None and v > 0

    def test_decreases_with_progress(self):
        """进度越靠后，剩余时间应越短。"""
        early = core.estimate_eta(20, 20, "Parse Page Layout", [{"name": "Parse PDF"}])
        late = core.estimate_eta(90, 85, "Typesetting", [
            {"name": "Parse PDF"}, {"name": "Parse Page Layout"},
            {"name": "Translate Paragraphs"}])
        assert early is not None and late is not None
        assert late < early

    def test_capped_at_24h(self):
        v = core.estimate_eta(10, 2, "unknown")
        assert v is None or v <= 24 * 3600

    def test_fmt_duration(self):
        assert core.fmt_duration(None) == "估算中"
        assert core.fmt_duration(45) == "45 秒"
        assert core.fmt_duration(83) == "1 分 23 秒"
        assert "小时" in core.fmt_duration(3700)


class TestDiagnostic:
    def test_builds_file(self, tmp_path):
        out = tmp_path / "diag.txt"
        p = core.build_diagnostic("测试日志\n第二行", str(out))
        assert p is not None and Path(p).exists()
        txt = Path(p).read_text(encoding="utf-8")
        assert "诊断报告" in txt
        assert "运行环境" in txt
        assert "测试日志" in txt

    def test_no_secret_leak(self, tmp_path):
        """诊断包里不能出现 api_key 的真实值。"""
        out = tmp_path / "diag.txt"
        p = core.build_diagnostic("", str(out))
        txt = Path(p).read_text(encoding="utf-8")
        # 字段名会出现，但值必须是空的或 ***
        for line in txt.splitlines():
            if "api_key" in line:
                assert "***" in line or line.strip().endswith("api_key"), \
                    "api_key 行不应出现真实值: %r" % line

    def test_masks_home_path(self, tmp_path):
        out = tmp_path / "diag.txt"
        p = core.build_diagnostic("", str(out))
        txt = Path(p).read_text(encoding="utf-8")
        # 程序目录只保留末两段，不该出现完整盘符路径
        assert "C:\\Users" not in txt

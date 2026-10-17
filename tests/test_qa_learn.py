# -*- coding: utf-8 -*-
"""qa / learn_check 测试：质量检查、扫描检测、术语提取。"""

import pytest

import qa
import learn_check as lc


def test_detect_scanned_text_pdf(sample_pdf):
    r = qa.detect_scanned(str(sample_pdf))
    assert isinstance(r, dict)
    assert r.get("pages") == 2
    assert r.get("scanned") is False, "有文字的 PDF 不应被判为扫描件"


def test_detect_scanned_missing_file():
    r = qa.detect_scanned("不存在的文件.pdf")
    assert isinstance(r, dict)


def test_toc_pages_spec_returns_str(sample_pdf):
    s = qa.toc_pages_spec(str(sample_pdf))
    assert isinstance(s, str)


def test_detect_toc_pages_returns_list(sample_pdf):
    r = qa.detect_toc_pages(str(sample_pdf))
    assert isinstance(r, list)


def test_extract_pairs_same_file(sample_pdf):
    pairs = qa.extract_pairs(str(sample_pdf), str(sample_pdf), page=1)
    assert isinstance(pairs, list)


def test_check_consistency_same_file(sample_pdf):
    issues = qa.check_consistency(str(sample_pdf), str(sample_pdf))
    assert isinstance(issues, list)


def test_audit_glossary_on_builtin(app_dir):
    r = qa.audit_glossary(str(app_dir / "glossary.csv"))
    assert isinstance(r, dict)


def test_extract_candidates_finds_repeats():
    text = "ADC ADC ADC DAC DAC opamp opamp opamp filter filter"
    cands = lc.extract_candidates(text, top=10)
    terms = [t for t, _ in cands]
    assert "ADC" in terms, "高频词 ADC 应被提取"
    for term, cnt in cands:
        assert cnt >= 2, "候选词至少出现 2 次"


def test_extract_candidates_skips_stopwords():
    text = "the the the and and of of to to"
    cands = lc.extract_candidates(text, top=10)
    terms = [t.lower() for t, _ in cands]
    for w in ("the", "and", "of", "to"):
        assert w not in terms, "停用词不应进入候选：%s" % w


def test_extract_candidates_empty_text():
    assert lc.extract_candidates("", top=10) == []


def test_learn_from_pdf_returns_list(sample_pdf):
    r = lc.learn_from_pdf(str(sample_pdf), set(), top=10)
    assert isinstance(r, list)


def test_check_quality_same_file(sample_pdf):
    r = lc.check_quality(str(sample_pdf), str(sample_pdf))
    assert isinstance(r, list)

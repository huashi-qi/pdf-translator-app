# -*- coding: utf-8 -*-
"""glossary_hook 测试：术语替换 / 词边界 / 占位符还原 / 统计。"""

import pytest


def test_load_glossary_returns_count(fresh_glossary):
    n = fresh_glossary.load_glossary()
    assert n > 0, "内置术语表应能加载出词条"


def test_lookup_maps_en_to_cn(fresh_glossary):
    fresh_glossary.load_glossary()
    m = fresh_glossary._lookup()
    assert len(m) > 0
    for k, v in list(m.items())[:5]:
        assert isinstance(k, str) and isinstance(v, str)
        assert k.strip() and v.strip()


def test_apply_replaces_terms_with_placeholders(fresh_glossary):
    """命中术语后，原文里的词应被替换成 @@Gn@@ 占位符。"""
    fresh_glossary.load_glossary()
    src = "The buck converter has soft-start and output voltage."
    replaced, mapping = fresh_glossary.apply_glossary(src)
    assert mapping, "应至少命中一个术语"
    assert "@@" in replaced, "命中后应出现占位符"
    for ph in mapping:
        assert ph in replaced, "占位符 %s 应在替换后的文本里" % ph


def test_restore_puts_chinese_back(fresh_glossary):
    """restore 的职责是把占位符还原成【中文】译法（不是还原英文）。"""
    fresh_glossary.load_glossary()
    src = "The buck converter has soft-start."
    replaced, mapping = fresh_glossary.apply_glossary(src)
    back = fresh_glossary.restore_glossary(replaced, mapping)
    assert "@@" not in back, "还原后不应残留占位符"
    for cn in mapping.values():
        assert cn in back, "还原后应含中文译法 %s" % cn


def test_restore_is_lossless_for_original_words(fresh_glossary):
    """还原后的文本 = 原文中每个占位符换成对应中文。"""
    fresh_glossary.load_glossary()
    src = "The buck converter works."
    replaced, mapping = fresh_glossary.apply_glossary(src)
    expected = replaced
    for ph, cn in mapping.items():
        expected = expected.replace(ph, cn)
    assert fresh_glossary.restore_glossary(replaced, mapping) == expected


def test_apply_no_match_returns_same_text(fresh_glossary):
    fresh_glossary.load_glossary()
    src = "zzz qqq xxx"
    replaced, mapping = fresh_glossary.apply_glossary(src)
    assert mapping == {}
    assert replaced == src


def test_word_boundary_no_false_positive(fresh_glossary):
    """词边界必须生效：PASS / LENGTH 不能被拆成术语。

    这是实际踩过的坑 —— 缺词边界时 PASS 会变成 PA接地。
    """
    fresh_glossary.load_glossary()
    for word in ("PASS", "LENGTH", "CLASS", "GLASS"):
        replaced, mapping = fresh_glossary.apply_glossary(word)
        assert mapping == {}, "%s 不应命中术语，实际 %r" % (word, mapping)
        assert replaced == word, "%s 不应被改写，实际 %r" % (word, replaced)


def test_empty_text_is_safe(fresh_glossary):
    fresh_glossary.load_glossary()
    assert fresh_glossary.apply_glossary("") == ("", {})


def test_restore_with_empty_mapping(fresh_glossary):
    assert fresh_glossary.restore_glossary("abc", {}) == "abc"


def test_stats_file_roundtrip(fresh_glossary, tmp_path, monkeypatch):
    sf = tmp_path / "stats.json"
    monkeypatch.setattr(fresh_glossary, "_STATS_FILE", sf, raising=False)
    fresh_glossary.clear_stats_file()
    assert fresh_glossary.read_stats_file().get("calls", 0) == 0


def test_format_term_report_handles_empty():
    import qa
    lines = qa.format_term_report({}, 1100)
    assert isinstance(lines, list)


def test_format_term_report_lists_terms():
    import qa
    lines = qa.format_term_report({"buck converter": 5}, 1100, limit=10)
    joined = "\n".join(lines)
    assert "buck converter" in joined or "5" in joined

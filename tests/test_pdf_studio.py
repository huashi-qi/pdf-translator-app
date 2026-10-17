# -*- coding: utf-8 -*-
"""pdf_studio 测试：文字块识别 / 移动 / 撤销栈 / 页面操作。"""

import pytest

import pdf_studio as PS


@pytest.fixture
def studio(sample_pdf):
    doc = PS.PdfStudio(str(sample_pdf))
    yield doc
    try:
        doc.close()
    except Exception:
        pass


def test_open_and_page_count(studio):
    assert studio.pages == 2


def test_text_blocks_detected(studio):
    blocks = studio.text_blocks(0)
    assert len(blocks) > 0, "第一页应识别出文字块"
    for b in blocks:
        assert hasattr(b, "text")
        assert len(b.bbox) == 4


def test_block_at_hits_inside_bbox(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    b = blocks[0]
    x0, y0, x1, y1 = b.bbox
    hit = studio.block_at(0, (x0 + x1) / 2, (y0 + y1) / 2)
    assert hit is not None, "点击文字块中心应命中"


def test_block_at_misses_outside(studio):
    assert studio.block_at(0, -500, -500) is None


def test_blocks_in_rect_selects(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    x0, y0, x1, y1 = blocks[0].bbox
    got = studio.blocks_in(0, (x0 - 5, y0 - 5, x1 + 5, y1 + 5))
    assert len(got) >= 1, "框住文字块应能选中"


def test_blocks_in_empty_rect(studio):
    got = studio.blocks_in(0, (-900, -900, -800, -800))
    assert got == []


def test_move_block_marks_dirty(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    before = len(studio.history())
    studio.move_blocks([blocks[0]], 10, 5)
    assert len(studio.history()) > before, "移动后应产生撤销记录"


def test_move_zero_does_not_add_history(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    before = len(studio.history())
    studio.move_blocks([blocks[0]], 0, 0)
    assert len(studio.history()) == before, "原地移动不应产生历史"


def test_undo_redo_roundtrip(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    n0 = len(studio.history())
    studio.move_blocks([blocks[0]], 20, 20)
    assert len(studio.history()) > n0
    assert studio.undo() is True
    assert studio.redo() is True


def test_undo_on_fresh_doc_is_false(sample_pdf):
    doc = PS.PdfStudio(str(sample_pdf))
    try:
        assert doc.undo() is False, "没有操作时撤销应返回 False"
        assert doc.redo() is False
    finally:
        doc.close()


def test_whiteout_adds_history(studio):
    before = len(studio.history())
    studio.whiteout(0, (50, 50, 150, 90))
    assert len(studio.history()) > before


def test_draw_line_and_rect(studio):
    before = len(studio.history())
    studio.draw_line(0, (50, 100), (250, 100))
    studio.draw_rect(0, (60, 110, 200, 160))
    assert len(studio.history()) >= before + 2


def test_insert_text_adds_history(studio):
    before = len(studio.history())
    studio.insert_text(0, 100, 300, "测试文字", size=12)
    assert len(studio.history()) > before


def test_rotate_page(studio):
    studio.rotate_page(0, 90)
    assert len(studio.history()) > 0


def test_insert_blank_page_changes_count(studio):
    n = studio.pages
    studio.insert_blank(0)
    assert studio.pages == n + 1


def test_delete_page_changes_count(studio):
    n = studio.pages
    studio.delete_page(0)
    assert studio.pages == n - 1


def test_save_as_creates_file(studio, tmp_path):
    out = tmp_path / "out.pdf"
    studio.text_blocks(0)
    ok = studio.save_as(str(out))
    assert ok is not False
    assert out.exists() and out.stat().st_size > 0, "另存为应生成非空文件"


def test_resolve_returns_list(studio):
    blocks = studio.text_blocks(0)
    assert blocks, "没有文字块"
    studio.move_blocks(blocks[:1], 3, 3)
    r = studio.resolve(blocks)
    assert isinstance(r, list)


def test_page_clamp_out_of_range(studio):
    """越界页码应被夹到合法范围，不抛异常。"""
    r = studio.whiteout(999, (10, 10, 50, 50))
    assert r in (True, False)

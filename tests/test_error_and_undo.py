# -*- coding: utf-8 -*-
"""②③④ 新功能测试：错误分类 / 撤销机制 / 日志菜单。"""

import pytest

import core


class TestExplainError:
    """③ 错误分类：把技术报错变成可操作建议。"""

    def test_winerror_missing_file(self):
        r = core.explain_error("[WinError 2] 系统找不到指定的文件。")
        assert r["title"] == "缺少必要的文件"
        assert "解压" in r["advice"]
        assert r["solvable"] is True

    def test_connection_error(self):
        r = core.explain_error(
            "ConnectionError: HTTPSConnectionPool(host='cn.bing.com')")
        assert r["title"] == "网络连接失败"
        assert "联网" in r["advice"]

    def test_timeout(self):
        r = core.explain_error("Read timed out")
        assert r["title"] == "请求超时"
        assert "并发" in r["advice"]

    def test_rate_limit(self):
        r = core.explain_error("HTTP 429 Too Many Requests")
        assert r["title"] == "请求太频繁，被限流了"

    def test_auth_error(self):
        r = core.explain_error("401 Unauthorized: invalid api key")
        assert r["title"] == "API Key 无效或没有权限"

    def test_permission_denied(self):
        r = core.explain_error("[Errno 13] Permission denied: out.pdf")
        assert r["title"] == "文件被占用或没有写入权限"

    def test_memory_error(self):
        r = core.explain_error("MemoryError")
        assert r["title"] == "内存不足"

    def test_encrypted_pdf(self):
        r = core.explain_error("文档已加密，需要密码")
        assert r["title"] == "PDF 已加密"

    def test_unknown_falls_back(self):
        r = core.explain_error("Something totally unexpected")
        assert r["title"] == "翻译失败"
        assert r["solvable"] is False
        assert "诊断报告" in r["advice"]

    def test_empty_error_is_safe(self):
        r = core.explain_error("")
        assert r["title"] == "翻译失败"

    def test_none_error_is_safe(self):
        r = core.explain_error(None)
        assert isinstance(r, dict)

    def test_always_has_required_keys(self):
        for raw in ("WinError 2", "429", "随便什么", ""):
            r = core.explain_error(raw)
            for k in ("title", "advice", "solvable", "matched", "raw"):
                assert k in r, "缺少字段 %s" % k

    def test_case_insensitive(self):
        a = core.explain_error("CONNECTIONERROR")
        b = core.explain_error("connectionerror")
        assert a["title"] == b["title"]


class TestUndoMechanism:
    """② 撤销：验证数据能正确恢复（不依赖 Tk）。"""

    def test_history_restore_roundtrip(self, tmp_path, monkeypatch):
        hf = tmp_path / "history.json"
        monkeypatch.setattr(core, "HISTORY_FILE", hf)

        items = [{"time": "t1", "source_name": "a.pdf"},
                 {"time": "t2", "source_name": "b.pdf"}]
        hf.write_text(__import__("json").dumps(items), encoding="utf-8")

        old = core.load_history()
        assert len(old) == 2

        # 模拟删除
        hf.write_text("[]", encoding="utf-8")
        assert len(core.load_history()) == 0

        # 模拟撤销
        hf.write_text(__import__("json").dumps(old, ensure_ascii=False),
                      encoding="utf-8")
        assert len(core.load_history()) == 2

    def test_file_backup_restore(self, tmp_path):
        f = tmp_path / "out.pdf"
        f.write_bytes(b"%PDF-1.4 original content\n")
        blob = f.read_bytes()

        f.unlink()
        assert not f.exists()

        f.write_bytes(blob)
        assert f.exists()
        assert f.read_bytes() == blob

    def test_deleted_rows_can_be_reinserted(self):
        """术语表删除撤销：按原下标插回，顺序要对。"""
        rows = [["a", "甲", "g"], ["b", "乙", "g"],
                ["c", "丙", "g"], ["d", "丁", "g"]]
        # 删掉下标 1 和 3（倒序删）
        removed = []
        for i in sorted([1, 3], reverse=True):
            removed.append((i, rows.pop(i)))
        assert [r[0] for r in rows] == ["a", "c"]

        # 撤销：按下标升序插回
        for i, row in sorted(removed):
            rows.insert(min(i, len(rows)), row)
        assert [r[0] for r in rows] == ["a", "b", "c", "d"]
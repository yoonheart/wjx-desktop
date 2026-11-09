"""网络标记清除（MOTW）测试。

只在 Windows 跑：Zone.Identifier 是 NTFS 备用数据流，其他平台没有这个概念。
"""

from __future__ import annotations

import os
import sys

import pytest

from wjxdesk.shell.motw import clear_executable_mark, clear_network_marks

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Zone.Identifier 仅存在于 Windows NTFS"
)


def _mark(path) -> str:
    """给文件写一个「来自网络」标记，返回流路径。"""
    stream = os.fspath(path) + ":Zone.Identifier"
    with open(stream, "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    return stream


def _has(stream: str) -> bool:
    return os.path.exists(stream)


def test_clears_dll_mark(tmp_path):
    target = tmp_path / "Python.Runtime.dll"
    target.write_bytes(b"MZ")
    stream = _mark(target)
    assert _has(stream)

    assert clear_network_marks(tmp_path) == 1
    assert not _has(stream)


def test_clears_nested_files(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    dll = nested / "ClrLoader.dll"
    dll.write_bytes(b"MZ")
    pyd = nested / "fastapi.pyd"
    pyd.write_bytes(b"MZ")
    s1, s2 = _mark(dll), _mark(pyd)

    assert clear_network_marks(tmp_path) == 2
    assert not _has(s1) and not _has(s2)


def test_leaves_other_suffixes_alone(tmp_path):
    note = tmp_path / "使用说明.txt"
    note.write_text("x", encoding="utf-8")
    stream = _mark(note)

    assert clear_network_marks(tmp_path) == 0
    assert _has(stream)


def test_clean_tree_is_noop(tmp_path):
    (tmp_path / "a.dll").write_bytes(b"MZ")
    assert clear_network_marks(tmp_path) == 0


def test_missing_root_is_noop(tmp_path):
    assert clear_network_marks(tmp_path / "nope") == 0


def test_source_run_does_not_touch_anything():
    """源码运行时没有 _MEIPASS，默认调用必须什么都不做。"""
    assert clear_network_marks() == 0


def test_executable_mark_skipped_when_not_frozen():
    """源码运行时 sys.executable 是 python.exe，绝对不能动它。"""
    assert clear_executable_mark() is False

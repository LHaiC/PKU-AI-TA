from pathlib import Path

from crawler import extract


def test_rar_is_archive():
    assert extract._kind("hw2.rar") == "rar"
    assert extract.is_archive("HW2.RAR")


def test_missing_rar_tool_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    archive = tmp_path / "x.rar"
    archive.write_bytes(b"not really a rar")
    out_dir = tmp_path / "out"
    assert extract.extract_archive(archive, out_dir) == []

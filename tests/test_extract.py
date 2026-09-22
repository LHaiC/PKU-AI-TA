import gzip
import io
import tarfile
import zipfile
from pathlib import Path

from crawler.extract import dir_files, extract_archive, is_archive


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tgz_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_is_archive():
    assert is_archive("a.zip")
    assert is_archive("a.7z")
    assert is_archive("a.tar.gz")
    assert is_archive("a.tgz")
    assert is_archive("a.gz")
    assert not is_archive("a.pdf")
    assert not is_archive("a.log")


def test_zip_extracts_tree(tmp_path: Path):
    z = tmp_path / "sub.zip"
    z.write_bytes(_zip_bytes({"Logs/1_synth.log": b"log", "report.pdf": b"%PDF", "dir/": b""}))
    out = extract_archive(z, tmp_path / "x")
    names = {p.name for p in out}
    assert names == {"1_synth.log", "report.pdf"}
    assert (tmp_path / "x/Logs/1_synth.log").read_bytes() == b"log"


def test_zip_skips_traversal(tmp_path: Path):
    z = tmp_path / "evil.zip"
    z.write_bytes(_zip_bytes({"../escape.txt": b"x", "/abs.txt": b"y", "ok.txt": b"z"}))
    out = extract_archive(z, tmp_path / "x")
    assert [p.name for p in out] == ["ok.txt"]
    assert not (tmp_path / "escape.txt").exists()


def test_tgz_extracts(tmp_path: Path):
    t = tmp_path / "logs.tar.gz"
    t.write_bytes(_tgz_bytes({"logs/a.log": b"A"}))
    out = extract_archive(t, tmp_path / "x")
    assert [p.name for p in out] == ["a.log"]


def test_bare_gz_single_file(tmp_path: Path):
    g = tmp_path / "big.log.gz"
    g.write_bytes(gzip.compress(b"hello"))
    out = extract_archive(g, tmp_path / "x")
    assert [p.name for p in out] == ["big.log"]
    assert out[0].read_bytes() == b"hello"


def test_nested_archive_recursion(tmp_path: Path):
    inner = _zip_bytes({"deep/inner.log": b"inner"})
    outer = _zip_bytes({"outer.zip": inner, "top.txt": b"t"})
    z = tmp_path / "outer.zip"
    z.write_bytes(outer)
    out = extract_archive(z, tmp_path / "x")
    names = {p.name for p in out}
    assert {"outer.zip", "top.txt", "inner.log"} <= names


def test_7z_extracts(tmp_path: Path):
    import shutil, subprocess
    if not shutil.which("7z"):
        import pytest
        pytest.skip("7z not installed")
    src = tmp_path / "src" / "d"
    src.mkdir(parents=True)
    (src / "a.log").write_bytes(b"A")
    subprocess.run(["7z", "a", str(tmp_path / "s.7z"), str(src)], check=True,
                   capture_output=True)
    out = extract_archive(tmp_path / "s.7z", tmp_path / "x")
    assert "a.log" in {p.name for p in out}


def test_bad_zip_returns_empty(tmp_path: Path):
    z = tmp_path / "bad.zip"
    z.write_bytes(b"not a zip")
    assert extract_archive(z, tmp_path / "x") == []


def test_dir_files_sorted(tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b/y.txt").write_text("y")
    (tmp_path / "a.txt").write_text("a")
    assert [p.name for p in dir_files(tmp_path)] == ["a.txt", "y.txt"]

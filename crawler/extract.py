"""Extract student-submitted archives into plain files for the grader.

Students hand in .zip/.7z/.tar.gz bundles (often nested: a zip containing a
tar.gz of logs). The CLI grader is told not to run shell commands, so we
unpack everything here up front. Safety rails: member paths are sanitized
against traversal, individual files larger than _MAX_MEMBER_SIZE are skipped
(OpenROAD .odb/.spef dumps can be hundreds of MB and are ungradeable anyway),
and nested archives recurse to at most _MAX_DEPTH levels.
"""
from __future__ import annotations

import gzip
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

_MAX_MEMBER_SIZE = 100 * 1024 * 1024  # per extracted file
_MAX_DEPTH = 3

_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def _kind(name: str) -> str | None:
    low = name.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(_TAR_SUFFIXES):
        return "tar"
    if low.endswith(".gz"):
        return "gz_or_tar"
    if low.endswith(".7z"):
        return "7z"
    return None


def is_archive(name: str) -> bool:
    return _kind(name) is not None


def _safe_member(name: str) -> PurePosixPath | None:
    """Return a normalized relative member path, or None to skip."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or any(part == ".." for part in p.parts):
        return None
    parts = [part for part in p.parts if part not in (".", "")]
    if not parts:
        return None
    return PurePosixPath(*parts)


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 1000):
        cand = dest.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"too many name collisions for {dest}")


def _write(dest_dir: Path, rel: PurePosixPath, data: bytes, out: list[Path]) -> None:
    target = _unique(dest_dir / Path(*rel.parts))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    out.append(target)


def _extract_zip(path: Path, dest_dir: Path, out: list[Path]) -> None:
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size > _MAX_MEMBER_SIZE:
                continue
            rel = _safe_member(info.filename)
            if rel is None:
                continue
            _write(dest_dir, rel, zf.read(info.filename), out)


def _extract_tar(path: Path, dest_dir: Path, out: list[Path]) -> None:
    with tarfile.open(path, "r:*") as tf:
        for member in tf:
            if not member.isreg() or member.size > _MAX_MEMBER_SIZE:
                continue
            rel = _safe_member(member.name)
            if rel is None:
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            _write(dest_dir, rel, fobj.read(), out)


def _extract_gz_or_tar(path: Path, dest_dir: Path, out: list[Path]) -> None:
    try:
        _extract_tar(path, dest_dir, out)
        return
    except tarfile.TarError:
        pass
    data = gzip.open(path, "rb").read(_MAX_MEMBER_SIZE + 1)
    if len(data) > _MAX_MEMBER_SIZE:
        return
    name = path.name[: -len(".gz")] or path.name + ".out"
    _write(dest_dir, PurePosixPath(name), data, out)


def _extract_7z(path: Path, dest_dir: Path, out: list[Path]) -> None:
    before = {p for p in dest_dir.rglob("*") if p.is_file()} if dest_dir.exists() else set()
    subprocess.run(
        ["7z", "x", "-y", f"-o{dest_dir}", str(path)],
        capture_output=True, timeout=300, check=True,
    )
    for p in sorted(dest_dir.rglob("*")):
        if p.is_file() and p not in before and p.stat().st_size <= _MAX_MEMBER_SIZE:
            out.append(p)


def extract_archive(path: Path, dest_dir: Path, depth: int = 0) -> list[Path]:
    """Extract `path` into dest_dir (inner structure preserved) and return the
    list of files written. Nested archives are extracted recursively up to
    _MAX_DEPTH. Returns [] for non-archives or unhandled formats."""
    kind = _kind(path.name)
    if kind is None or depth >= _MAX_DEPTH:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    try:
        if kind == "zip":
            _extract_zip(path, dest_dir, out)
        elif kind in ("tar", "gz_or_tar"):
            _extract_gz_or_tar(path, dest_dir, out)
        elif kind == "7z":
            _extract_7z(path, dest_dir, out)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, subprocess.SubprocessError):
        return out
    # Recurse into nested archives extracted in this pass.
    for p in list(out):
        if is_archive(p.name):
            nested = extract_archive(p, p.parent / (p.stem + "_x"), depth + 1)
            out.extend(nested)
    return out


def open_archive_bytes(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """For callers that work on in-memory attachments: return
    (inner_name, bytes) pairs for one archive, non-recursive."""
    kind = _kind(filename)
    members: list[tuple[str, bytes]] = []
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > _MAX_MEMBER_SIZE:
                    continue
                rel = _safe_member(info.filename)
                if rel:
                    members.append((str(rel), zf.read(info.filename)))
    elif kind in ("tar", "gz_or_tar"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf:
                if m.isreg() and m.size <= _MAX_MEMBER_SIZE:
                    rel = _safe_member(m.name)
                    fobj = tf.extractfile(m)
                    if rel and fobj:
                        members.append((str(rel), fobj.read()))
    return members


def text_sidecar(path: Path) -> Path | None:
    """Write a `<name>.txt` text extraction next to a .pdf/.docx so text-only
    reader tools can grade it. Returns the sidecar path, or None if the file
    type isn't handled or extraction yields nothing."""
    low = path.name.lower()
    try:
        if low.endswith(".pdf"):
            import fitz  # pymupdf
            parts: list[str] = []
            with fitz.open(path) as doc:
                for i, page in enumerate(doc):
                    parts.append(f"--- page {i + 1} ---\n{page.get_text()}")
            text = "\n".join(parts).strip()
        elif low.endswith(".docx"):
            import docx  # python-docx
            doc = docx.Document(path)
            text = "\n".join(p.text for p in doc.paragraphs).strip()
        else:
            return None
    except Exception:
        return None
    if not text:
        return None
    sidecar = path.with_name(path.name + ".txt")
    sidecar.write_text(text, encoding="utf-8")
    return sidecar


def render_pdf_pages(path: Path, max_pages: int = 30, dpi: int = 110) -> list[Path]:
    """Render each page of a PDF to `<name>_pages/page-NN.png` so a
    vision-capable grader can inspect screenshots/figures. Skips PDFs with
    more than max_pages pages (returns [])."""
    if not path.name.lower().endswith(".pdf"):
        return []
    try:
        import fitz  # pymupdf
        out_dir = path.with_name(path.name + "_pages")
        out_dir.mkdir(exist_ok=True)
        out: list[Path] = []
        with fitz.open(path) as doc:
            if len(doc) > max_pages:
                return []
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            for i, page in enumerate(doc):
                png = out_dir / f"page-{i + 1:02d}.png"
                if not png.exists():
                    png.write_bytes(page.get_pixmap(matrix=mat).tobytes("png"))
                out.append(png)
        return out
    except Exception:
        return []


def extract_docx_media(path: Path) -> list[Path]:
    """Pull embedded images out of a .docx (it is a zip: word/media/*) into
    `<name>_media/` so the grader can view them."""
    if not path.name.lower().endswith(".docx"):
        return []
    try:
        out_dir = path.with_name(path.name + "_media")
        out: list[Path] = []
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.startswith("word/media/") or name.endswith("/"):
                    continue
                out_dir.mkdir(exist_ok=True)
                target = _unique(out_dir / Path(name).name)
                target.write_bytes(zf.read(name))
                out.append(target)
        return out
    except Exception:
        return []


def dir_files(dest_dir: Path) -> list[Path]:
    """All regular files under dest_dir, sorted — for the grader file list."""
    if not dest_dir.exists():
        return []
    return sorted(p for p in dest_dir.rglob("*") if p.is_file())

"""Persist crawled submissions to the workdir for human review and grading.

Layout per student:
  <workdir>/submissions/<assignment>/<sid>_<name>/originals/  raw uploads
  <workdir>/submissions/<assignment>/<sid>_<name>/grading/    everything the
      grader sees: extracted archive trees, .txt sidecars, rendered PDF pages,
      embedded docx media and the text answer.
"""
from __future__ import annotations

from pathlib import Path


def save_submissions(submissions: list, save_dir: Path, assignment_title: str) -> dict[str, list[Path]]:
    """Save each student's files to save_dir/assignment_title/ for human review.

    Saves every attachment plus a `_text.txt` for the text answer (if any).
    Returns a map of student_id -> saved file paths (used by CLI grader engines).
    """
    import re
    import shutil

    # ORFS/EDA binary artifacts are ungradeable and can crash a reader tool —
    # keep them on disk for humans but out of the grader's file list.
    _JUNK_EXT = {".odb", ".spef", ".sdf", ".lib", ".lef", ".def", ".db",
                 ".bin", ".a", ".o", ".so", ".v", ".sv", ".cdl", ".gds"}
    _MAX_LISTED_SIZE = 50 * 1024 * 1024

    def _gradeable(p: Path) -> bool:
        if is_archive(p.name) or p.suffix.lower() in _JUNK_EXT:
            return False
        try:
            return p.stat().st_size <= _MAX_LISTED_SIZE
        except OSError:
            return False

    from crawler.extract import (
        _unique, dir_files, extract_archive, extract_docx_media, is_archive,
        render_pdf_pages, text_sidecar,
    )
    safe_title = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', assignment_title)
    dest = save_dir / safe_title
    dest.mkdir(parents=True, exist_ok=True)
    files_map: dict[str, list[Path]] = {}
    for sub in submissions:
        # Per-student layout:
        #   <sid>_<name>/originals/  — the raw uploads, original filenames
        #   <sid>_<name>/grading/    — everything the grader sees: extracted
        #                            archive trees, non-archive copies,
        #                            .txt sidecars, rendered PDF pages,
        #                            embedded docx media, text answers
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', sub.student_name)
        sdir = dest / f"{sub.student_id}_{safe_name}"
        # Remove stale artifacts from previous runs, including the old flat
        # layout (<sid>_<name>.zip / _files/ / .pdf_pages/) at top level.
        for p in dest.iterdir():
            if p.name.startswith(f"{sub.student_id}_"):
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        originals = sdir / "originals"
        grading = sdir / "grading"
        originals.mkdir(parents=True)
        grading.mkdir()

        for att in sub.attachments:
            orig = _unique(originals / Path(att.filename).name)
            orig.write_bytes(att.data)
            if is_archive(att.filename):
                extract_archive(orig, grading)
            else:
                shutil.copy2(orig, grading / orig.name)

        # .pdf/.docx get a .txt sidecar plus rendered/embedded images so the
        # grader can read the text AND visually verify figures/screenshots.
        for p in list(dir_files(grading)):
            text_sidecar(p)
            render_pdf_pages(p)
            extract_docx_media(p)
        if sub.text_content.strip():
            (grading / f"{sub.student_id}_{safe_name}_text.txt").write_text(
                sub.text_content, encoding="utf-8")
        files_map[sub.student_id] = [p for p in dir_files(grading) if _gradeable(p)]
    return files_map

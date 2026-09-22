"""Per-assignment working-directory metadata.

`meta.json` sits beside the assignment's scores.xlsx and records the
Blackboard coordinates (course_id, gradeBookPK, title) plus the crawled
deadline, so later commands (decay/submit) don't need them re-passed.
"""
from __future__ import annotations

import json
from pathlib import Path

META_NAME = "meta.json"


def load_meta(workdir: Path) -> dict:
    try:
        return json.loads((workdir / META_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_meta(workdir: Path, **fields) -> None:
    """Merge fields into <workdir>/meta.json; empty values are ignored."""
    meta = load_meta(workdir)
    meta.update({k: v for k, v in fields.items() if v not in (None, "")})
    try:
        (workdir / META_NAME).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

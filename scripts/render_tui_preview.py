#!/usr/bin/env python3
"""Render the review TUI's student view to docs/tui-preview.svg using demo data.

Usage:
    uv run python scripts/render_tui_preview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console

from review.demo_data import create_demo_workspace
from review.tui import display_student
from review.tui_components import load_review_data

# Carol Wang: low confidence + uncertain parts, so the preview shows every panel.
PREVIEW_STUDENT_INDEX = 2


def main() -> None:
    scores, _submissions, _rubric = create_demo_workspace()
    _wb, _idx, rows = load_review_data(scores, needs_review_only=False, all_students=True)

    index = min(PREVIEW_STUDENT_INDEX, len(rows) - 1)
    _row_idx, row_data = rows[index]

    console = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    display_student(console, row_data, index + 1, len(rows))

    output = ROOT / "docs" / "tui-preview.svg"
    output.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(output), title="ta review — student view")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()

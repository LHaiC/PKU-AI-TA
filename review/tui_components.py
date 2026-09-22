"""
Components for the interactive TUI reviewer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import openpyxl
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

# Enable readline for better line editing (arrow keys, history, etc.)
try:
    import readline
    # Optional: Configure readline for better behavior
    readline.parse_and_bind('set editing-mode emacs')
except ImportError:
    # pyreadline3 (the Windows pyreadline fork) also ships a top-level
    # `readline` shim, so on Windows the import above usually succeeds once
    # the dependency is installed. Keep this explicit fallback for safety.
    try:
        import pyreadline3 as readline  # type: ignore
    except ImportError:
        # No readline available, but input() still works on Windows
        # Windows console has basic line editing support built-in
        pass


def prompt_text(prompt_label: str, default: str = "", console: Console | None = None,
                allow_interrupt: bool = False, prefill: bool = False) -> str:
    """Prompt for text input using Python's built-in input() for better IME and cursor support.

    This is better than rich.prompt.Prompt for:
    - Chinese/Japanese/CJK input (IME support)
    - Left/right arrow keys for cursor movement
    - Proper backspace handling with multi-byte characters

    With prefill=True the default is inserted into the input line itself
    (editable in place — Enter accepts it as-is); otherwise it is shown as a
    "(default: …)" hint and an empty input returns it.

    If allow_interrupt is True, KeyboardInterrupt will be raised instead of returning default.
    """
    # Print the prompt on its own line first to avoid readline cursor position issues
    if console:
        console.print(f"{prompt_label}", end="")
        if default and not prefill:
            console.print(f" [dim](default: {default})[/dim]", end="")
        console.print()
    else:
        print(f"{prompt_label}{f' (default: {default})' if default and not prefill else ''}")

    # Editable pre-fill via prompt_toolkit — the draft text sits in the input
    # buffer and Enter accepts it as-is. (Plain input() can't pre-fill; the
    # readline hooks for it don't exist on libedit, which this Python uses.)
    if prefill:
        try:
            from prompt_toolkit import prompt as pt_prompt
            result = pt_prompt("> ", default=default)
            return result.strip()
        except KeyboardInterrupt:
            if allow_interrupt:
                raise
            return default
        except EOFError:
            return default
        except Exception:
            pass  # fall through to plain input()

    try:
        result = input("> ")
        return result.strip() if result.strip() != "" else default
    except EOFError:
        print()
        return default
    except KeyboardInterrupt:
        print()
        if allow_interrupt:
            raise
        return default


def find_submission_file(submissions_dir: Path, student_id: str, student_name: str) -> Path | None:
    """Find submission file for a student by matching student_id in filename."""
    if not submissions_dir.exists():
        return None
    for assignment_dir in submissions_dir.iterdir():
        if not assignment_dir.is_dir() or assignment_dir.name.startswith((".", "_")):
            continue
        for f in assignment_dir.iterdir():
            # Per-student dir: return its originals/ (or the dir itself)
            if f.is_dir() and student_id in f.name:
                orig = f / "originals"
                return orig if orig.is_dir() else f
            if f.is_file() and not f.name.startswith(".") and student_id in f.name:
                return f
    return None


def open_file(filepath: Path, console: Console | None = None) -> None:
    """Open file with default system viewer. Cross-platform support."""
    import shutil
    filepath_str = str(filepath)
    try:
        if sys.platform == "darwin":  # macOS
            subprocess.run(["open", filepath_str], check=False)
        elif sys.platform == "win32":  # Windows
            os.startfile(filepath_str)  # type: ignore
        else:  # Linux / Unix variants
            # Try openers in order of preference, checking if they exist first
            for opener in ["xdg-open", "gio", "gnome-open", "kde-open"]:
                if shutil.which(opener):
                    try:
                        subprocess.run([opener, filepath_str], check=False,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except Exception:
                        continue
    except Exception as e:
        if console:
            console.print(f"[yellow]Warning: Could not open file ({e})[/yellow]")
            console.print(f"[dim]Please open manually: {filepath_str}[/dim]")

def needs_review_check(row_data: dict) -> bool:
    """Check if a student needs review even if already approved."""
    # Check if score is perfect (consider override if present)
    total_max = float(row_data.get("total_max", 100) or 100)
    override_score = row_data.get("reviewer_override_score")
    if override_score is not None and override_score != "":
        try:
            current_score = float(override_score)
        except (ValueError, TypeError):
            current_score = float(row_data.get("total_score", 0) or 0)
    else:
        current_score = float(row_data.get("total_score", 0) or 0)
    has_notes = bool(str(row_data.get("reviewer_notes") or "").strip())
    # Needs review if not perfect score and no notes, even if approved
    return current_score < total_max and not has_notes

def load_review_data(
    scores: Path,
    needs_review_only: bool,
    all_students: bool,
    below: float | None = None,
) -> tuple[Any, dict, list[tuple[int, dict]]]:
    """Load student data from scores spreadsheet and filter based on review status.

    Filter semantics:
    - needs_review_only: include rows with needs_review=YES OR non-empty
      uncertain_parts OR any non-perfect score (pct < 100) — every deduction
      must be eyeballed by a human. Derived from the row contents; the stored
      flag alone is not trusted (old exports predate the review_below rule).
    - below: a hard cap — only rows with pct < below pass, applied on top of
      whatever else matched. `--needs-review --below 95` thus drops flagged
      100-scores from the queue.

    Queue order: student_id ascending, matching the on-disk submission
    directories so locating a student's files is trivial.
    """
    wb = openpyxl.load_workbook(scores)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]
    idx = {name: i for i, name in enumerate(headers)}

    def _pct(row_data: dict) -> float:
        total_max = float(row_data.get("total_max") or 0)
        score = float(row_data.get("total_score") or 0)
        return score / total_max * 100 if total_max > 0 else 0.0

    rows: list[tuple[int, dict]] = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row[idx["student_id"]]:
            continue
        row_data = {name: row[i] if i < len(row) else None for name, i in idx.items()}
        pct = _pct(row_data)
        uncertain_raw = str(row_data.get("uncertain_parts_json") or "").strip()
        has_uncertain = uncertain_raw not in ("", "[]", "null")
        if (needs_review_only and row_data.get("needs_review") != "YES"
                and not has_uncertain and pct >= 100):
            continue
        if below is not None and pct >= below:
            continue
        # Skip approved students only if they don't need review for missing notes
        is_approved = str(row_data.get("approved", "")).upper() == "YES"
        if not all_students and is_approved and not needs_review_check(row_data):
            continue
        rows.append((row_idx, row_data))

    # Queue order: student_id ascending — matches submissions/ dir naming.
    rows.sort(key=lambda t: str(t[1].get("student_id") or ""))

    return wb, idx, rows

def auto_approve_students(ws: Any, idx: dict, console: Console) -> bool:
    """Auto-approve students with perfect scores who don't need review."""
    auto_approved = 0
    modified = False
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row[idx["student_id"]]:
            continue
        row_data = {name: row[i] if i < len(row) else None for name, i in idx.items()}
        # Check: 100 points, no flags/uncertainties, not already approved
        total_score = float(row_data.get("total_score", 0) or 0)
        total_max = float(row_data.get("total_max", 100) or 100)
        flagged = (row_data.get("needs_review") == "YES"
                   or str(row_data.get("uncertain_parts_json") or "").strip() not in ("", "[]", "null"))
        already_approved = str(row_data.get("approved", "")).upper() == "YES"

        if total_score >= total_max and not flagged and not already_approved:
            student_name = row_data.get("student_name", "")
            student_id = row_data.get("student_id", "")
            console.print(f"  [dim]Auto-approving:[/dim] {student_name} ({student_id}) — {total_score}/{total_max}")
            ws.cell(row=row_idx, column=idx["approved"] + 1, value="YES")
            auto_approved += 1
            modified = True

    if auto_approved > 0:
        console.print(f"[green]Auto-approved {auto_approved} student(s) with perfect scores.[/green]")
    
    return modified

class ReviewSession:
    """Manages the state of an interactive review session."""
    def __init__(self, scores: Path, needs_review_only: bool, all_students: bool, below: float | None = None):
        self.scores = scores
        self.wb, self.idx, self.rows = load_review_data(scores, needs_review_only, all_students, below)
        self.ws = self.wb.active
        self.current_idx = 0
        self.modified_rows: dict[int, dict] = {}
        self.modified = False

    def get_current_row(self) -> tuple[int, dict] | None:
        if 0 <= self.current_idx < len(self.rows):
            row_idx, row_data_original = self.rows[self.current_idx]
            row_data = self.modified_rows.get(row_idx, row_data_original.copy())
            return row_idx, row_data
        return None

    def next_student(self):
        self.current_idx += 1

    def prev_student(self):
        if self.current_idx > 0:
            self.current_idx -= 1

    def update_row(self, row_idx: int, row_data: dict):
        self.modified_rows[row_idx] = row_data
        self.modified = True

    def save_changes(self):
        """Write back ONLY the rows the reviewer touched, merged onto a fresh
        read of the file. The session workbook may be hours stale — regrades
        run concurrently — and wb.save() would clobber every row with old data."""
        if not self.modified:
            return
        fresh = openpyxl.load_workbook(self.scores)
        fws = fresh.active
        fidx = {c.value: i for i, c in enumerate(fws[1])}
        frow_of: dict[str, int] = {}
        for r, row in enumerate(fws.iter_rows(min_row=2, values_only=True), start=2):
            sid = row[fidx["student_id"]]
            if sid:
                frow_of[str(sid)] = r
        sid_of_row = {r: str(d["student_id"]) for r, d in self.rows}
        skipped = 0
        for row_idx, row_data in self.modified_rows.items():
            sid = str(row_data.get("student_id") or sid_of_row.get(row_idx, ""))
            fr = frow_of.get(sid)
            if fr is None:
                skipped += 1
                continue
            for name, col0 in fidx.items():
                if name in row_data:
                    fws.cell(row=fr, column=col0 + 1, value=row_data[name])
        fresh.save(self.scores)
        # Keep session state consistent for any subsequent saves
        self.wb, self.ws = fresh, fws
        self.modified_rows.clear()
        self.modified = False
        if skipped:
            print(f"[yellow]Warning: {skipped} edited row(s) not found in current file — skipped.[/yellow]")

def _draft_notes(row_data: dict, current_score: float, total_max: float) -> str:
    """Existing notes if present, else auto-generated deduction summary."""
    existing = str(row_data.get("reviewer_notes") or "").strip()
    if existing:
        return existing
    try:
        import json as _json
        from models import CriterionScore, ScoringResult
        result = ScoringResult(
            student_id=str(row_data.get("student_id", "")),
            student_name=str(row_data.get("student_name", "")),
            assignment_id=str(row_data.get("assignment_id", "")),
            total_score=current_score,
            total_max=total_max,
            confidence=float(row_data.get("confidence") or 0),
            breakdown=[CriterionScore(**b) for b in _json.loads(row_data.get("breakdown_json") or "[]")],
        )
        return result.deduction_summary()
    except Exception:
        return ""


def handle_approve(session: ReviewSession, row_idx: int, row_data: dict, console: Console) -> bool:
    """Handle the 'approve' action. Returns True if approved, False if cancelled."""
    from rich.prompt import Confirm

    total_max = float(row_data.get("total_max", 100) or 100)
    override_score = row_data.get("reviewer_override_score")
    if override_score is not None and override_score != "":
        try:
            current_score = float(override_score)
        except (ValueError, TypeError):
            current_score = float(row_data.get("total_score", 0) or 0)
    else:
        current_score = float(row_data.get("total_score", 0) or 0)
    is_perfect = current_score >= total_max

    # Reviewer notes (sent to the student as richContent) only make sense for
    # non-perfect scores — they explain deductions. Perfect scores get none.
    if not is_perfect:
        draft = _draft_notes(row_data, current_score, total_max)
        edited = prompt_text("[bold cyan]Reviewer notes[/bold cyan] [dim](编辑后回车确认，清空回车则无 notes)[/dim]",
                             default=draft, console=console, prefill=True)
        if not edited:
            if not Confirm.ask("[bold cyan]No reviewer notes — approve anyway?[/bold cyan]", default=False):
                console.print("[yellow]Cancelled — not approved.[/yellow]")
                return False
        row_data["reviewer_notes"] = edited
        session.ws.cell(row=row_idx, column=session.idx["reviewer_notes"] + 1, value=edited)

    session.ws.cell(row=row_idx, column=session.idx["approved"] + 1, value="YES")
    row_data["approved"] = "YES"
    session.update_row(row_idx, row_data)
    console.print("[green]Marked as approved.[/green]")
    return True

def handle_notes(session: ReviewSession, row_idx: int, row_data: dict, console: Console) -> None:
    """Handle the 'notes' action — input line pre-filled with current notes."""
    current_notes = str(row_data.get("reviewer_notes") or "")
    try:
        new_notes = prompt_text("[bold cyan]Reviewer notes[/bold cyan] [dim](预填当前内容，编辑后回车)[/dim]",
                                default=current_notes, console=console,
                                allow_interrupt=True, prefill=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled - notes not changed.[/yellow]")
        return
    row_data["reviewer_notes"] = new_notes
    session.ws.cell(row=row_idx, column=session.idx["reviewer_notes"] + 1, value=new_notes)
    session.update_row(row_idx, row_data)
    console.print("[green]Updated reviewer notes.[/green]")

def handle_edit(session: ReviewSession, row_idx: int, row_data: dict, breakdown: list, console: Console) -> list:
    """Handle the 'edit' action."""
    if not breakdown:
        console.print("[yellow]No breakdown data to edit[/yellow]")
        return breakdown
    breakdown, new_total, new_max = edit_breakdown(console, breakdown)
    # Update the row data
    row_data["breakdown_json"] = json.dumps(breakdown)
    row_data["total_score"] = new_total
    row_data["total_max"] = new_max
    row_data["pct"] = round((new_total / new_max) * 100, 1) if new_max > 0 else 0
    # Update Excel
    session.ws.cell(row=row_idx, column=session.idx["breakdown_json"] + 1, value=row_data["breakdown_json"])
    session.ws.cell(row=row_idx, column=session.idx["total_score"] + 1, value=new_total)
    session.ws.cell(row=row_idx, column=session.idx["total_max"] + 1, value=new_max)
    session.ws.cell(row=row_idx, column=session.idx["pct"] + 1, value=row_data["pct"])
    session.update_row(row_idx, row_data)
    console.print(f"[green]Updated total score: {new_total} / {new_max}[/green]")
    return breakdown

def handle_override(session: ReviewSession, row_idx: int, row_data: dict, console: Console) -> None:
    """Handle the 'override' action. Sets override score and stays on current student."""
    current_override = row_data.get("reviewer_override_score", "")
    if current_override:
        console.print(f"\n[bold]Current override:[/bold] {current_override}")
    new_score = Prompt.ask("[bold cyan]Enter override score[/bold cyan]", default=str(current_override) if current_override else "")
    if new_score:
        try:
            score_val = float(new_score)
            row_data["reviewer_override_score"] = score_val
            session.ws.cell(row=row_idx, column=session.idx["reviewer_override_score"] + 1, value=score_val)
            session.update_row(row_idx, row_data)
            console.print(f"[green]Override score set to {score_val}. Use (a) to approve.[/green]")
        except ValueError:
            console.print("[red]Invalid score[/red]")

def edit_breakdown(console: Console, breakdown: list) -> tuple[list, float, float]:
    """Interactive editor for breakdown. Returns (new_breakdown, new_total, new_max)."""
    while True:
        console.print()
        console.print(Panel("[bold]Edit Criterion Scores[/bold]\n"
                           "Enter the number of the criterion to edit, or 'd' when done",
                           border_style="magenta"))

        # Show current breakdown with numbers
        from rich import box
        bd_table = Table(box=box.HORIZONTALS, show_lines=True, border_style="dim")
        bd_table.add_column("#", style="dim", justify="right")
        bd_table.add_column("Criterion", style="cyan")
        bd_table.add_column("Awarded", justify="right", style="green")
        bd_table.add_column("Max", justify="right")
        for i, item in enumerate(breakdown, start=1):
            awarded = float(item.get("points_awarded", 0))
            max_p = float(item.get("points_max", 0))
            style = "red" if awarded < max_p else "green"
            bd_table.add_row(
                str(i),
                str(item.get("criterion", "")),
                Text(f"{awarded}", style=style),
                f"{max_p}"
            )
        console.print(bd_table)

        # Calculate and show current total
        current_total = sum(float(b.get("points_awarded", 0)) for b in breakdown)
        current_max = sum(float(b.get("points_max", 0)) for b in breakdown)
        console.print(f"\n[bold]Current Total:[/bold] {current_total} / {current_max}")
        console.print()

        choice = Prompt.ask("[bold magenta]Criterion # to edit, or [d]one[/bold magenta]",
                           choices=[str(i) for i in range(1, len(breakdown) + 1)] + ["d", "done"],
                           default="d")

        if choice.lower() in ("d", "done"):
            new_total = sum(float(b.get("points_awarded", 0)) for b in breakdown)
            new_max = sum(float(b.get("points_max", 0)) for b in breakdown)
            return breakdown, new_total, new_max

        # Edit selected criterion
        idx = int(choice) - 1
        item = breakdown[idx]
        console.print(f"\n[bold]Editing:[/bold] {item.get('criterion', '')}")
        console.print(f"  Current: {item.get('points_awarded', 0)} / {item.get('points_max', 0)}")
        console.print(f"  Reasoning: {item.get('reasoning', '')}")

        new_score = Prompt.ask(f"  New score (0-{item.get('points_max', 0)})",
                              default=str(item.get("points_awarded", 0)))
        try:
            score_val = float(new_score)
            max_val = float(item.get("points_max", 0))
            if 0 <= score_val <= max_val:
                item["points_awarded"] = score_val
                console.print(f"[green]Updated to {score_val} / {max_val}[/green]")
            else:
                console.print(f"[red]Score must be between 0 and {max_val}[/red]")
        except ValueError:
            console.print("[red]Invalid number[/red]")

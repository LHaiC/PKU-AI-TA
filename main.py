"""
PKU AI Teaching Assistant CLI

Commands:
  ta assignments --course <id>                  List assignments and gradeBookPK IDs
  ta grade       --course <id> --column <id>    Crawl submissions and score with the LLM
  ta review      [--scores scores.xlsx]         Interactive TUI review (--demo included)
  ta status      [--scores scores.xlsx]         Summarise progress (--json)
  ta show        --student <id>                 Full detail for one student (--json)
  ta approve     --student <id> [--score N]     Record a review decision (--json)
  ta submit      --course <id> --column <id>    Post approved grades (use --dry-run first)
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Optional
from time import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TextColumn
from rich.table import Table
from rich.text import Text

app = typer.Typer(help="PKU AI Teaching Assistant")
console = Console()


def _load_settings(json_output: bool = False):
    """Load .env configuration, turning a missing/invalid file into a clean CLI error."""
    try:
        from config import settings
        return settings
    except Exception as e:
        message = (
            "Could not load configuration from .env. Copy .env.example to .env and "
            "fill in OPENAI_API_KEY, PKU_USERNAME, PKU_PASSWORD (and COURSE_ID)."
        )
        if json_output:
            typer.echo(json.dumps({"error": message}))
        else:
            console.print(f"[red]Error:[/red] {message}")
            console.print(f"[dim]{e}[/dim]")
        raise typer.Exit(1)


@app.command()
def assignments(
    course: Annotated[str, typer.Option(help="Blackboard course ID, e.g. _12345_1")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """List assignments in a course with their gradeBookPK IDs (the value for --column)."""
    settings = _load_settings(json_output)

    course_id = course or settings.course_id
    if not course_id:
        if json_output:
            typer.echo(json.dumps({"error": "--course is required (or set COURSE_ID in .env)"}))
        else:
            console.print("[red]Error:[/red] --course is required (or set COURSE_ID in .env)")
        raise typer.Exit(1)

    from auth.iaaa import get_session
    from crawler.pku_homework import PKUHomeworkCrawler

    if not json_output:
        console.print("[bold]Authenticating with PKU IAAA…[/bold]")
    try:
        client = get_session()
    except RuntimeError as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    crawler = PKUHomeworkCrawler(client, course_id, set())
    columns = crawler.fetch_assignments()

    if json_output:
        typer.echo(json.dumps(
            {
                "course_id": course_id,
                "assignments": [
                    {
                        "title": c.get("name", ""),
                        "gradeBookPK": c.get("gradeBookPK", ""),
                        "id": c.get("id", ""),
                    }
                    for c in columns
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    if not columns:
        console.print("[yellow]No assignments found.[/yellow]")
        return

    table = Table(title=f"Assignments in {course_id}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", style="cyan")
    table.add_column("gradeBookPK", justify="right")
    for i, col in enumerate(columns, start=1):
        table.add_row(str(i), col.get("name", ""), str(col.get("gradeBookPK", "")))
    console.print(table)
    console.print("\nPass the gradeBookPK value to [bold]--column[/bold], e.g. "
                  f"[dim]--column {columns[0].get('gradeBookPK', '')}[/dim]")


@app.command()
def grade(
    course: Annotated[str, typer.Option(help="Blackboard course ID, e.g. _12345_1")] = "",
    column: Annotated[str, typer.Option(help="Gradebook column (assignment) ID")] = "",
    rubric: Annotated[Path, typer.Option(help="Path to rubric file (any format the LLM supports)")] = Path("rubric.md"),
    whitelist: Annotated[str, typer.Option(help="Comma-separated student IDs to include; empty = all")] = "",
    out: Annotated[Path, typer.Option(help="Output Excel path")] = Path("scores.xlsx"),
    save_dir: Annotated[Optional[Path], typer.Option(help="Save submission files here for human review; default: submissions/")] = Path("submissions"),
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show intermediate scores for each student")] = False,
    resume: Annotated[bool, typer.Option("--resume", "-r", help="Resume from previous partial run (if any)")] = False,
    regrade_unapproved: Annotated[bool, typer.Option("--regrade-unapproved", help="Keep approved students, only regrade those not approved")] = False,
    prompt: Annotated[Path, typer.Option(help="System prompt file for the LLM (default: prompts/system_en.md)")] = Path("prompts/system_en.md"),
) -> None:
    """Crawl submissions, score with LLM, export review spreadsheet.

    Press Ctrl-C to interrupt; partial results will be saved to the output file
    and can be resumed with --resume.

    Use --regrade-unapproved to keep already-approved students and only regrade
    those that haven't been approved yet.
    """
    from threading import Lock

    settings = _load_settings()

    from auth.iaaa import get_session
    from crawler.pku_homework import PKUHomeworkCrawler
    from review.spreadsheet import export
    from scorer.llm import score_submission
    from models import ScoringResult

    # Checkpoint save/load using Excel format
    checkpoint_path = out
    all_results: list[ScoringResult] = []
    processed_ids: set[str] = set()
    save_lock = Lock()

    def save_checkpoint() -> None:
        """Save current progress to output Excel file."""
        with save_lock:
            if all_results:
                export(all_results, checkpoint_path)

    def load_checkpoint() -> tuple[list[ScoringResult], set[str]]:
        """Load previous progress from output Excel file (if exists and --resume or --regrade-unapproved is set)."""
        if (resume or regrade_unapproved) and checkpoint_path.exists():
            try:
                from review.spreadsheet import load_reviewed
                records = load_reviewed(checkpoint_path)

                if regrade_unapproved:
                    # Keep only approved students, others will be regraded
                    approved_results = [r.result for r in records if r.approved]
                    all_results_loaded = [r.result for r in records]
                    console.print(f"[bold cyan]Regrade mode:[/bold cyan] Loaded {len(all_results_loaded)} total, keeping {len(approved_results)} already-approved")
                    return approved_results, {r.student_id for r in approved_results}
                else:
                    # Normal resume: keep all previously processed
                    results = [r.result for r in records]
                    console.print(f"[bold cyan]Resuming from checkpoint:[/bold cyan] {len(results)} previously processed result(s)")
                    return results, {r.student_id for r in results}
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load checkpoint: {e}[/yellow]")
        return [], set()

    # Load checkpoint if resuming or regrading unapproved
    unapproved_student_ids: set[str] = set()
    if resume or regrade_unapproved:
        all_results, processed_ids = load_checkpoint()
        if regrade_unapproved and checkpoint_path.exists():
            # For --regrade-unapproved, find students who are NOT approved
            # These are the ones we need to regrade
            try:
                from review.spreadsheet import load_reviewed
                all_records = load_reviewed(checkpoint_path)
                unapproved_student_ids = {r.result.student_id for r in all_records if not r.approved}
                console.print(f"[bold cyan]Regrade mode:[/bold cyan] Found {len(unapproved_student_ids)} unapproved student(s) to regrade")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not determine unapproved students: {e}[/yellow]")
    else:
        all_results = []
        processed_ids = set()

    # Resolve config — CLI args override .env
    course_id = course or settings.course_id
    if not course_id:
        console.print("[red]Error:[/red] --course is required (or set COURSE_ID in .env)")
        raise typer.Exit(1)

    # Determine whitelist:
    # - If --regrade-unapproved: only regrade unapproved students
    # - Else: use CLI whitelist or settings whitelist
    if regrade_unapproved and unapproved_student_ids:
        whitelist_ids: set[str] = unapproved_student_ids
    else:
        whitelist_ids: set[str] = (
            {s.strip() for s in whitelist.split(",") if s.strip()}
            if whitelist
            else settings.whitelist_ids
        )

    if not rubric.exists():
        console.print(f"[red]Error:[/red] Rubric file not found: {rubric}")
        raise typer.Exit(1)

    rubric_text = rubric.read_text(encoding="utf-8")

    console.print("[bold]Step 1/3:[/bold] Authenticating with PKU IAAA…")
    client = get_session()

    crawler = PKUHomeworkCrawler(client, course_id, whitelist_ids)

    if column:
        # column here is expected to be gradeBookPK (numeric), e.g. "423829"
        columns = [{"gradeBookPK": column, "name": column, "id": f"_{column}_1"}]
    else:
        console.print("[bold]Step 1b:[/bold] Fetching assignment list…")
        columns = crawler.fetch_assignments()
        console.print(f"  Found {len(columns)} assignment(s).")

    start_time = time()

    try:
        for col in columns:
            grade_book_pk = col.get("gradeBookPK") or col["id"].strip("_").split("_")[0]
            col_title = col.get("name") or col["id"]
            console.print(f"\n[bold]Step 2/3:[/bold] Fetching submissions for [cyan]{col_title}[/cyan]…")

            submissions = crawler.fetch_submissions(grade_book_pk, col_title)
            if not submissions:
                console.print("  No submissions found.")
                continue

            # Filter out already graded submissions (from PKU website)
            already_graded = [s for s in submissions if s.already_graded]
            if already_graded:
                console.print(f"  [dim]Skipping {len(already_graded)} already-graded submission(s):[/dim]")
                for s in already_graded:
                    console.print(f"    [dim]{s.student_id} {s.student_name}[/dim]")
                submissions = [s for s in submissions if not s.already_graded]
                if not submissions:
                    console.print("  No ungraded submissions left to process.")
                    continue

            # Filter out already processed submissions if resuming
            if processed_ids:
                submissions = [s for s in submissions if s.student_id not in processed_ids]
                if not submissions:
                    console.print("  All submissions already processed.")
                    continue
                console.print(f"  {len(submissions)} submission(s) remaining to process")

            if save_dir:
                _save_submissions(submissions, save_dir, col_title)
                console.print(f"  Saved files → [cyan]{save_dir / col_title}[/cyan]")

            total_submissions = len(submissions)
            console.print(f"  Scoring {total_submissions} submission(s) with LLM (threads={settings.ta_threads}, prompt={prompt.name})…")
            console.print(f"  [dim]Press Ctrl-C to interrupt — progress will be saved[/dim]")

            # Use transient=False for verbose mode so results stay on screen
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TextColumn("[dim]ETA: {task.fields[eta]}"),
                console=console, transient=not verbose,
            ) as progress:
                task = progress.add_task("  Scoring", total=total_submissions, eta="calculating...")
                completed_count = 0

                with ThreadPoolExecutor(max_workers=settings.ta_threads) as executor:
                    futures = {executor.submit(score_submission, sub, rubric_text, prompt): sub for sub in submissions}
                    for future in as_completed(futures):
                        sub = futures[future]
                        try:
                            result = future.result()
                            all_results.append(result)
                            processed_ids.add(result.student_id)
                            completed_count += 1

                            # Calculate ETA
                            if completed_count >= 2:
                                elapsed = time() - start_time
                                avg_time_per = elapsed / completed_count
                                remaining = (total_submissions - completed_count) * avg_time_per
                                if remaining < 60:
                                    eta_str = f"{remaining:.0f}s"
                                elif remaining < 3600:
                                    eta_str = f"{remaining/60:.1f}m"
                                else:
                                    eta_str = f"{remaining/3600:.1f}h"
                                progress.update(task, eta=eta_str)
                            else:
                                progress.update(task, eta="...")

                            # Save checkpoint after each result for safety
                            save_checkpoint()

                            if verbose:
                                # Show verbose output for each student
                                needs_review = result.needs_review
                                color = "yellow" if needs_review else "green"
                                status = "NEEDS_REVIEW" if needs_review else "OK"
                                console.print(
                                    f"  [{color}]{result.student_id:12s}[/] {result.student_name:10s} "
                                    f"→ {result.total_score:3.0f}/{result.total_max:3.0f} ({result.pct:3.0f}%) "
                                    f"[{color}]{status}[/]"
                                )
                        except Exception as e:
                            console.print(f"  [red]Error scoring {sub.student_id}:[/red] {e}")
                        finally:
                            progress.advance(task)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        if all_results:
            console.print(f"[yellow]Saving {len(all_results)} partial result(s)...[/yellow]")
            save_checkpoint()
            console.print(f"[cyan]Checkpoint saved to {checkpoint_path}[/cyan]")
            console.print(f"[cyan]Resume later with: --resume[/cyan]")
        raise typer.Exit(1)

    if not all_results:
        console.print("[yellow]No results to export.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Step 3/3:[/bold] Exporting {len(all_results)} result(s) → [cyan]{out}[/cyan]")
    export(all_results, out)

    needs_review = sum(1 for r in all_results if r.needs_review)
    console.print(
        f"\n[green]Done.[/green] {needs_review}/{len(all_results)} submission(s) flagged for review "
        f"(highlighted in yellow in the spreadsheet)."
    )
    console.print(f"Review the spreadsheet, set [bold]approved[/bold] = YES, then run [bold]ta submit[/bold].")


@app.command()
def submit(
    course: Annotated[str, typer.Option(help="Blackboard course ID")] = "",
    column: Annotated[str, typer.Option(help="Gradebook column (assignment) ID")] = "",
    scores: Annotated[Path, typer.Option(help="Reviewed Excel spreadsheet")] = Path("scores.xlsx"),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print what would be submitted without posting")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """Submit approved scores from the reviewed spreadsheet back to course.pku.edu.cn.

    Run this by hand after a human has approved the records. Use --dry-run first
    to preview, which never posts anything.
    """
    settings = _load_settings(json_output)

    from auth.iaaa import get_session
    from review.spreadsheet import load_reviewed
    from submitter.blackboard import submit_scores

    course_id = course or settings.course_id
    if not course_id or not column:
        message = "Both --course and --column are required."
        if json_output:
            typer.echo(json.dumps({"error": message}))
        else:
            console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(1)
    # BB REST API needs _423829_1 format; accept bare numeric gradeBookPK too
    col_id = column if column.startswith("_") else f"_{column}_1"

    if not scores.exists():
        message = f"Scores file not found: {scores}"
        if json_output:
            typer.echo(json.dumps({"error": message}))
        else:
            console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(1)

    records = load_reviewed(scores)
    approved_count = sum(1 for r in records if r.approved)
    if not json_output:
        console.print(f"Loaded {len(records)} record(s), {approved_count} approved.")

    if approved_count == 0:
        if json_output:
            typer.echo(json.dumps(
                {"course_id": course_id, "column": col_id, "dry_run": dry_run, "results": []},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            console.print("[yellow]Nothing to submit — no records marked approved.[/yellow]")
        raise typer.Exit(0)

    if not json_output:
        console.print("[bold]Authenticating with PKU IAAA…[/bold]")
    client = get_session()

    results = submit_scores(client, course_id, col_id, records, dry_run=dry_run, quiet=json_output)

    if json_output:
        typer.echo(json.dumps(
            {"course_id": course_id, "column": col_id, "dry_run": dry_run, "results": results},
            ensure_ascii=False,
            indent=2,
        ))
    elif dry_run:
        console.print("[dim]Dry run complete — nothing was posted.[/dim]")


@app.command()
def review(
    scores: Annotated[Path, typer.Option(help="Excel spreadsheet to review")] = Path("scores.xlsx"),
    submissions: Annotated[Path, typer.Option(help="Directory with submission files")] = Path("submissions"),
    rubric: Annotated[Path, typer.Option(help="Path to rubric file to open during review")] = Path("rubric.md"),
    needs_review_only: Annotated[bool, typer.Option("--needs-review", "-n", help="Only review students marked needs_review=YES")] = False,
    all_students: Annotated[bool, typer.Option("--all", "-a", help="Review all students (including already approved)")] = False,
    auto_approve: Annotated[bool, typer.Option("--auto-approve", help="Auto-approve 100-point submissions that don't need review")] = False,
    demo: Annotated[bool, typer.Option("--demo", help="Use bundled sample data (no login or API key required)")] = False,
) -> None:
    """Interactive TUI for reviewing submissions one by one.

    Shows score breakdown, opens submission file, and lets you approve or override scores.
    Press 'e' to edit individual criterion scores, 'r' to open the rubric, 'b' to go back.

    Use --auto-approve to automatically approve students with 100/100 and needs_review=NO.
    Use --demo to try the interface with sample data.
    """
    from review.tui import run_review_tui

    if demo:
        from review.demo_data import create_demo_workspace

        scores, submissions, rubric = create_demo_workspace()
        console.print(f"[bold cyan]Demo mode:[/bold cyan] sample data created in {scores.parent}")
        console.print("[dim]Nothing here touches the real course — quit at any time with 'q'.[/dim]")

    try:
        run_review_tui(
            console=console,
            scores=scores,
            submissions=submissions,
            rubric=rubric,
            needs_review_only=needs_review_only,
            all_students=all_students,
            auto_approve=auto_approve,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def status(
    scores: Annotated[Path, typer.Option(help="Excel spreadsheet to inspect")] = Path("scores.xlsx"),
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """Summarise grading progress and list students that still need a decision.

    Non-interactive equivalent of looking at the yellow rows in the spreadsheet.
    """
    from review.spreadsheet import load_reviewed

    if not scores.exists():
        if json_output:
            typer.echo(json.dumps({"error": f"Scores file not found: {scores}"}))
        else:
            console.print(f"[red]Error:[/red] Scores file not found: {scores}")
        raise typer.Exit(1)

    records = load_reviewed(scores)
    pending = [r for r in records if not r.approved]

    def _row(r) -> dict:
        res = r.result
        return {
            "student_id": res.student_id,
            "student_name": res.student_name,
            "assignment_id": res.assignment_id,
            "total_score": res.total_score,
            "total_max": res.total_max,
            "pct": res.pct,
            "confidence": res.confidence,
            "needs_review": res.needs_review,
            "approved": r.approved,
            "reviewer_override_score": r.reviewer_override_score,
            "final_score": r.final_score,
            "reviewer_notes": r.reviewer_notes,
        }

    payload = {
        "scores_file": str(scores),
        "total": len(records),
        "approved": len(records) - len(pending),
        "pending": len(pending),
        "needs_review": sum(1 for r in pending if r.result.needs_review),
        "students": [_row(r) for r in records],
    }

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.print(
        f"[bold]{scores}[/bold]: {payload['total']} record(s), "
        f"[green]{payload['approved']} approved[/green], "
        f"{payload['pending']} pending "
        f"([yellow]{payload['needs_review']} of them flagged for review[/yellow])"
    )
    if not pending:
        console.print("[green]Nothing pending — every record is approved.[/green]")
        return

    table = Table(title="Pending approval")
    table.add_column("Student ID", style="cyan")
    table.add_column("Name")
    table.add_column("Score", justify="right")
    table.add_column("Pct", justify="right")
    table.add_column("Confidence", justify="right")
    table.add_column("Needs review")
    table.add_column("Notes")
    for r in pending:
        res = r.result
        table.add_row(
            res.student_id,
            res.student_name,
            f"{r.final_score:g}/{res.total_max:g}",
            f"{res.pct:g}%",
            f"{res.confidence:.2f}",
            "[yellow]YES[/yellow]" if res.needs_review else "NO",
            "yes" if r.reviewer_notes else "-",
        )
    console.print(table)
    console.print("[dim]Approve with [bold]ta approve --student <id> --notes \"...\"[/bold], "
                  "then submit by hand with [bold]ta submit[/bold].[/dim]")


@app.command()
def show(
    student: Annotated[str, typer.Option("--student", help="Student ID to inspect (single ID)")] = "",
    scores: Annotated[Path, typer.Option(help="Excel spreadsheet to inspect")] = Path("scores.xlsx"),
    submissions: Annotated[Path, typer.Option(help="Directory with saved submission files")] = Path("submissions"),
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """Show the full scoring detail for one student: breakdown, flags, reasoning, files."""
    from review.spreadsheet import load_reviewed
    from review.tui_components import find_submission_file

    if not student:
        console.print("[red]Error:[/red] --student is required.")
        raise typer.Exit(1)
    if not scores.exists():
        console.print(f"[red]Error:[/red] Scores file not found: {scores}")
        raise typer.Exit(1)

    records = [r for r in load_reviewed(scores) if r.result.student_id == student]
    if not records:
        console.print(f"[red]Error:[/red] No record for student {student} in {scores}")
        raise typer.Exit(1)

    def _submission_file(res) -> str:
        path = find_submission_file(submissions, res.student_id, res.student_name)
        return str(path) if path else ""

    if json_output:
        typer.echo(json.dumps(
            {
                "scores_file": str(scores),
                "records": [
                    {
                        **r.result.model_dump(),
                        "approved": r.approved,
                        "reviewer_override_score": r.reviewer_override_score,
                        "final_score": r.final_score,
                        "reviewer_notes": r.reviewer_notes,
                        "submission_file": _submission_file(r.result),
                    }
                    for r in records
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    for r in records:
        res = r.result
        console.print()
        info = Table(show_header=False, box=None)
        info.add_row("[bold]Student:[/]", f"{res.student_id}  {res.student_name}")
        info.add_row("[bold]Assignment:[/]", res.assignment_id)
        score_line = f"{r.final_score:g} / {res.total_max:g} ({res.pct}%)"
        if r.reviewer_override_score is not None:
            score_line += f"  [green](override; LLM gave {res.total_score:g})[/green]"
        info.add_row("[bold]Score:[/]", score_line)
        info.add_row(
            "[bold]Confidence:[/]",
            f"{res.confidence:.2f}   [bold]Needs review:[/] "
            f"{'[yellow]YES[/yellow]' if res.needs_review else 'NO'}   "
            f"[bold]Approved:[/] {'[green]YES[/green]' if r.approved else '[red]NO[/red]'}",
        )
        if r.reviewer_notes:
            info.add_row("[bold]Reviewer notes:[/]", r.reviewer_notes)
        sub_file = _submission_file(res)
        if sub_file:
            info.add_row("[bold]Submission:[/]", f"[blue]{sub_file}[/blue]")
        console.print(Panel(info, title=f"Student {res.student_id}", border_style="cyan"))

        if res.breakdown:
            bd = Table(title="Score Breakdown", show_lines=False)
            bd.add_column("#", style="dim", justify="right")
            bd.add_column("Criterion", style="cyan", max_width=40, overflow="fold")
            bd.add_column("Awarded", justify="right")
            bd.add_column("Max", justify="right")
            bd.add_column("Reasoning", style="dim", max_width=60, overflow="fold")
            for i, item in enumerate(res.breakdown, start=1):
                style = "red" if item.points_awarded < item.points_max else "green"
                bd.add_row(
                    str(i),
                    item.criterion,
                    Text(f"{item.points_awarded:g}", style=style),
                    f"{item.points_max:g}",
                    item.reasoning,
                )
            console.print(bd)

        if res.uncertain_parts:
            uc = Table(title="Uncertain Parts")
            uc.add_column("Description", style="yellow", max_width=60, overflow="fold")
            uc.add_column("Suggested", justify="right")
            for item in res.uncertain_parts:
                uc.add_row(item.description, f"{item.suggested_score:g} / {item.suggested_max:g}")
            console.print(uc)

        if res.llm_reasoning:
            console.print(Panel(Text(res.llm_reasoning, style="dim", overflow="fold"),
                                title="LLM Reasoning", border_style="dim"))


@app.command()
def approve(
    student: Annotated[str, typer.Option("--student", help="Student ID(s), comma-separated")] = "",
    scores: Annotated[Path, typer.Option(help="Excel spreadsheet to update")] = Path("scores.xlsx"),
    score: Annotated[Optional[float], typer.Option("--score", help="Override score (single student only)")] = None,
    notes: Annotated[str, typer.Option("--notes", help="Reviewer notes to store")] = "",
    force: Annotated[bool, typer.Option("--force", help="Allow approving a non-perfect score without notes")] = False,
    revoke: Annotated[bool, typer.Option("--revoke", help="Set approved back to NO instead of YES")] = False,
    auto_perfect: Annotated[bool, typer.Option("--auto-perfect", help="Approve every perfect score that is not flagged for review")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """Record a human review decision in the spreadsheet without using the TUI.

    Approves the given student(s), optionally with an override score and notes.
    Non-perfect scores require --notes (matching the TUI), unless --force is given.
    This command only edits the spreadsheet; it never submits anything.
    """
    from review.tui_components import auto_approve_students, load_review_data

    if not scores.exists():
        console.print(f"[red]Error:[/red] Scores file not found: {scores}")
        raise typer.Exit(1)

    wanted = [s.strip() for s in student.split(",") if s.strip()]
    if not wanted and not auto_perfect:
        console.print("[red]Error:[/red] --student is required (or use --auto-perfect).")
        raise typer.Exit(1)
    if score is not None and len(wanted) != 1:
        console.print("[red]Error:[/red] --score can only be used with exactly one --student.")
        raise typer.Exit(1)

    wb, idx, rows = load_review_data(scores, needs_review_only=False, all_students=True)
    ws = wb.active
    quiet_console = Console(stderr=True) if json_output else console

    modified = False
    auto_ids: list[str] = []
    if auto_perfect:
        for _row_idx, row_data in rows:
            total_score = float(row_data.get("total_score") or 0)
            total_max = float(row_data.get("total_max") or 100)
            if (total_score >= total_max
                    and row_data.get("needs_review") != "YES"
                    and str(row_data.get("approved", "")).upper() != "YES"):
                auto_ids.append(str(row_data.get("student_id")))
        modified = auto_approve_students(ws, idx, quiet_console) or modified

    by_id: dict[str, tuple[int, dict]] = {}
    for row_idx, row_data in rows:
        by_id.setdefault(str(row_data.get("student_id")), (row_idx, row_data))

    changes: list[dict] = []
    if json_output:
        changes.extend(
            {"student_id": sid, "ok": True, "approved": True, "auto": True}
            for sid in auto_ids
        )
    for sid in wanted:
        hit = by_id.get(sid)
        if hit is None:
            changes.append({"student_id": sid, "ok": False, "error": "not found in spreadsheet"})
            continue

        row_idx, row_data = hit

        if revoke:
            ws.cell(row=row_idx, column=idx["approved"] + 1, value="NO")
            changes.append({"student_id": sid, "ok": True, "approved": False})
            modified = True
            continue

        total_max = float(row_data.get("total_max") or 0)
        existing_override = row_data.get("reviewer_override_score")
        existing_notes = str(row_data.get("reviewer_notes") or "").strip()
        if score is not None:
            if not (0 <= score <= total_max):
                changes.append({
                    "student_id": sid,
                    "ok": False,
                    "error": f"score {score:g} is outside 0..{total_max:g}",
                })
                continue
            effective_score = score
        elif existing_override not in (None, ""):
            effective_score = float(existing_override)
        else:
            effective_score = float(row_data.get("total_score") or 0)

        if effective_score < total_max and not existing_notes and not notes.strip() and not force:
            changes.append({
                "student_id": sid,
                "ok": False,
                "error": "non-perfect score requires --notes (or --force)",
            })
            continue

        if score is not None:
            ws.cell(row=row_idx, column=idx["reviewer_override_score"] + 1, value=score)
        if notes.strip():
            ws.cell(row=row_idx, column=idx["reviewer_notes"] + 1, value=notes.strip())
        ws.cell(row=row_idx, column=idx["approved"] + 1, value="YES")
        changes.append({"student_id": sid, "ok": True, "approved": True, "final_score": effective_score})
        modified = True

    if modified:
        wb.save(scores)

    if json_output:
        typer.echo(json.dumps(
            {"scores_file": str(scores), "auto_perfect": auto_perfect, "changes": changes},
            ensure_ascii=False,
            indent=2,
        ))
    else:
        for change in changes:
            if change["ok"]:
                if change["approved"]:
                    console.print(f"[green]✓[/green] {change['student_id']} approved "
                                  f"({change['final_score']:g})")
                else:
                    console.print(f"[yellow]↺[/yellow] {change['student_id']} approval revoked")
            else:
                console.print(f"[red]✗[/red] {change['student_id']}: {change['error']}")
        if modified:
            console.print(f"[green]Saved {scores}[/green]")
        elif auto_perfect:
            console.print("[dim]No perfect scores left to auto-approve.[/dim]")
        else:
            console.print("[yellow]No changes written.[/yellow]")

    if changes and not any(c["ok"] for c in changes):
        raise typer.Exit(1)


def _save_submissions(submissions: list, save_dir: Path, assignment_title: str) -> None:
    """Save each student's attachment file to save_dir/assignment_title/ for human review."""
    import re
    safe_title = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', assignment_title)
    dest = save_dir / safe_title
    dest.mkdir(parents=True, exist_ok=True)
    for sub in submissions:
        for att in sub.attachments:
            ext = Path(att.filename).suffix or ""
            # Filename: studentId_studentName.ext  (e.g. 2300012345_张三.pdf)
            safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', sub.student_name)
            filename = f"{sub.student_id}_{safe_name}{ext}"
            (dest / filename).write_bytes(att.data)


if __name__ == "__main__":
    app()

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
def engines() -> None:
    """List scoring engines and which agentic CLIs are installed on this machine."""
    settings = _load_settings()
    from scorer.cli_scorer import cli_engines_on_path

    table = Table(title="Scoring engines")
    table.add_column("Engine", style="cyan")
    table.add_column("Status")
    table.add_column("Notes", style="dim")

    configured = (settings.ta_cli_engine or "api").lower()
    api_ok = bool(settings.openai_api_key)
    table.add_row(
        "api" + (" ←configured" if configured == "api" else ""),
        "[green]ready[/green]" if api_ok else "[yellow]missing OPENAI_API_KEY[/yellow]",
        f"OpenAI-compatible endpoint ({settings.openai_base_url})",
    )
    opts = ", ".join(
        f"{k}={v}" for k, v in
        (("model", settings.ta_cli_model), ("effort", settings.ta_cli_effort))
        if v
    ) or "defaults"
    for name, path in cli_engines_on_path().items():
        table.add_row(
            name + (" ←configured" if configured == name else ""),
            f"[green]{path}[/green]" if path else "[red]not on PATH[/red]",
            f"agentic CLI ({opts})",
        )
    if settings.grader_cmd:
        table.add_row("TA_GRADER_CMD", "[green]custom[/green]", settings.grader_cmd)
    console.print(table)
    console.print("[dim]Select with --engine or TA_CLI_ENGINE in .env; "
                  "TA_CLI_MODEL/TA_CLI_EFFORT tune it; TA_GRADER_CMD overrides "
                  "everything.[/dim]")


@app.command()
def grade(
    course: Annotated[str, typer.Option(help="Blackboard course ID, e.g. _12345_1")] = "",
    column: Annotated[str, typer.Option(help="Gradebook column (assignment) ID")] = "",
    rubric: Annotated[Optional[Path], typer.Option(help="Path to rubric file; default: <out dir>/rubric.md")] = None,
    whitelist: Annotated[str, typer.Option(help="Comma-separated student IDs to include; empty = all")] = "",
    out: Annotated[Path, typer.Option(help="Output Excel path; its directory is the assignment workdir")] = Path("scores.xlsx"),
    save_dir: Annotated[Optional[Path], typer.Option(help="Save submission files here; default: <out dir>/submissions/")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show intermediate scores for each student")] = False,
    resume: Annotated[bool, typer.Option("--resume", "-r", help="Resume from previous partial run (if any)")] = False,
    regrade_unapproved: Annotated[bool, typer.Option("--regrade-unapproved", help="Keep approved students, only regrade those not approved")] = False,
    prompt: Annotated[Optional[Path], typer.Option(help="System prompt file; default: <out dir>/prompt_zh.md or prompt.md if present, else prompts/system_en.md")] = None,
    engine: Annotated[str, typer.Option(help="Scoring engine: api, devin, claude, codex, opencode, cmdc. Env: TA_CLI_ENGINE")] = "",
) -> None:
    """Crawl submissions, score with LLM, export review spreadsheet.

    Press Ctrl-C to interrupt; partial results will be saved to the output file
    and can be resumed with --resume.

    Use --regrade-unapproved to keep already-approved students and only regrade
    those that haven't been approved yet.
    """
    from threading import Lock

    settings = _load_settings()

    # Workdir convention: everything lives beside the --out spreadsheet.
    if rubric is None:
        rubric = out.parent / "rubric.md"
    if save_dir is None:
        save_dir = out.parent / "submissions"
    if prompt is None:
        prompt = next(
            (p for p in (out.parent / "prompt_zh.md", out.parent / "prompt.md") if p.exists()),
            Path("prompts/system_en.md"),
        )

    from auth.iaaa import get_session
    from crawler.pku_homework import PKUHomeworkCrawler
    from review.spreadsheet import export
    from models import ScoringResult

    engine_name = (engine or settings.ta_cli_engine or "api").lower()
    if engine_name == "api":
        if not settings.openai_api_key:
            console.print(
                "[red]Error:[/red] --engine api requires OPENAI_API_KEY in .env. "
                "Use --engine devin/claude/codex/opencode/cmdc to grade with an agentic CLI instead."
            )
            raise typer.Exit(1)
        from scorer.llm import score_submission
    elif not save_dir:
        console.print("[red]Error:[/red] CLI engines need --save-dir to read submission files from disk.")
        raise typer.Exit(1)
    elif not settings.grader_cmd:
        # Preset CLI engine: fail early if it's unknown or not installed.
        from scorer.cli_scorer import _ENGINES, cli_engines_on_path
        if engine_name not in _ENGINES:
            console.print(f"[red]Error:[/red] unknown engine {engine_name!r} "
                          f"(api, {', '.join(_ENGINES)}; or set TA_GRADER_CMD)")
            raise typer.Exit(1)
        if not cli_engines_on_path().get(engine_name):
            console.print(f"[red]Error:[/red] '{engine_name}' CLI not found on PATH — "
                          "run `ta engines` to see what's installed")
            raise typer.Exit(1)

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
        """Load previous progress from output Excel file for --resume."""
        if resume and checkpoint_path.exists():
            try:
                from review.spreadsheet import load_reviewed
                records = load_reviewed(checkpoint_path)
                results = [r.result for r in records]
                console.print(f"[bold cyan]Resuming from checkpoint:[/bold cyan] {len(results)} previously processed result(s)")
                return results, {r.student_id for r in results}
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load checkpoint: {e}[/yellow]")
        return [], set()

    cli_whitelist: set[str] = (
        {s.strip() for s in whitelist.split(",") if s.strip()} if whitelist else set()
    )

    if regrade_unapproved and checkpoint_path.exists():
        # Regrade = redo a target set while KEEPING every other record.
        # Targets: all unapproved records, narrowed by --whitelist if given.
        try:
            from review.spreadsheet import load_reviewed
            all_records = load_reviewed(checkpoint_path)
            approved_ids = {r.result.student_id for r in all_records if r.approved}
            unapproved_ids = {r.result.student_id for r in all_records if not r.approved}
            # An explicit --whitelist names exactly who to regrade — approved
            # or not. Without one, default to all unapproved records.
            targets = cli_whitelist if cli_whitelist else (unapproved_ids - approved_ids)
            all_results = [r.result for r in all_records if r.result.student_id not in targets]
            processed_ids = {r.student_id for r in all_results}
            whitelist_ids = targets
            console.print(f"[bold cyan]Regrade mode:[/bold cyan] keeping {len(all_results)} result(s), regrading {len(targets)} student(s)")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not determine unapproved students: {e}[/yellow]")
            whitelist_ids = cli_whitelist or settings.whitelist_ids
    else:
        if resume:
            all_results, processed_ids = load_checkpoint()
        else:
            all_results, processed_ids = [], set()
        whitelist_ids = cli_whitelist or settings.whitelist_ids

    # Resolve config — CLI args override .env
    course_id = course or settings.course_id
    if not course_id:
        console.print("[red]Error:[/red] --course is required (or set COURSE_ID in .env)")
        raise typer.Exit(1)

    if not rubric.exists():
        console.print(f"[red]Error:[/red] Rubric file not found: {rubric}")
        raise typer.Exit(1)

    rubric_text = rubric.read_text(encoding="utf-8")

    console.print("[bold]Step 1/3:[/bold] Authenticating with PKU IAAA…")
    client = get_session()

    crawler = PKUHomeworkCrawler(client, course_id, whitelist_ids)

    console.print("[bold]Step 1b:[/bold] Fetching assignment list…")
    columns = crawler.fetch_assignments()
    if column:
        # --column is a gradeBookPK (numeric); resolve its real title —
        # getStudentWork.do needs the actual assignment name, not the PK.
        columns = [c for c in columns if str(c.get("gradeBookPK")) == str(column)]
        if not columns:
            console.print(f"[red]Error:[/red] gradeBookPK {column} not found in course {course_id}")
            raise typer.Exit(1)
    console.print(f"  Found {len(columns)} assignment(s) to process.")

    start_time = time()

    try:
        for col in columns:
            grade_book_pk = col.get("gradeBookPK") or col["id"].strip("_").split("_")[0]
            col_title = col.get("name") or col["id"]
            console.print(f"\n[bold]Step 2/3:[/bold] Fetching submissions for [cyan]{col_title}[/cyan]…")

            # Pin the assignment's coordinates + deadline beside the scores
            # file so decay/submit don't need them re-passed.
            from workdir import save_meta
            due_dt = crawler.fetch_due_date(grade_book_pk)
            save_meta(out.parent, course_id=course_id, column=grade_book_pk,
                      title=col_title,
                      due=due_dt.strftime("%Y-%m-%d %H:%M:%S") if due_dt else "")
            if due_dt:
                console.print(f"  Deadline: [cyan]{due_dt:%Y-%m-%d %H:%M}[/cyan] (cached to meta.json)")

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

            files_map: dict[str, list[Path]] = {}
            if save_dir:
                files_map = _save_submissions(submissions, save_dir, col_title)
                console.print(f"  Saved files → [cyan]{save_dir / col_title}[/cyan]")

            if engine_name == "api":
                def score_fn(sub):
                    return score_submission(sub, rubric_text, prompt)
            else:
                from scorer.cli_scorer import make_cli_scorer
                score_fn = make_cli_scorer(
                    engine_name, rubric_text, prompt, files_map,
                    prompts_dir=save_dir / "_grader_prompts",
                )

            total_submissions = len(submissions)
            console.print(f"  Scoring {total_submissions} submission(s) via {engine_name} (threads={settings.ta_threads}, prompt={prompt.name})…")
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
                    futures = {executor.submit(score_fn, sub): sub for sub in submissions}
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
    student: Annotated[str, typer.Option(help="Comma-separated student IDs to submit; empty = all approved")] = "",
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

    from workdir import load_meta
    meta = load_meta(scores.parent)
    course_id = course or meta.get("course_id") or settings.course_id
    column = column or str(meta.get("column") or "")
    if not course_id or not column:
        message = "Both --course and --column are required (or run grade first to write meta.json)."
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
    if student.strip():
        wanted = {s.strip() for s in student.split(",") if s.strip()}
        records = [r for r in records if r.result.student_id in wanted]
        missing = wanted - {r.result.student_id for r in records}
        if missing and not json_output:
            console.print(f"[yellow]Warning: not found in {scores.name}: "
                          f"{', '.join(sorted(missing))}[/yellow]")
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
    scores: Annotated[Path, typer.Option(help="Excel spreadsheet to review; its directory is the workdir")] = Path("scores.xlsx"),
    submissions: Annotated[Optional[Path], typer.Option(help="Directory with submission files; default: <scores dir>/submissions")] = None,
    rubric: Annotated[Optional[Path], typer.Option(help="Rubric file to open during review; default: <scores dir>/rubric.md")] = None,
    needs_review_only: Annotated[bool, typer.Option("--needs-review", "-n", help="Only review flagged/uncertain/non-perfect students")] = False,
    all_students: Annotated[bool, typer.Option("--all", "-a", help="Review all students (including already approved)")] = False,
    below: Annotated[float | None, typer.Option("--below", help="Hard cap: only review scores below this percent")] = None,
    demo: Annotated[bool, typer.Option("--demo", help="Use bundled sample data (no login or API key required)")] = False,
) -> None:
    """Interactive TUI for reviewing submissions one by one.

    Shows score breakdown, opens submission file, and lets you approve or override scores.
    Press 'e' to edit individual criterion scores, 'r' to open the rubric, 'b' to go back.

    Use `approve --auto-perfect` to batch-approve clean 100/100 submissions.
    Use --demo to try the interface with sample data.
    """
    from review.tui import run_review_tui

    if demo:
        from review.demo_data import create_demo_workspace

        scores, submissions, rubric = create_demo_workspace()
        console.print(f"[bold cyan]Demo mode:[/bold cyan] sample data created in {scores.parent}")
        console.print("[dim]Nothing here touches the real course — quit at any time with 'q'.[/dim]")
    else:
        # Workdir convention: related files live beside the scores spreadsheet.
        if submissions is None:
            submissions = scores.parent / "submissions"
        if rubric is None:
            rubric = scores.parent / "rubric.md"

    try:
        run_review_tui(
            console=console,
            scores=scores,
            submissions=submissions,
            rubric=rubric,
            needs_review_only=needs_review_only,
            all_students=all_students,
            below=below,
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
    submissions: Annotated[Optional[Path], typer.Option(help="Directory with saved submission files; default: <scores dir>/submissions")] = None,
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
    if submissions is None:
        submissions = scores.parent / "submissions"

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

        auto_notes = ""
        if effective_score < total_max and not existing_notes and not notes.strip() and not force:
            # Auto-fill notes from the scoring breakdown — they are posted to
            # the platform as student-facing feedback (richContent).
            try:
                import json as _json
                from models import CriterionScore, ScoringResult
                res = ScoringResult(
                    student_id=sid,
                    student_name=str(row_data.get("student_name", "")),
                    assignment_id=str(row_data.get("assignment_id", "")),
                    total_score=effective_score,
                    total_max=total_max,
                    confidence=float(row_data.get("confidence") or 0),
                    breakdown=[CriterionScore(**b) for b in _json.loads(row_data.get("breakdown_json") or "[]")],
                )
                auto_notes = res.deduction_summary()
            except Exception:
                auto_notes = ""
            if not auto_notes:
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
        elif auto_notes:
            ws.cell(row=row_idx, column=idx["reviewer_notes"] + 1, value=auto_notes)
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


@app.command()
def decay(
    scores: Annotated[Path, typer.Option(help="Reviewed Excel spreadsheet; decay columns are written into it in place")] = Path("scores.xlsx"),
    course: Annotated[str, typer.Option(help="Blackboard course ID; default: meta.json")] = "",
    column: Annotated[str, typer.Option(help="Gradebook column (gradeBookPK); default: meta.json")] = "",
    due: Annotated[str, typer.Option(help="Deadline override, e.g. '2026-09-16 23:59'; default: meta.json, else auto-fetched")] = "",
    rule: Annotated[Optional[Path], typer.Option(help="Late-penalty rule file; default: <scores dir>/.ddl_rule, else ./.ddl_rule")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON only")] = False,
) -> None:
    """Apply the late-submission decay rule, annotating the scores file in place.

    Fetches each student's submission timestamp from Blackboard, applies the
    scaling tiers from the rule file, and writes submitted_at / hours_late /
    decay_factor / decay_reason columns. Downstream, final_score =
    (reviewer_override or LLM score) × decay_factor, so `ta submit` reads the
    same file — there is no separate "final" spreadsheet to keep in sync.

    Idempotent: re-running recomputes factors and refreshes the 迟交扣分 note
    rather than stacking duplicates. Rows with factor < 1 are listed for
    manual verification.
    """
    settings = _load_settings(json_output)
    from workdir import load_meta, save_meta

    meta = load_meta(scores.parent)

    if rule is None:
        rule = scores.parent / ".ddl_rule"
        if not rule.exists():
            rule = Path(".ddl_rule")
    if not rule.exists():
        console.print(f"[red]Error:[/red] Rule file not found: {rule} "
                      "(copy .ddl_rule.example and edit it)")
        raise typer.Exit(1)
    if not scores.exists():
        console.print(f"[red]Error:[/red] Scores file not found: {scores}")
        raise typer.Exit(1)

    from datetime import datetime
    from review.decay import parse_rule, parse_time, compute_decay
    try:
        buckets = parse_rule(rule)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    course_id = course or meta.get("course_id") or settings.course_id
    column = column or str(meta.get("column") or "")
    if not course_id or not column:
        console.print("[red]Error:[/red] --course and --column are required "
                      "(or run `grade` first to write meta.json)")
        raise typer.Exit(1)

    from auth.iaaa import get_session
    from crawler.pku_homework import PKUHomeworkCrawler, HW_BASE, _parse_student_list
    from review.spreadsheet import load_reviewed

    console.print("[bold]Fetching submission times…[/bold]")
    client = get_session()
    crawler = PKUHomeworkCrawler(client, course_id, set())
    columns = crawler.fetch_assignments()
    col = next((c for c in columns if str(c.get("gradeBookPK")) == str(column)), None)
    if col is None:
        console.print(f"[red]Error:[/red] gradeBookPK {column} not found")
        raise typer.Exit(1)
    save_meta(scores.parent, course_id=course_id, column=str(column), title=col["name"])

    # Due date: --due flag > meta.json > legacy due.txt > gradebook REST API.
    # Resolved values are cached to meta.json so the deadline stays pinned
    # even if the column is edited later.
    due_dt = parse_time(due) if due else None
    due_source = "--due" if due_dt else ""
    if due_dt is None and meta.get("due"):
        due_dt = parse_time(str(meta["due"]))
        due_source = "meta.json"
    if due_dt is None:
        legacy_due = scores.parent / "due.txt"
        if legacy_due.exists():
            due_dt = parse_time(legacy_due.read_text().strip())
            due_source = str(legacy_due)
    if due_dt is None:
        due_dt = crawler.fetch_due_date(str(column))
        due_source = "gradebook REST API"
        if due_dt is None:
            console.print("[red]Error:[/red] could not determine the deadline — "
                          "pass --due 'YYYY-MM-DD HH:MM'")
            raise typer.Exit(1)
    save_meta(scores.parent, due=due_dt.strftime("%Y-%m-%d %H:%M:%S"))
    console.print(f"[bold]Deadline:[/bold] {due_dt:%Y-%m-%d %H:%M} ({due_source})")

    text = client.get(f"{HW_BASE}/getStudentWork.do", params={
        "course_id": course_id, "gradeBookPK": str(column),
        "title": col["name"], "showAll": "true",
    }).text
    students = _parse_student_list(text)
    submit_times = {
        s["userId"]: {
            "newest": s.get("submitted_at", ""),
            "all": [a.get("submitted_at", "") for a in s.get("attempts", [])],
        }
        for s in students
    }

    records = load_reviewed(scores)
    rows = compute_decay(records, submit_times, due_dt, buckets)

    # Annotate scores.xlsx in place. decay_factor drives final_score downstream;
    # reviewer_override_score is left untouched (it stays a pure human field).
    # Decay notes are stripped then re-appended so re-runs stay idempotent.
    import openpyxl
    wb = openpyxl.load_workbook(scores)
    ws = wb.active
    idx = {c.value: i for i, c in enumerate(ws[1])}
    from copy import copy as _copy
    for name in ("submitted_at", "hours_late", "decay_factor", "decay_reason"):
        if name not in idx:
            c = ws.cell(row=1, column=ws.max_column + 1, value=name)
            c.font = _copy(ws.cell(1, 1).font)
            idx[name] = c.column - 1
    by_sid = {d.student_id: d for d in rows}
    for r in range(2, ws.max_row + 1):
        sid = str(ws.cell(r, idx["student_id"] + 1).value or "")
        d = by_sid.get(sid)
        if d is None:
            continue
        notes = str(ws.cell(r, idx["reviewer_notes"] + 1).value or "")
        notes = "\n".join(
            l for l in notes.splitlines() if not l.startswith("迟交扣分：")
        ).strip()
        if d.factor < 1.0:
            note = f"迟交扣分：{d.raw_score:g}×{d.factor:g}={d.final_score:g}（{d.reason}）"
            notes = f"{notes}\n{note}".strip() if notes else note
        ws.cell(r, idx["reviewer_notes"] + 1, notes)
        for name, val in (("submitted_at", d.submitted_at),
                          ("hours_late", round(d.hours_late, 1)),
                          ("decay_factor", d.factor),
                          ("decay_reason", d.reason)):
            ws.cell(r, idx[name] + 1, val)

    wb.save(scores)

    late = [d for d in rows if d.factor < 1.0]
    rejected = [d for d in late if d.factor == 0]
    table = Table(title=f"Late submissions ({len(late)} of {len(rows)})", show_lines=True)
    for col_name in ("ID", "Name", "Submitted", "Hours late", "Factor", "Raw", "Final"):
        table.add_column(col_name)
    for d in late:
        style = "red" if d.factor == 0 else "yellow"
        table.add_row(d.student_id, d.student_name, d.submitted_at,
                      f"{d.hours_late:.1f}", f"{d.factor:g}",
                      f"{d.raw_score:g}", f"[{style}]{d.final_score:g}[/]")
    console.print(table)
    if rejected:
        console.print(f"[red]{len(rejected)} student(s) submitted >72h late — rule says not accepted (factor 0).[/red]")
    console.print(f"[green]Updated {scores}[/green] — verify the list above, then submit with:")
    console.print(f"  uv run python main.py submit --scores {scores} --dry-run")

    if json_output:
        typer.echo(json.dumps(
            {"scores": str(scores), "due": due_dt.strftime("%Y-%m-%d %H:%M:%S"),
             "late": [d.__dict__ for d in late]},
            ensure_ascii=False, indent=2, default=str,
        ))


def _save_submissions(submissions: list, save_dir: Path, assignment_title: str) -> dict[str, list[Path]]:
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


if __name__ == "__main__":
    app()

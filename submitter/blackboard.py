"""
Submit grades back to the PKU homework plugin (bb-homeWorkCheck-BBLEARN).

Endpoint discovered by inspecting the CheckWork.do page JS sendData() function:
  POST saveStudentGrade.do
  Body (form-encoded):
    inputData   — numeric score
    attemptPk   — per-student attempt identifier
    gradeBookPk — assignment identifier (note lowercase 'k')
    course_id   — course identifier
    richContent — reviewer notes
    gradePk     — per-student grade record ID (hardcoded in sendData() JS)

gradePk is injected server-side into the page JS for each student, so we must
fetch CheckWork.do per student to extract it.
"""
from __future__ import annotations

import re

import httpx
from rich.console import Console

from models import ReviewRecord

HW_BASE = "/webapps/bb-homeWorkCheck-BBLEARN/homeWorkCheck"
SUBMIT_ENDPOINT = f"{HW_BASE}/saveStudentGrade.do"

console = Console()

_GRADE_PK_RE = re.compile(r"gradePk=[^)]*encodeURIComponent\((\d+)\)")


def _fetch_assignment_title(client: httpx.Client, course_id: str, grade_book_pk: str) -> str:
    """Look up the assignment title for a gradeBookPK via getHomeWorkList.do."""
    from crawler.pku_homework import _parse_homework_list

    resp = client.get(
        f"{HW_BASE}/getHomeWorkList.do",
        params={"course_id": course_id},
    )
    resp.raise_for_status()
    assignments = _parse_homework_list(resp.text)
    for a in assignments:
        if a.get("gradeBookPK") == grade_book_pk:
            return a.get("name", "")
    return ""


def _fetch_student_meta(client: httpx.Client, course_id: str, grade_book_pk: str) -> tuple[dict[str, dict], str]:
    """
    Return ({userId: {filePk, attemptPk}}, assignment_title) by parsing getStudentWork.do.
    """
    from crawler.pku_homework import _STUDENT_ONCLICK_PATTERN, _STUDENT_PATTERN

    title = _fetch_assignment_title(client, course_id, grade_book_pk)

    resp = client.get(
        f"{HW_BASE}/getStudentWork.do",
        params={"course_id": course_id, "gradeBookPK": grade_book_pk, "title": title, "showAll": "true"},
    )
    resp.raise_for_status()
    html = resp.text

    meta: dict[str, dict] = {}
    for m in _STUDENT_PATTERN.finditer(html):
        _, user_id, file_pk, _, attempt_pk, _ = m.groups()
        meta[user_id] = {"filePk": file_pk, "attemptPk": attempt_pk}
    for m in _STUDENT_ONCLICK_PATTERN.finditer(html):
        user_id, file_pk, attempt_pk, _ = m.groups()
        meta.setdefault(user_id, {"filePk": file_pk, "attemptPk": attempt_pk})

    return meta, title


def _fetch_grade_pk(
    client: httpx.Client,
    course_id: str,
    grade_book_pk: str,
    user_id: str,
    file_pk: str,
    attempt_pk: str,
    title: str,
) -> str | None:
    """
    Fetch CheckWork.do for one student and extract gradePk from sendData() JS.
    gradePk is a per-student grade record ID injected server-side.
    """
    resp = client.get(
        f"{HW_BASE}/CheckWork.do",
        params={
            "course_id": course_id,
            "gradeBookPK": grade_book_pk,
            "userId": user_id,
            "filePk": file_pk,
            "title": title,
            "attemptPk": attempt_pk,
        },
    )
    resp.raise_for_status()
    html = resp.text

    m = _GRADE_PK_RE.search(html)
    return m.group(1) if m else None


def submit_scores(
    client: httpx.Client,
    course_id: str,
    column_id: str,
    records: list[ReviewRecord],
    *,
    dry_run: bool = False,
    quiet: bool = False,
) -> list[dict]:
    """
    Submit approved grades via saveStudentGrade.do.

    column_id may be "_423829_1" (BB REST format) or bare "423829" (gradeBookPK).
    Returns one result dict per approved record; set quiet=True to suppress
    human-readable output (used by `ta submit --json`).
    """
    def log(*args, **kwargs) -> None:
        if not quiet:
            console.print(*args, **kwargs)

    grade_book_pk = column_id.strip("_").split("_")[0]

    approved = [r for r in records if r.approved]
    skipped = len(records) - len(approved)
    if skipped:
        log(f"[yellow]Skipping {skipped} unapproved record(s).[/yellow]")
    if not approved:
        return []

    if dry_run:
        results: list[dict] = []
        for r in approved:
            log(
                f"[dim][DRY RUN][/dim] Would submit: "
                f"{r.result.student_id} ({r.result.student_name})"
                f" → {r.final_score:g}/{r.result.total_max:g}"
                f"  notes: {(r.reviewer_notes or '')[:60]}"
            )
            results.append({
                "student_id": r.result.student_id,
                "student_name": r.result.student_name,
                "score": r.final_score,
                "total_max": r.result.total_max,
                "dry_run": True,
                "ok": True,
                "error": "",
            })
        return results

    # ── Step 1: fetch filePk / attemptPk for all students ────────────────
    log("  Fetching submission metadata…")
    try:
        student_meta, assignment_title = _fetch_student_meta(client, course_id, grade_book_pk)
    except httpx.HTTPStatusError as e:
        log(f"[red]Error fetching student list:[/red] {e}")
        return [{
            "student_id": r.result.student_id,
            "student_name": r.result.student_name,
            "score": r.final_score,
            "total_max": r.result.total_max,
            "dry_run": False,
            "ok": False,
            "error": f"failed to fetch student list: {e}",
        } for r in approved]
    log(f"  Found metadata for {len(student_meta)} student(s). Assignment: [cyan]{assignment_title}[/cyan]")

    # ── Step 2: for each approved student, fetch gradePk and submit ──────
    results = []
    for r in approved:
        uid = r.result.student_id
        score = r.final_score
        notes = (r.reviewer_notes or "").strip()

        def _fail(error: str) -> None:
            results.append({
                "student_id": uid,
                "student_name": r.result.student_name,
                "score": score,
                "total_max": r.result.total_max,
                "dry_run": False,
                "ok": False,
                "error": error,
            })

        if uid not in student_meta:
            log(f"[yellow]⚠[/yellow]  {uid} ({r.result.student_name}): no submission metadata — skipping")
            _fail("no submission metadata")
            continue

        meta = student_meta[uid]

        # Fetch CheckWork.do to get gradePk
        try:
            grade_pk = _fetch_grade_pk(
                client, course_id, grade_book_pk,
                uid, meta["filePk"], meta["attemptPk"], assignment_title,
            )
        except httpx.HTTPStatusError as e:
            log(f"[red]✗[/red]  {uid} ({r.result.student_name}): "
                f"failed to load CheckWork.do — {e}")
            _fail(f"failed to load CheckWork.do: {e}")
            continue

        if grade_pk is None:
            log(f"[yellow]⚠[/yellow]  {uid} ({r.result.student_name}): "
                "gradePk not found in page JS — skipping")
            _fail("gradePk not found in page JS")
            continue

        score_str = str(int(score)) if score == int(score) else str(score)
        payload = {
            "inputData": score_str,
            "attemptPk": meta["attemptPk"],
            "gradeBookPk": grade_book_pk,
            "course_id": course_id,
            "richContent": notes[:2000],
            "gradePk": grade_pk,
        }

        try:
            resp = client.post(SUBMIT_ENDPOINT, data=payload)
            resp.raise_for_status()
            log(
                f"[green]✓[/green]  {uid} ({r.result.student_name})"
                f" → {score_str}/{r.result.total_max:g}"
            )
            results.append({
                "student_id": uid,
                "student_name": r.result.student_name,
                "score": score,
                "total_max": r.result.total_max,
                "dry_run": False,
                "ok": True,
                "error": "",
            })
        except httpx.HTTPStatusError as e:
            log(
                f"[red]✗[/red]  {uid} ({r.result.student_name}): "
                f"HTTP {e.response.status_code} — {e.response.text[:300]}"
            )
            _fail(f"HTTP {e.response.status_code}: {e.response.text[:300]}")

    ok = sum(1 for item in results if item["ok"])
    result_color = "green" if ok == len(approved) else "yellow"
    log(
        f"\n[{result_color}]Done.[/{result_color}] {ok}/{len(approved)} grade(s) submitted."
    )
    return results

"""
Crawler for PKU's custom homework system: bb-homeWorkCheck-BBLEARN.

Endpoints (all under /webapps/bb-homeWorkCheck-BBLEARN/homeWorkCheck/):

  getHomeWorkList.do?course_id=...
      → HTML page listing all assignments with gradeBookPK (numeric) and title

  getStudentWork.do?course_id=...&gradeBookPK=...&title=...&showAll=true
      → HTML page listing every submitted student:
          userId (real student number), name, filePk, attemptPk
        Links use CheckWork.do per student.

  CheckWork.do?course_id=...&gradeBookPK=...&userId=...&filePk=...&title=...&attemptPk=...
      → HTML page for one student's submission.
        JS embeds: var filePath = '/usr/local/blackboard/content/storage/pdf/{courseId}/{gradeBookPK}/{filePk}/{filename}'

  api/pdf.do?path={double_url_encoded_filePath}
      → Serves the PDF directly (application/pdf).
        NOTE: pass the double-encoded path directly in the URL string —
        do NOT use httpx params= which would triple-encode it.

  downloadBatch.do?course_id=...&gradeBookPK=...&isGroup=false
      → ZIP of all submitted files in the same order as getStudentWork.do.
        Used as a fast alternative to per-student fetching when no whitelist.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from models import Attachment, Submission

HW_BASE = "/webapps/bb-homeWorkCheck-BBLEARN/homeWorkCheck"

# getStudentWork.do has two submission link formats:
#   1. href="...CheckAloneWork.do?...userId=X&filePk=Y&...&attemptPk=Z">查看</a> (already graded)
#   2. onclick="checkWork('userId','filePk','attemptPk')">批改</a> (needs grading)
_STUDENT_PATTERN = re.compile(
    r'<a[^>]*CheckAloneWork\.do\?course_id=[^&]+&gradeBookPK=(\d+)'
    r'&userId=(\d+)&filePk=(\d+)&title=([^&"]+)&attemptPk=(\d+)[^>]*>([^<]+)</a>'
)
# Newer Blackboard versions pass a 4th arg: checkWork('userId','filePk','attemptPk','groupPk')
_STUDENT_ONCLICK_PATTERN = re.compile(
    r"""onclick=['"]\s*checkWork\(\s*['"](\d+)['"]\s*,\s*['"](\d+)['"]\s*,\s*['"](\d+)['"](?:\s*,\s*['"](\d*)['"])?\s*\)[^>]*>([^<]+)</a>"""
)
_NAME_PATTERN = re.compile(
    r'scope="row"[^>]*>\s*(\d{10})\s*</th>.*?table-data-cell-value">(.*?)</span>',
    re.DOTALL,
)
_ROW_PATTERN = re.compile(r'<tr id="listContainer_row:\d+".*?</tr>', re.DOTALL)
_CELL_VALUE_PATTERN = re.compile(
    r'mobile-table-label">\s*(.*?)\s*:\s*</span>\s*'
    r'<span class="table-data-cell-value">(.*?)</span>',
    re.DOTALL,
)
_FILE_PATH_PATTERN = re.compile(r"filePath\s*=\s*'([^']+)'")
# One attempt can carry several files; each is an attemptFile link that opens
# the same CheckWork page with a different filePk (switching filePath).
_ATTEMPT_FILE_PATTERN = re.compile(
    r'id="currentAttempt_attemptFile_[^"]+"[^>]*href="[^"]*filePk=(\d+)[^"]*attemptPk=(\d+)'
)


class PKUHomeworkCrawler:
    def __init__(self, client: httpx.Client, course_id: str, whitelist: set[str]):
        self.client = client
        self.course_id = course_id
        self.whitelist = whitelist

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_due_date(self, grade_book_pk: str) -> "datetime | None":
        """Read the gradebook column's due date via the Blackboard REST API.

        Returns a naive datetime in Asia/Shanghai (site time), or None.
        """
        from datetime import datetime, timezone, timedelta
        col_id = grade_book_pk if grade_book_pk.startswith("_") else f"_{grade_book_pk}_1"
        try:
            resp = self.client.get(
                f"/learn/api/public/v1/courses/{self.course_id}/gradebook/columns/{col_id}"
            )
            if resp.status_code != 200:
                return None
            due = resp.json().get("grading", {}).get("due")
            if not due:
                return None
            return datetime.fromisoformat(due).astimezone(
                timezone(timedelta(hours=8))).replace(tzinfo=None)
        except Exception:
            return None

    def fetch_assignments(self) -> list[dict]:
        """Return list of assignments: [{id, name, gradeBookPK}, ...]."""
        resp = self.client.get(
            f"{HW_BASE}/getHomeWorkList.do",
            params={"course_id": self.course_id},
        )
        resp.raise_for_status()
        return _parse_homework_list(resp.text)

    def fetch_submissions(self, grade_book_pk: str, title: str) -> list[Submission]:
        """
        Fetch student submissions for one assignment.

        Strategy:
        - Whitelist set → per-student: CheckWork.do + api/pdf.do (2 reqs per student)
        - No whitelist  → batch ZIP: downloadBatch.do (1 req for all files, fast)
        """
        resp = self.client.get(
            f"{HW_BASE}/getStudentWork.do",
            params={
                "course_id": self.course_id,
                "gradeBookPK": grade_book_pk,
                "title": title,
                "showAll": "true",
            },
        )
        resp.raise_for_status()
        students = _parse_student_list(resp.text)

        if not students:
            # Dump the page for debugging when nothing parses — an expired
            # session or a markup change otherwise fails silently.
            dbg = Path(f"/tmp/ta_studentwork_{grade_book_pk}.html")
            try:
                dbg.write_text(resp.text, encoding="utf-8")
            except Exception:
                pass
            return []

        if self.whitelist:
            return self._fetch_per_student(students, grade_book_pk, title)
        else:
            return self._fetch_batch_zip(students, grade_book_pk, title)

    # ------------------------------------------------------------------
    # Fetching strategies
    # ------------------------------------------------------------------

    def _fetch_per_student(
        self, students: list[dict], grade_book_pk: str, title: str
    ) -> list[Submission]:
        """Download individual files via CheckWork.do + api/pdf.do."""
        submissions: list[Submission] = []
        for student in students:
            student_id = student["userId"]
            if student_id not in self.whitelist:
                continue

            # Download every attempt's file — students often split report and
            # logs across attempts, and grading only the newest would miss work.
            attempts = student.get("attempts") or [
                {"filePk": student["filePk"], "attemptPk": student["attemptPk"]}
            ]
            attachments: list[Attachment] = []
            for attempt in attempts:
                attachments.extend(self._download_attempt_files(
                    grade_book_pk=grade_book_pk,
                    title=title,
                    user_id=student_id,
                    file_pk=attempt["filePk"],
                    attempt_pk=attempt["attemptPk"],
                ))
            if not attachments:
                continue

            submissions.append(Submission(
                student_id=student_id,
                student_name=student["name"],
                assignment_id=grade_book_pk,
                attachments=attachments,
                already_graded=student.get("already_graded", False),
            ))
        return submissions

    def _fetch_batch_zip(
        self, students: list[dict], grade_book_pk: str, title: str
    ) -> list[Submission]:
        """Download all files at once via downloadBatch.do ZIP.

        Falls back to per-student fetching if batch download fails."""
        try:
            zip_resp = self.client.get(
                f"{HW_BASE}/downloadBatch.do",
                params={"course_id": self.course_id, "gradeBookPK": grade_book_pk, "isGroup": "false"},
            )
            zip_resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
                zip_files = [(name, zf.read(name)) for name in zf.namelist()]

            # An empty or short batch zip would silently pair files to the
            # wrong students — fall back to per-student downloads instead.
            if len(zip_files) != len(students):
                raise zipfile.BadZipFile(
                    f"batch zip has {len(zip_files)} files for {len(students)} students"
                )

            submissions: list[Submission] = []
            for student, (filename, file_bytes) in zip(students, zip_files):
                student_id = student["userId"]
                submissions.append(Submission(
                    student_id=student_id,
                    student_name=student["name"],
                    assignment_id=grade_book_pk,
                    attachments=[Attachment(filename=filename, data=file_bytes)],
                    already_graded=student.get("already_graded", False),
                ))
            return submissions
        except (httpx.HTTPStatusError, zipfile.BadZipFile) as e:
            # Fall back to per-student fetching if batch download fails
            import sys
            print(f"  [yellow]Batch download failed (will fetch individually): {e}[/yellow]", file=sys.stderr)
            # Temporarily set whitelist to all students to trigger per-student fetch
            original_whitelist = self.whitelist
            self.whitelist = {s["userId"] for s in students}
            try:
                return self._fetch_per_student(students, grade_book_pk, title)
            finally:
                self.whitelist = original_whitelist

    def _download_attempt_files(
        self, grade_book_pk: str, title: str, user_id: str, file_pk: str, attempt_pk: str
    ) -> list[Attachment]:
        """Download every file attached to one attempt.

        The CheckWork page only points filePath at the requested filePk, but
        lists sibling files as currentAttempt_attemptFile links — each reloads
        the page with its own filePk. Fetch the page once per filePk.
        """
        resp = self.client.get(
            f"{HW_BASE}/CheckWork.do",
            params={
                "course_id": self.course_id,
                "gradeBookPK": grade_book_pk,
                "userId": user_id,
                "filePk": file_pk,
                "title": title,
                "attemptPk": attempt_pk,
            },
        )
        resp.raise_for_status()

        file_pks = {file_pk}
        for pk, att_pk in _ATTEMPT_FILE_PATTERN.findall(resp.text):
            if att_pk == attempt_pk:
                file_pks.add(pk)

        attachments: list[Attachment] = []
        seen_paths: set[str] = set()
        for pk in sorted(file_pks):
            page = resp.text if pk == file_pk else self.client.get(
                f"{HW_BASE}/CheckWork.do",
                params={
                    "course_id": self.course_id,
                    "gradeBookPK": grade_book_pk,
                    "userId": user_id,
                    "filePk": pk,
                    "title": title,
                    "attemptPk": attempt_pk,
                },
            ).text
            m = _FILE_PATH_PATTERN.search(page)
            if not m:
                continue
            file_path = m.group(1)
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            filename = file_path.rsplit("/", 1)[-1]

            # Double-encode and pass directly in URL (params= would triple-encode)
            encoded = quote(quote(file_path, safe=""), safe="")
            file_resp = self.client.get(f"{HW_BASE}/api/pdf.do?path={encoded}")
            file_resp.raise_for_status()
            attachments.append(Attachment(filename=filename, data=file_resp.content))
        return attachments


# ------------------------------------------------------------------
# HTML parsers
# ------------------------------------------------------------------

def _parse_homework_list(html: str) -> list[dict]:
    """Extract assignment list from getHomeWorkList.do HTML."""
    pattern = re.compile(
        r'getStudentWork\.do\?[^"\']*?title=([^&"\']+)[^"\']*?gradeBookPK=(\d+)|'
        r'getStudentWork\.do\?[^"\']*?gradeBookPK=(\d+)[^"\']*?title=([^&"\']+)'
    )
    seen: set[str] = set()
    assignments: list[dict] = []
    for m in pattern.finditer(html):
        title = unquote(m.group(1) or m.group(4) or "")
        pk = m.group(2) or m.group(3) or ""
        if pk and pk not in seen:
            seen.add(pk)
            assignments.append({"id": f"_{pk}_1", "name": title, "gradeBookPK": pk})
    return assignments


def _parse_student_list(html: str) -> list[dict]:
    """Extract submitted student list from getStudentWork.do HTML.

    Handles two link formats:
    - CheckAloneWork.do href with "查看" (view, already graded)
    - onclick="checkWork('userId','filePk','attemptPk')" with "批改" (grade, needs grading)

    Students may have multiple attempts (Blackboard limits files per attempt),
    so each student's entry carries an `attempts` list with every filePk/
    attemptPk pair, ordered oldest to newest. `already_graded` reflects the
    newest attempt's link text.
    """
    names = {m.group(1): m.group(2).strip() for m in _NAME_PATTERN.finditer(html)}
    student_map: dict[str, dict] = {}  # userId -> {name, attempts: [...]}

    # Per-row context: each <tr> carries the attempt's 提交时间 alongside its
    # checkWork link — needed for deadline-decay computation.
    row_time: dict[str, str] = {}  # attemptPk -> submit timestamp text
    for row_m in _ROW_PATTERN.finditer(html):
        block = row_m.group(0)
        cells = {k.strip(): v.strip() for k, v in _CELL_VALUE_PATTERN.findall(block)}
        submit_time = cells.get("提交时间", "")
        for cm in _STUDENT_ONCLICK_PATTERN.finditer(block):
            row_time[cm.group(3)] = submit_time
        for cm in _STUDENT_PATTERN.finditer(block):
            row_time[cm.group(5)] = submit_time

    def _add(user_id: str, file_pk: str, attempt_pk: str, link_text: str) -> None:
        entry = student_map.setdefault(
            user_id, {"userId": user_id, "name": names.get(user_id, "Unknown"), "attempts": []}
        )
        pair = (file_pk, attempt_pk)
        if any((a["filePk"], a["attemptPk"]) == pair for a in entry["attempts"]):
            return
        entry["attempts"].append({
            "filePk": file_pk,
            "attemptPk": attempt_pk,
            "submitted_at": row_time.get(attempt_pk, ""),
            "already_graded": link_text.strip() == "查看",
        })

    for m in _STUDENT_PATTERN.finditer(html):
        _, user_id, file_pk, _title_enc, attempt_pk, link_text = m.groups()
        _add(user_id, file_pk, attempt_pk, link_text)

    for m in _STUDENT_ONCLICK_PATTERN.finditer(html):
        user_id, file_pk, attempt_pk, _group_pk, link_text = m.groups()
        _add(user_id, file_pk, attempt_pk, link_text)

    # Blackboard limits files per attempt, so students split their submission
    # across attempts (e.g. report.pdf in attempt 1, logs.zip in attempt 2).
    # Keep ALL attempts so every file gets downloaded; order by attemptPk.
    students: list[dict] = []
    for entry in student_map.values():
        entry["attempts"].sort(key=lambda a: int(a["attemptPk"]))
        newest = entry["attempts"][-1]
        entry["filePk"] = newest["filePk"]
        entry["attemptPk"] = newest["attemptPk"]
        entry["submitted_at"] = newest["submitted_at"]
        entry["already_graded"] = newest["already_graded"]
        students.append(entry)
    return students


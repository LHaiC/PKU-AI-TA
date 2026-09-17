# How it works

```text
course.pku.edu.cn (Blackboard)
        │  1. IAAA SSO login (RSA-encrypted password → OAuth token → session cookies)
        ▼
   crawler ── gradeBookPK ──▶ submissions (PDF / Word / images, saved under submissions/)
        │
        ▼  2. LLM scoring (OpenAI-compatible API, rubric + prompt, 4 threads by default)
   scores.xlsx ──▶ yellow rows flagged for review
        │
        ▼  3. Human review (TUI or ta approve) → approved = YES
        │
        ▼  4. ta submit (human-only) → saveStudentGrade.do
```

## Authentication

`auth/iaaa.py` reproduces the official IAAA login flow: fetch the RSA public
key, encrypt the password with PKCS1v15 (matching the JSEncrypt behavior on
the login page), exchange credentials for a token, then exchange the token for
Blackboard session cookies on course.pku.edu.cn.

## Crawling

`crawler/pku_homework.py` talks to PKU's homework plugin
(`bb-homeWorkCheck-BBLEARN`):

- `getHomeWorkList.do` — assignment list (`gradeBookPK` + title)
- `getStudentWork.do` — every submitted student with `userId`, `filePk`, `attemptPk`
- `downloadBatch.do` — one ZIP with all files (fast path when no whitelist)
- `CheckWork.do` + `api/pdf.do` — per-student download (whitelist path, and
  fallback if the batch download fails)

Only the newest attempt per student is used, and submissions already graded on
the platform are skipped.

## Scoring

`scorer/llm.py` sends the rubric, the system prompt, and the submission
content to the configured model and expects a structured JSON result
(`total_score`, `breakdown`, `uncertain_parts`, `confidence`,
`llm_reasoning`).

| File type | How it is processed |
|---|---|
| Text-embedded PDF | Text extracted with `pypdf`, sent as text |
| Scanned / image PDF | Pages rendered at 2× via `pymupdf`, sent as images |
| Submitted JPEG / PNG | Sent directly as images |
| Word (.docx) | Text extracted with `python-docx` |
| Plain text | Decoded and sent as text |

The prompts instruct the model to default to full marks: every deduction needs
an explicit reason and point amount, and uncertain parts are awarded full
marks and flagged for human review instead of being penalized. A record is
flagged `needs_review` when `confidence < REVIEW_THRESHOLD` (default 0.75) or
when `uncertain_parts` is non-empty.

## Review and approval

Scoring results land in `scores.xlsx` (one row per student, yellow when
flagged). Reviewer columns (`reviewer_override_score`, `reviewer_notes`,
`approved`) are written by the TUI, by `ta approve`, or by hand in Excel.
Grading checkpoints reuse the same spreadsheet, so `--resume` and
`--regrade-unapproved` can tell approved from unfinished records.

`reviewer_override_score` wins over the LLM score when both exist; that final
value is what gets submitted.

## Submission

`submitter/blackboard.py` posts to `saveStudentGrade.do` with the score,
per-student `attemptPk` and `gradePk` (extracted from the `CheckWork.do` page
JavaScript), and the reviewer notes as feedback text. Only records with
`approved = YES` are sent through, and `ta submit --dry-run` prints exactly
what would be posted without posting it.

## Safety

- Real grade submission is human-only; agents are instructed to stop at
  `--dry-run` and hand over the command.
- `.env` holds credentials and is git-ignored; agents must never read it.
- The PKU CA is not in default trust stores, so the HTTP client disables
  certificate verification for the campus endpoints (same as the browser
  workaround the login flow expects).

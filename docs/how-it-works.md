# How it works

```text
course.pku.edu.cn (Blackboard)
        │  1. IAAA SSO login (RSA-encrypted password → OAuth token → session cookies)
        │     (or reuse a browser cookie via .bb_cookie / BB_COOKIE)
        ▼
   crawler ── gradeBookPK ──▶ submissions (PDF / Word / images, all attempts,
        │                      saved under <workdir>/submissions/)
        │     + gradebook REST API ──▶ due date → meta.json
        ▼  2. Scoring: OpenAI-compatible API or an agentic CLI
              (devin/claude/codex/opencode/cmdc — TA_CLI_ENGINE)
   <workdir>/scores.xlsx ──▶ yellow rows flagged for review
        │
        ▼  3. Human review (TUI or ta approve) → approved = YES
        │
        ▼  4. ta decay (optional) → decay_factor column, applied in place
        │
        ▼  5. ta submit (human-only) → saveStudentGrade.do
```

`workdir` is the directory containing the scores file — `meta.json`
(course/column/title/due), `rubric.md`, `.ddl_rule`, and `submissions/` all
resolve inside it, so one `--scores` path is all the later commands need.

## Authentication

`auth/iaaa.py` reproduces the official IAAA login flow: fetch the RSA public
key, encrypt the password with PKCS1v15 (matching the JSEncrypt behavior on
the login page), exchange credentials for a token, then exchange the token for
Blackboard session cookies on course.pku.edu.cn. Alternatively a browser
`Cookie:` header can be supplied via `.bb_cookie`/`BB_COOKIE` to skip IAAA
entirely (useful when the account requires OTP).

## Crawling

`crawler/pku_homework.py` talks to PKU's homework plugin
(`bb-homeWorkCheck-BBLEARN`):

- `getHomeWorkList.do` — assignment list (`gradeBookPK` + title)
- `getStudentWork.do` — every submitted student with `userId`, `filePk`, `attemptPk`
- `downloadBatch.do` — one ZIP with all files (fast path when no whitelist)
- `CheckWork.do` + `api/pdf.do` — per-student download (whitelist path, and
  fallback if the batch download fails)
- `gradebook/columns` (REST) — the assignment's due date, stored in
  `meta.json` for `ta decay`

Every attempt of every student is downloaded (students often split report and
attachments across attempts); submissions already graded on the platform are
skipped.

## Scoring

Two interchangeable engines, selected by `TA_CLI_ENGINE`/`--engine`:

- `api` — `scorer/llm.py` sends the rubric, system prompt, and extracted
  submission content to the configured OpenAI-compatible endpoint.
- `devin`/`claude`/`codex`/`opencode`/`cmdc` — `scorer/cli_scorer.py` writes
  one prompt file per student and shells out to the agentic CLI, which reads
  the saved submission files itself (visual inspection included). No API key
  needed; `TA_CLI_MODEL`/`TA_CLI_EFFORT` map onto each CLI's own flags, and
  `TA_GRADER_CMD` overrides the presets entirely. `ta engines` lists which
  CLIs are installed.

Both expect a structured JSON result (`total_score`, `breakdown`,
`uncertain_parts`, `confidence`, `llm_reasoning`).

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
flagged `needs_review` when `confidence < REVIEW_THRESHOLD` (default 0.75),
when `uncertain_parts` is non-empty, or when the score is below
`REVIEW_BELOW` (default 90%).

## Review and approval

Scoring results land in `scores.xlsx` (one row per student, yellow when
flagged). Reviewer columns (`reviewer_override_score`, `reviewer_notes`,
`approved`) are written by the TUI, by `ta approve`, or by hand in Excel.
Grading checkpoints reuse the same spreadsheet, so `--resume` and
`--regrade-unapproved` can tell approved from unfinished records.

`final_score` is what gets submitted: `reviewer_override_score` wins over the
LLM score when present, and `decay_factor` (written by `ta decay`) multiplies
whichever applies. The review queue is derived, not stored: `--needs-review`
queues every flagged, uncertain, or non-perfect record, so an approval of a
non-perfect score without notes is never considered finished.

`ta decay` applies the `.ddl_rule` late-penalty tiers in place: it fetches
submission timestamps, resolves the deadline (`--due` → `meta.json` →
gradebook REST API), and writes `submitted_at`/`hours_late`/`decay_factor`/
`decay_reason` back into the same `scores.xlsx`. Re-running is idempotent —
factors are recomputed and the penalty note is replaced, not duplicated.

## Submission

`submitter/blackboard.py` posts to `saveStudentGrade.do` with the score,
per-student `attemptPk` (newest attempt) and `gradePk` (extracted from the
`CheckWork.do` page JavaScript), and the reviewer notes as feedback text
(capped at 2000 chars by the platform payload). Only records with
`approved = YES` are sent through, and `ta submit --dry-run` prints exactly
what would be posted — full notes included — without posting it.

## Safety

- Real grade submission is human-only; agents are instructed to stop at
  `--dry-run` and hand over the command.
- `.env` holds credentials and is git-ignored; agents must never read it.
- The PKU CA is not in default trust stores, so the HTTP client disables
  certificate verification for the campus endpoints (same as the browser
  workaround the login flow expects).

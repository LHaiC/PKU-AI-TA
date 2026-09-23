# Command reference

All commands run from the repository root with `uv run`. Set `COURSE_ID` in
`.env` to omit `--course`, and see [finding-ids.md](finding-ids.md) if you do
not know the IDs yet.

## Contents

- [`ta assignments`](#ta-assignments)
- [`ta grade`](#ta-grade)
- [`ta status`](#ta-status)
- [`ta show`](#ta-show)
- [`ta review`](#ta-review)
- [`ta approve`](#ta-approve)
- [`ta submit`](#ta-submit)
- [Configuration](#configuration)
- [Customising the system prompt](#customising-the-system-prompt)
- [Hard rules](#hard-rules)

---

## `ta assignments`

List every assignment in a course with its `gradeBookPK` (the value for
`--column`).

```bash
uv run python main.py assignments --course _98024_1
uv run python main.py assignments --course _98024_1 --json
```

## `ta grade`

Crawl submissions, score them with the LLM, and export a review spreadsheet.

```bash
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md --out scores.xlsx

# Only students in a whitelist
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md \
  --whitelist 2300012345,2300012346

# Interrupt with Ctrl-C and resume later
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md --resume

# Keep already-approved students, regrade the rest
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md --regrade-unapproved

# Chinese grading prompt
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md \
  --prompt prompts/system_zh.md

# Grade with an agentic CLI instead of the HTTP API (devin/claude/codex/opencode/cmdc).
# Submission files must be saved to disk — the CLI agent reads them itself.
# `ta engines` shows which CLIs are installed on this machine.
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md \
  --engine devin --save-dir submissions/
```

| Flag | Description |
|---|---|
| `--course` | Blackboard course ID (or set `COURSE_ID` in `.env`) |
| `--column` | Assignment `gradeBookPK`; omit to fetch the assignment list |
| `--rubric` | Path to your rubric Markdown file |
| `--whitelist` | Comma-separated student IDs to grade; omit to grade everyone |
| `--out` | Output Excel file (default: `scores.xlsx`) |
| `--save-dir` | Where to save submission files (default: `submissions/`) |
| `--prompt` | System prompt file (default: `prompts/system_en.md`) |
| `--verbose` / `-v` | Print each student's result as it is scored |
| `--resume` / `-r` | Resume a previously interrupted run |
| `--regrade-unapproved` | Keep approved students, regrade the rest |
| `--engine` | Scoring engine: `api` (default), `devin`, `claude`, `codex`, `opencode`, `cmdc`. Env: `TA_CLI_ENGINE`; tune with `TA_CLI_MODEL`/`TA_CLI_EFFORT` |

Progress is checkpointed to the output spreadsheet after every student, so
`--resume` never re-scores finished submissions. Students already graded on
the platform are skipped automatically.

## `ta status`

Summarise progress and list students that still need a decision.

```bash
uv run python main.py status --scores scores.xlsx
uv run python main.py status --scores scores.xlsx --json
```

`--json` prints a single JSON document with `total`, `approved`, `pending`,
`needs_review`, and one entry per student. Useful for agents and scripts.

## `ta show`

Full detail for one student: score, breakdown, uncertain parts, LLM reasoning,
reviewer notes, and the saved submission file.

```bash
uv run python main.py show --student 2300012345 --scores scores.xlsx
uv run python main.py show --student 2300012345 --scores scores.xlsx --json
uv run python main.py show --student 2300012345 --scores scores.xlsx --submissions submissions/
```

## Working-directory convention

Each assignment lives in its own directory beside the repo (e.g. `../Lab0/`).
Point `--scores` (or `--out` for `grade`) at that directory and everything
else resolves inside it:

```
Lab0/
  scores.xlsx        # single source of truth — grading, review, decay, submit
  meta.json          # course_id / column / title / due (written by grade & decay)
  rubric.md          # rubric (grade --rubric default)
  prompt_zh.md       # grader prompt, picked up automatically if present
  .ddl_rule          # late-penalty rule (copy the repo's .ddl_rule.example)
  submissions/       # crawled attachments per student
```

`--course`/`--column`/`--due` only need to be passed to `grade` (or once, to
override); `decay` and `submit` read them from `meta.json`.

## `ta review`

Interactive TUI for reviewing students one by one. See
[tui.md](tui.md) for key bindings and a preview image.

```bash
uv run python main.py review --scores ../Lab0/scores.xlsx --needs-review
uv run python main.py review --scores ../Lab0/scores.xlsx --all
uv run python main.py review --demo
```

| Flag | Description |
|---|---|
| `--scores` | Excel file to review; its directory is the workdir (default: `scores.xlsx`) |
| `--submissions` | Directory with submission files (default: `<scores dir>/submissions`) |
| `--rubric` | Rubric file opened with `r` (default: `<scores dir>/rubric.md`) |
| `--needs-review` / `-n` | Only flagged students, or any non-perfect score |
| `--all` / `-a` | Include already-approved students |
| `--below N` | Hard cap: only scores below N percent |
| `--demo` | Use bundled sample data; no login or API key required |

## `ta approve`

Record a human review decision without opening the TUI. This is the
non-interactive equivalent of pressing `a` in `ta review`, intended for agents
and scripts. It only edits the spreadsheet; it never submits anything.

```bash
# Approve with notes (notes are required for non-perfect scores)
uv run python main.py approve --student 2300012345 --scores scores.xlsx \
  --notes "Deducted 5 pts: missing bound proof."

# Override the score (single student only)
uv run python main.py approve --student 2300012345 --scores scores.xlsx \
  --score 95 --notes "Regraded by hand."

# Revoke a previous approval
uv run python main.py approve --student 2300012345 --scores scores.xlsx --revoke

# Approve every perfect score that is not flagged for review
uv run python main.py approve --auto-perfect --scores scores.xlsx

# Machine-readable result
uv run python main.py approve --student 2300012345 --scores scores.xlsx --json
```

| Flag | Description |
|---|---|
| `--student` | Student ID(s), comma-separated |
| `--scores` | Excel file to update (default: `scores.xlsx`) |
| `--score` | Override score; requires exactly one student |
| `--notes` | Reviewer notes to store |
| `--force` | Allow a non-perfect approval without notes |
| `--revoke` | Set `approved` back to `NO` |
| `--auto-perfect` | Approve 100/100 records with no flags or uncertain parts |
| `--json` | Print one JSON document and nothing else |

## `ta decay`

Apply the late-submission penalty rule. Fetches each student's submission
timestamp from Blackboard, looks up the deadline (meta.json → gradebook REST
API), and annotates the scores file **in place** with `submitted_at`,
`hours_late`, `decay_factor`, `decay_reason`. `final_score` becomes
`(override or LLM score) × decay_factor`, so `submit` reads the same file.
Re-running is safe: factors are recomputed and the 迟交扣分 note is refreshed,
not duplicated. Students with factor < 1 are listed for verification.

```bash
uv run python main.py decay --scores ../Lab0/scores.xlsx
uv run python main.py decay --scores ../Lab0/scores.xlsx --due "2026-09-18 00:00"
```

The rule file is `.ddl_rule` — one `<hours late upper bound> <factor>` per
line (copy `.ddl_rule.example`; it is gitignored like `.env`):

```
24   0.8    # ≤24h late: ×0.8
48   0.6
72   0.4
# beyond the last bound: 0 (rejected) unless an `inf` row says otherwise
```

## `ta submit`

Post approved grades back to course.pku.edu.cn. **Real submission is
human-only** — always preview with `--dry-run` first.

```bash
# Preview (posts nothing) — course/column come from meta.json when omitted
uv run python main.py submit --scores ../Lab0/scores.xlsx --dry-run

# Re-post corrected feedback for one student only
uv run python main.py submit --scores ../Lab0/scores.xlsx --student 2300012345

# Real submission
uv run python main.py submit --scores ../Lab0/scores.xlsx

# Machine-readable result
uv run python main.py submit --scores ../Lab0/scores.xlsx --dry-run --json
```

Rows without `approved = YES` are never submitted. Reviewer notes are sent
along as the feedback text (truncated to 2000 chars by the platform payload).

## Configuration

Copy `.env.example` to `.env` and fill it in:

| Variable | Description |
|---|---|
| `TA_CLI_ENGINE` | Scoring engine: `api` (default), `devin`, `claude`, `codex`, `opencode`, `cmdc` (`TA_ENGINE` still accepted) |
| `TA_CLI_MODEL` | Model passed to the CLI engine (mapped per engine; empty = CLI default; `TA_DEVIN_MODEL` still accepted) |
| `TA_CLI_EFFORT` | Reasoning effort for the CLI engine, e.g. `low`/`medium`/`high` — claude `--effort`, codex `model_reasoning_effort`, opencode `--variant`, cmdc `--effort` (ignored by devin/api) |
| `TA_GRADER_CMD` | Custom grader command template with `{prompt_file}`; overrides engine presets |
| `OPENAI_API_KEY` | OpenRouter (or compatible) API key — required only for `TA_CLI_ENGINE=api` |
| `OPENAI_BASE_URL` | API endpoint (default: `https://openrouter.ai/api/v1`) |
| `TA_MODEL` | Model to use, e.g. `qwen/qwen3.5-397b-a17b` |
| `PKU_USERNAME` | PKU student/staff ID |
| `PKU_PASSWORD` | PKU password |
| `COURSE_ID` | Blackboard course ID, e.g. `_98024_1` |
| `STUDENT_WHITELIST` | Optional comma-separated student IDs; empty = all |
| `REVIEW_THRESHOLD` | Confidence below this is flagged for review (default `0.75`) |
| `TA_THREADS` | Number of parallel scoring threads (default `4`) |
| `ENABLE_THINKING` | Set `true` to keep Qwen3 thinking tokens (slower, costlier) |

Never commit `.env`; it is already in `.gitignore`.

## Customising the system prompt

Built-in prompts live in `prompts/`:

| File | Language |
|---|---|
| `prompts/system_en.md` | English (default) |
| `prompts/system_zh.md` | Chinese |

Pass any prompt file to `--prompt`. The prompts are plain Markdown; edit them
to change grading philosophy, tone, or output format without touching code.

## Hard rules

1. Never run `ta submit` without `--dry-run`; real submission is human-only.
2. Never approve without explicit confirmation from the person responsible.
3. Never read, print, or commit `.env`.
4. Every deduction needs an explicit reason; never invent rubric criteria.

---
name: pku-ta
description: Operate the PKU AI Teaching Assistant (`ta` CLI) end to end. Use when the user asks to grade homework from course.pku.edu.cn, find a course or assignment ID, inspect LLM grading results, record review decisions, or submit approved grades. Covers the full workflow from crawl to review, with grading still done through the configured LLM API.
license: MIT
metadata:
  audience: teaching-assistants
  workflow: course.pku.edu.cn
---

# PKU AI Teaching Assistant

Use the `ta` CLI in this repository to grade submissions from PKU's teaching
platform (course.pku.edu.cn, Blackboard), show the results to the user, record
their review decisions, and hand off the final submission step.

Grading runs through the configured scoring engine: either an OpenAI-compatible
LLM API (`--engine api`, the default; needs `OPENAI_API_KEY`) or an agentic CLI
(`--engine devin|claude|codex|opencode|cmdc`, which reads the saved submission files
itself — no API key needed; `ta engines` lists which CLIs are installed). Your job is to drive the pipeline, explain the results, and
keep the human in control.

## Hard rules

1. **Never run a real submission.** `ta submit --dry-run` is allowed for
   previewing; `ta submit` without `--dry-run` is human-only. Give the user the
   exact command to run instead.
2. **Never approve without explicit confirmation.** Only call `ta approve`
   after the user has clearly confirmed that specific student's score — or the
   batch policy, e.g. "approve every perfect score" — in the conversation.
   Subagents never approve at all, not even `--auto-perfect`.
3. **Never read, print, or commit `.env`.** If credentials are missing, tell
   the user which keys to fill in; do not ask them to paste secrets into chat.
4. **Never change a score silently.** Every override must come from the user,
   and every deduction needs an explicit reason.
5. **Run commands from the repository root** with `uv run`, for example
   `uv run python main.py status --scores scores.xlsx`.

## Setup check

Before grading, confirm:

- `uv run python main.py --help` works (install with `uv sync --extra dev`).
- `.env` exists (copy from `.env.example`) with `OPENAI_API_KEY`, `PKU_USERNAME`,
  `PKU_PASSWORD`, and ideally `COURSE_ID`.
- An assignment workdir exists outside the repo (e.g. `../Lab1/`) containing
  `rubric.md`. If it does not, help the user write one; ask for the problem
  list and point values instead of inventing criteria.

### Working-directory convention

Everything for one assignment lives beside its `scores.xlsx`:

```
<workdir>/scores.xlsx   meta.json   rubric.md   prompt_zh.md?   .ddl_rule?
                        submissions/<assignment>/<sid>_<name>/{originals,grading}
```

`--scores`/`--out` selects the workdir; `submissions/`, `rubric.md`,
`prompt_zh.md`, `.ddl_rule`, and `meta.json` (course_id/column/title/due)
all resolve inside it. `grade` writes `meta.json` (including the deadline
crawled from the gradebook); `decay` and `submit` read it back, so
`--course`/`--column`/`--due` are only needed for `grade` or as overrides.

## Workflow

### 0. Find IDs (if the user does not have them)

```bash
uv run python main.py assignments --course _98024_1
```

This prints a table of assignment titles and `gradeBookPK` values. Use the
`gradeBookPK` as `--column`. If the course ID is unknown, see
`docs/finding-ids.md` (the `course_id=_..._1` part of any course URL).

### 1. Grade

```bash
uv run python main.py grade --course _98024_1 --column 423829 --out ../Lab1/scores.xlsx
```

Useful flags:

| Flag | When to use |
|---|---|
| `--whitelist 2300012345,2300012346` | Grade only some students |
| `--resume` | Continue an interrupted run |
| `--regrade-unapproved` | Keep approved students, regrade the rest |
| `--prompt prompts/system_zh.md` | Use the Chinese grading prompt |
| `--engine devin` | Grade via an agentic CLI (`devin`/`claude`/`codex`/`opencode`/`cmdc`) instead of the LLM API — needs `--save-dir` (submission files on disk); `ta engines` lists installed CLIs; `TA_CLI_MODEL`/`TA_CLI_EFFORT` tune it |
| `--verbose` | Show each result as it is scored |

Grading is long-running and writes a checkpoint to the output spreadsheet after
every student. If a run is interrupted (or your command times out), rerun the
same command with `--resume`.

### 2. Inspect results

```bash
uv run python main.py status --scores scores.xlsx --json
uv run python main.py show --student 2300012345 --scores scores.xlsx --json
```

- `status --json` returns counts plus one row per student, including
  `needs_review`, `approved`, `final_score`, and notes.
- `show --json` returns the full breakdown, `uncertain_parts`,
  `llm_reasoning`, and the saved submission file path.

Summarise for the user: how many are pending, which are flagged for review,
and why (quote the `uncertain_parts` and the deductions). The user can also
review interactively in the TUI, which is the nicer experience for the human:

```bash
uv run python main.py review --needs-review
```

### 3. Record review decisions (after the user confirms)

```bash
uv run python main.py approve --student 2300012345 --scores scores.xlsx --notes "Deducted 5 pts: missing bound proof."
uv run python main.py approve --student 2300012345 --scores scores.xlsx --score 95 --notes "Regraded by hand."
uv run python main.py approve --student 2300012345 --scores scores.xlsx --revoke
```

- Non-perfect scores require `--notes` unless `--force` is given. Do not use
  `--force` unless the user explicitly asks for it.
- `--score` is an override and only works with a single student.
- Default review policy: skip every 100/100. After the user confirms the batch
  policy, run
  `uv run python main.py approve --auto-perfect --include-flagged --scores scores.xlsx`.
  `--include-flagged` also approves perfect scores carrying a review flag, so
  `review --needs-review` is left with non-perfect rows only.
- Without `--include-flagged`, `--auto-perfect` approves only 100/100 records
  with no flags or uncertain parts; flagged 100s stay in the queue.
- Never run either form from a subagent.

### 4. Late-submission decay (if the assignment has a deadline rule)

```bash
uv run python main.py decay --scores ../Lab1/scores.xlsx
```

Reads `.ddl_rule` (copy `.ddl_rule.example` — `上限小时 系数` per line,
e.g. `24  0.8`), fetches submission timestamps and the deadline (meta.json →
gradebook REST), and annotates scores.xlsx in place with `decay_factor` etc.
`final_score = (override or LLM score) × decay_factor`. Late students are
listed for verification; re-running is idempotent. Skip this step entirely
when the course has no late rule.

### 5. Hand off the submission

Preview (safe, posts nothing):

```bash
uv run python main.py submit --scores ../Lab1/scores.xlsx --dry-run
```

Then show the user the same command **without** `--dry-run` and let them run
it. Do not run it yourself, even if asked in a later turn — real submissions
are always human-only.

## Subagents

Fan out by workdir (`scores.xlsx`), never by student: `grade` already scores in
parallel and rewrites the checkpoint after every student, so two processes on
one spreadsheet lose approvals.

- Subagents may crawl, grade, and inspect (`status --json`, `show --json`).
- Subagents must never run `approve` or `submit`, including `--auto-perfect`.
  Approval is a human decision recorded by the parent session.
- A child session inherits this skill but not `AGENTS.md`: put the hard rules
  in its task context and tell it to read `skill://pku-ta` first.
- In omp, `.omp/config.yml` denies a bare `submit` command as a backstop.

## Output formats

`status --json`, `show --json`, `approve --json`, and `submit --json` print a
single JSON document on stdout and nothing else, so they are safe to parse.
`show --json` records use the `ScoringResult` schema from `models.py` plus
`approved`, `reviewer_override_score`, `final_score`, `reviewer_notes`, and
`submission_file`.

## Student-facing text

Deduction lines are pasted verbatim into student feedback. Write them the way
the course TA writes on paper:

- Use only terms from `rubric.md`, the course materials, or the submission;
  never coin a term or swap a course term for a near-synonym.
- Name the missing item and the deduction. No hedging (`可能`, `似乎`, `大概`,
  `如有疑问`, `仅供参考`), no process narration (`经核实`, `清晰可见`,
  `已按要求`, `综上所述`), no defence of the score.
- One sentence per criterion. No headings, bullets, emoji, or markdown.
- Never mention models, prompts, automation, or that a score came from a tool.

`prompts/system_zh.md` carries the same rules for the grader; apply them to
anything else you write for students or parents.

## Troubleshooting

| Symptom | Answer |
|---|---|
| `IAAA login failed` | Wrong `PKU_USERNAME`/`PKU_PASSWORD`, or the account needs an SMS/OTP step |
| `PKU_USERNAME and PKU_PASSWORD must be set` | `.env` is missing or incomplete |
| All submissions are skipped | They are already graded on the platform; use `--regrade-unapproved` if the user wants them redone |
| `Could not parse LLM response as JSON` | Rerun that student; if it repeats, lower `--whitelist` scope and inspect the submission |
| Want to see the TUI without credentials | `uv run python main.py review --demo` |

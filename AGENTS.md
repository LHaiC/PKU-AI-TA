# Repository guide for AI agents

PKU AI Teaching Assistant grades homework submissions from
course.pku.edu.cn (Blackboard) with an LLM, exports results to Excel for human
review, and pushes approved scores back to the platform.

## Start here

Read `.agents/skills/pku-ta/SKILL.md` for the full operating workflow. It is
the authoritative guide for any grading, review, or submission task.

## Hard rules

1. **Never run `ta submit` without `--dry-run`.** Real grade submission is
   human-only; prepare the command and hand it to the user.
2. **Never approve a record without explicit user confirmation.**
3. **Never read, print, or commit `.env`** or any credential.
4. Every deduction must have an explicit reason; never invent rubric criteria.
5. Run commands from the repository root with `uv run`, e.g.
   `uv run python main.py status --scores scores.xlsx`.

## Subagents

A subagent inherits this repo's skills but **not** this file. Paste the hard
rules into its task context and tell it to read `skill://pku-ta` first.

- Fan out by workdir (`scores.xlsx`), never by student: `grade` already scores
  in parallel and rewrites the checkpoint after every student, so two
  processes on one spreadsheet lose approvals.
- Subagents may crawl, grade, and inspect; they must never run `approve` or
  `submit`, including `--auto-perfect`.
- Approval is a human decision recorded by the parent session only.

## Command overview

| Command | Purpose |
|---|---|
| `assignments` | List assignments and their `gradeBookPK` (`--column`) values |
| `crawl` | Fetch and save submissions for review without scoring |
| `import-grades` | Import per-student grading JSON into `scores.xlsx` |
| `engines` | List scoring engines and which agentic CLIs are installed |
| `grade` | Crawl submissions, score with the LLM API, export `scores.xlsx` |
| `review` | Interactive TUI for reviewing students one by one |
| `status` | Summarise progress; `--json` for machine-readable output |
| `audit` | Check totals, decay arithmetic, notes and stale approvals |
| `show` | Full detail for one student; `--json` for machine-readable output |
| `approve` | Record a human review decision without the TUI |
| `decay` | Apply the late-submission rule (`.ddl_rule`), annotating scores.xlsx in place |
| `submit` | Post approved grades; always preview with `--dry-run` first |

## Development

```bash
uv sync --extra dev
uv run pytest tests/ -v
uv run python main.py --help
```

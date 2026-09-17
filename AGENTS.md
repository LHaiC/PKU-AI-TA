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

## Command overview

| Command | Purpose |
|---|---|
| `assignments` | List assignments and their `gradeBookPK` (`--column`) values |
| `grade` | Crawl submissions, score with the LLM API, export `scores.xlsx` |
| `review` | Interactive TUI for reviewing students one by one |
| `status` | Summarise progress; `--json` for machine-readable output |
| `show` | Full detail for one student; `--json` for machine-readable output |
| `approve` | Record a human review decision without the TUI |
| `submit` | Post approved grades; always preview with `--dry-run` first |

## Development

```bash
uv sync --extra dev
uv run pytest tests/ -v
uv run python main.py --help
```

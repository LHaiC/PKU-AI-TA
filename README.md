# PKU AI Teaching Assistant

Automatically grade student homework submissions from
[course.pku.edu.cn](https://course.pku.edu.cn) (Blackboard Learn) using an LLM,
export results to Excel for human review, and push approved scores back to the
platform.

**Pipeline:** crawl submissions → LLM scores against your rubric → human review → submit approved scores

## Features

- **Agent-driven or manual** — run the whole pipeline from opencode / Codex /
  commandcode via the bundled skill, or type the commands yourself
- **LLM scoring** through any OpenAI-compatible API (OpenRouter by default)
- **Handles PDFs, Word docs, and scanned/image submissions** (vision fallback)
- **Interactive TUI review** with per-criterion editing, overrides, and notes;
  plus non-interactive `status` / `show` / `approve` for scripts and agents
- **Crash-safe** — progress is checkpointed to the spreadsheet; resume anytime
- **English and Chinese** grading prompts

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- An [OpenRouter](https://openrouter.ai) API key (or any OpenAI-compatible endpoint)
- PKU IAAA credentials (student/staff ID + password)

## Quick start

```bash
git clone <repo-url> && cd PKU-AI-TA
uv sync --extra dev
cp .env.example .env        # fill in API key, PKU credentials, COURSE_ID
# create rubric.md describing the scoring criteria
# optional: student_list with one student ID per line
```

### With an AI agent CLI (recommended)

Open this repository in opencode, Codex, or commandcode and ask:

> Grade Homework 1 for the students in student_list and tell me what needs my
> attention.

The agent reads `.agents/skills/pku-ta/SKILL.md` and drives
`assignments → grade → status → approve` for you. It will never run a real
submission; it prints the final `ta submit` command for you to run yourself.

### Manually

```bash
# 1. Find the assignment ID (gradeBookPK)
uv run python main.py assignments --course _98024_1

# 2. Grade with the LLM
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md

# 3. Review (TUI), or try it without credentials: review --demo
uv run python main.py review --needs-review

# 4. Preview the submission, then run it yourself without --dry-run
uv run python main.py submit --course _98024_1 --column 423829 --scores scores.xlsx --dry-run
```

## Commands

| Command | Purpose |
|---|---|
| `assignments` | List assignments and their `gradeBookPK` (`--column`) values |
| `grade` | Crawl submissions, score with the LLM, export `scores.xlsx` |
| `review` | Interactive TUI for reviewing students one by one (`--demo` included) |
| `status` | Summarise progress; `--json` for machine-readable output |
| `show` | Full detail for one student; `--json` for machine-readable output |
| `approve` | Record a human review decision without the TUI |
| `submit` | Post approved grades; always preview with `--dry-run` first |

Full flag reference: [docs/usage.md](docs/usage.md).

## Documentation

| Doc | Contents |
|---|---|
| [finding-ids.md](docs/finding-ids.md) | How to find the course ID and `gradeBookPK` |
| [usage.md](docs/usage.md) | Every command and flag, configuration, prompts |
| [tui.md](docs/tui.md) | TUI guide, key bindings, screenshot |
| [agent-integration.md](docs/agent-integration.md) | Driving the TA from opencode / Codex / commandcode |
| [windows.md](docs/windows.md) | Windows setup, PowerShell examples, FAQ |
| [how-it-works.md](docs/how-it-works.md) | Architecture, file handling, safety model |
| [development.md](docs/development.md) | Tests, layout, regenerating the TUI preview |

## Hard rules

1. **Never run `ta submit` without `--dry-run`.** Real grade submission is human-only.
2. **Never approve a record without explicit confirmation** from the teacher.
3. **Never read, print, or commit `.env`** or any credential.
4. Every deduction must have an explicit reason; never invent rubric criteria.

## Development

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

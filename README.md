# PKU AI Teaching Assistant

Grade homework submissions from [course.pku.edu.cn](https://course.pku.edu.cn)
(Blackboard) with an LLM or an agentic CLI, review them in an interactive TUI,
apply late-submission penalties, and push approved scores back to the platform.

**Pipeline:** `grade` → `review`/`approve` → `decay` → `submit`

## Install

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), PKU IAAA
credentials.

```bash
git clone <repo-url> && cd PKU-AI-TA
uv sync --extra dev
cp .env.example .env          # PKU_USERNAME / PKU_PASSWORD / COURSE_ID
cp .ddl_rule.example .ddl_rule  # optional: late-penalty rule
```

Scoring engine — pick one (`ta engines` lists what's installed):

- **Agentic CLI** (no API key): `TA_CLI_ENGINE=devin|claude|codex|opencode|cmdc`.
  `TA_CLI_MODEL` / `TA_CLI_EFFORT` map onto each CLI's own flags
  (devin `--model`; claude `--model`/`--effort`; codex `-m`/`-c
  model_reasoning_effort`; opencode `-m`/`--variant`; cmdc `-m`/`--effort`) —
  empty means the CLI's default. Or set
  `TA_GRADER_CMD='mycmd --model x {prompt_file}'` for full control (must
  print the scoring JSON on stdout).
- **OpenAI-compatible API**: `TA_CLI_ENGINE=api` + `OPENAI_API_KEY`
  (OpenRouter by default; override with `OPENAI_BASE_URL`/`TA_MODEL`).

## With an agent CLI (recommended)

This repo ships a skill at `.agents/skills/pku-ta/SKILL.md`. Open the
repository in Codex / Devin / Claude Code / opencode and ask e.g.
"grade Lab 1 and tell me what needs my attention" — the agent drives the whole
pipeline and hands you the final `submit` command to run yourself. It will
never submit or approve on its own. Details: [docs/agent-integration.md](docs/agent-integration.md).

## Manually

Each assignment lives in a workdir outside the repo holding `scores.xlsx`,
`rubric.md`, `submissions/`, `meta.json` (course/column/deadline, written by
`grade`) — point `--scores` at it and everything resolves inside.

```bash
# 0. Find the assignment's gradeBookPK
uv run python main.py assignments --course _98024_1

# 1. Crawl + grade (checkpoints after every student; --resume if interrupted)
uv run python main.py grade --course _98024_1 --column 423829 --out ../Lab1/scores.xlsx

# 2. Review: batch-approve clean 100s, then TUI for the rest
uv run python main.py approve --scores ../Lab1/scores.xlsx --auto-perfect
uv run python main.py review  --scores ../Lab1/scores.xlsx --needs-review

# 3. Late-penalty decay (in place; idempotent; skip if no rule)
uv run python main.py decay --scores ../Lab1/scores.xlsx

# 4. Preview, then run the real submission yourself
uv run python main.py submit --scores ../Lab1/scores.xlsx --dry-run
```

`--course`/`--column`/`--due` are only needed for `grade` (or to override);
`decay` and `submit` read them from `meta.json`.

## Commands

| Command | Purpose |
|---|---|
| `assignments` | List assignments and their `gradeBookPK` (`--column`) values |
| `grade` | Crawl submissions, score, export `scores.xlsx` |
| `status` / `show` | Progress summary / one student's detail (`--json`) |
| `review` | Interactive TUI (`--needs-review`, `--below`, `--demo`) |
| `approve` | Record a decision without the TUI (`--auto-perfect`, `--revoke`) |
| `decay` | Apply the `.ddl_rule` late penalty in place |
| `submit` | Post approved grades; preview with `--dry-run` first |

Full flag reference: [docs/usage.md](docs/usage.md) · TUI keys:
[docs/tui.md](docs/tui.md) · internals: [docs/how-it-works.md](docs/how-it-works.md)

## Hard rules

1. **Never run `ta submit` without `--dry-run`** — real submission is human-only.
2. **Never approve a record without explicit confirmation** from the teacher.
3. **Never read, print, or commit `.env`** or any credential.
4. Every deduction needs an explicit reason; never invent rubric criteria.

## Development

```bash
uv run pytest tests/ -v
```

# PKU AI Teaching Assistant

Grade homework from [course.pku.edu.cn](https://course.pku.edu.cn)
(Blackboard) with an LLM or an agentic CLI, review in a TUI, apply late
penalties, and push approved scores back.

`crawl` (optional) → `grade` / `import-grades` → `review`/`approve` → `decay` → `submit`

## Install

Python 3.12+, [uv](https://docs.astral.sh/uv/), PKU IAAA credentials.

```bash
uv sync --extra dev
cp .env.example .env            # PKU_USERNAME / PKU_PASSWORD / COURSE_ID
cp .ddl_rule.example .ddl_rule  # optional late-penalty rule
```

Engine (`uv run python main.py engines` lists what's installed): an agentic CLI
(`TA_CLI_ENGINE=devin|claude|codex|opencode|cmdc`, no API key), the
OpenAI-compatible API (`TA_CLI_ENGINE=api` + `OPENAI_API_KEY`), or any command
that prints the scoring JSON — `TA_GRADER_CMD='mycmd {prompt_file}'`.

## With an agent CLI

Any agent CLI works: open the repository and tell it to read `AGENTS.md` or
`.agents/skills/pku-ta/SKILL.md`, then ask e.g. "grade Lab 1 and tell me what
needs my attention".

Review policy — **100/100 never reaches a human**:

```bash
uv run python main.py crawl   --course _98024_1 --column 423829 --out ../Lab1/scores.xlsx
uv run python main.py grade   --course _98024_1 --column 423829 --out ../Lab1/scores.xlsx
uv run python main.py approve --auto-perfect --include-flagged --scores ../Lab1/scores.xlsx
uv run python main.py review  --needs-review --scores ../Lab1/scores.xlsx
uv run python main.py submit  --scores ../Lab1/scores.xlsx --dry-run   # then run it yourself
```

When grading is done by an external agent, write one `<student_id>.json` per
student (the `ScoringResult` schema) and import them — totals must equal the
breakdown sum, otherwise the import aborts:

```bash
uv run python main.py import-grades ../Lab1/grades --scores ../Lab1/scores.xlsx --column 423829
```

`--include-flagged` approves perfect scores that carry a review flag, so the
queue holds non-perfect rows only. Without it, `--auto-perfect` leaves flagged
100s queued. Subagents may crawl, grade, and inspect; they must never approve
or submit. The batch policy is confirmed by the human once, then run from the
main session. In omp, `.omp/config.yml` denies a bare `submit`.

## Workdir layout

One assignment per directory outside the repo: `scores.xlsx`, `meta.json`,
`rubric.md`, `.ddl_rule`, `submissions/`. `--scores` selects it and everything
resolves inside; `decay` and `submit` read course/column from `meta.json`.

## Hard rules

1. **Never run `submit` without `--dry-run`** — real submission is human-only.
2. **Never approve without explicit confirmation**; a subagent never approves.
3. **Never read, print, or commit `.env`** or any credential.
4. Every deduction needs an explicit reason; never invent rubric criteria.

Flags: [docs/usage.md](docs/usage.md) · TUI keys: [docs/tui.md](docs/tui.md) ·
internals: [docs/how-it-works.md](docs/how-it-works.md) · agent handoff:
[docs/agent-integration.md](docs/agent-integration.md) · dev:
`uv run pytest tests/ -v`

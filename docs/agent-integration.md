# Driving the TA from an agent CLI

Grading still runs through the configured LLM API (`ta grade`), so no API keys
change and no model behavior changes. What changes is who operates the
commands: an agent CLI (opencode, Codex, commandcode, …) reads the skill,
runs the pipeline, and reports back, while the human only makes decisions.

## How each CLI finds the workflow

| CLI | Discovery |
|---|---|
| opencode | Reads `AGENTS.md` and repo skills at `.agents/skills/<name>/SKILL.md` |
| Codex | Reads `AGENTS.md`; it can open `.agents/skills/pku-ta/SKILL.md` directly |
| commandcode | Reads `AGENTS.md`; it can open the skill file directly |

`AGENTS.md` contains the hard rules and a pointer to the skill. The skill
itself (`.agents/skills/pku-ta/SKILL.md`) is the authoritative workflow.

## What agents may and may not do

| Action | Agent | Human |
|---|---|---|
| Find course/assignment IDs | ✅ `ta assignments` | |
| Grade with the API | ✅ `ta grade` | |
| Inspect results | ✅ `ta status --json`, `ta show --json` | |
| Explain deductions and flags | ✅ | |
| Approve / override / revoke | ✅ only after explicit confirmation | confirms |
| Preview submission | ✅ `ta submit --dry-run` | |
| Real submission | ❌ never | ✅ runs `ta submit` |

## Example session — opencode

```text
User: Grade Homework 1 for the students in student_list, then tell me what
      needs my attention.

Agent:
  1. Reads .agents/skills/pku-ta/SKILL.md
  2. Runs: uv run python main.py assignments --course _98024_1
     → finds gradeBookPK 423829
  3. Runs: uv run python main.py grade --course _98024_1 --column 423829 \
            --rubric rubric.md --whitelist 2300012345,2300012346 --out scores.xlsx
  4. Runs: uv run python main.py status --scores scores.xlsx --json
  5. Reports: "12 graded, 3 flagged for review. Carol Wang (2300012345) is
     flagged because the handwriting was hard to read; the LLM kept full
     marks and listed it under uncertain_parts. Want the breakdown?"

User: Yes, and approve Carol at 78 with a note that I checked the handwriting.

Agent:
  6. Runs: uv run python main.py show --student 2300012345 --scores scores.xlsx --json
  7. Summarises the breakdown and uncertain parts.
  8. Runs: uv run python main.py approve --student 2300012345 --scores scores.xlsx \
            --notes "Checked the handwriting; 78 stands."
  9. Prints the submission command for the human:
     uv run python main.py submit --course _98024_1 --column 423829 --scores scores.xlsx
```

## Example session — Codex

```text
$ codex

> Read .agents/skills/pku-ta/SKILL.md and follow it. Grade only the two
  students in student_list for assignment 423829, then show me the pending rows.

Codex: [runs assignments, grade, status; prints the pending table]
```

## Example session — commandcode

```text
$ commandcode "check grading progress for scores.xlsx"

commandcode: [reads AGENTS.md, runs `ta status --scores scores.xlsx --json`,
              reports 3 pending records and the reasons they were flagged]
```

## Handoff script for submission

After the human approves the records, the agent should print exactly this and
stop:

```bash
# Preview (safe):
uv run python main.py submit --course _98024_1 --column 423829 --scores scores.xlsx --dry-run

# Real submission (run this yourself):
uv run python main.py submit --course _98024_1 --column 423829 --scores scores.xlsx
```

## Machine-readable output

`status`, `show`, `approve`, and `submit` accept `--json` and then print one
JSON document on stdout with no other output. Example `status --json`:

```json
{
  "scores_file": "scores.xlsx",
  "total": 12,
  "approved": 9,
  "pending": 3,
  "needs_review": 2,
  "students": [
    {
      "student_id": "2300012345",
      "student_name": "Carol Wang",
      "total_score": 78.0,
      "total_max": 100.0,
      "pct": 78.0,
      "confidence": 0.58,
      "needs_review": true,
      "approved": false,
      "reviewer_override_score": null,
      "final_score": 78.0,
      "reviewer_notes": ""
    }
  ]
}
```

# Driving the TA from an agent CLI

Grading still runs through the configured LLM API (`ta grade`), so no API keys
change and no model behavior changes. What changes is who operates the
commands: an agent CLI (opencode, Codex, commandcode, …) reads the skill,
runs the pipeline, and reports back, while the human only makes decisions.

## How the CLI finds the workflow

There is nothing CLI-specific to configure. Open the repository and tell the
agent to read `AGENTS.md` (hard rules plus a pointer) or
`.agents/skills/pku-ta/SKILL.md` (the authoritative workflow), then follow it.

```text
Read .agents/skills/pku-ta/SKILL.md and follow it. Grade Lab 1 and tell me
what needs my attention.
```

`AGENTS.md` is the short version: hard rules plus a pointer to the skill.

## What agents may and may not do

| Action | Agent | Human |
|---|---|---|
| Find course/assignment IDs | ✅ `ta assignments` | |
| Grade with the API | ✅ `ta grade` | |
| Inspect results | ✅ `ta status --json`, `ta show --json` | |
| Explain deductions and flags | ✅ | |
| Approve / override / revoke | ✅ only after explicit confirmation, from the parent session | confirms |
| Approve from a subagent | ❌ never, including `--auto-perfect` | |
| Preview submission | ✅ `ta submit --dry-run` | |
| Real submission | ❌ never | ✅ runs `ta submit` |

## Subagents

Fan out by workdir (`scores.xlsx`), never by student: `grade` already scores
with `TA_THREADS` and rewrites the checkpoint after every student, so two
processes on one spreadsheet silently lose approvals.

- Subagents may crawl, grade, and inspect (`status --json`, `show --json`).
- Subagents must never run `approve` or `submit`, including `--auto-perfect`.
  Approval is recorded by the parent session after the human confirms; the
  batch policy "approve every perfect score" is one such confirmation.
- A child session inherits repo skills but not `AGENTS.md`: put the hard rules
  in its task context and tell it to read `skill://pku-ta` first.
- Read-only scout agents have no shell; use a full-tool agent for anything that
  runs `ta`.
- In omp, `.omp/config.yml` denies a bare `submit` command as a backstop. It is
  an approval policy, not a sandbox: a shell started through another process
  can still reach the binary.

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

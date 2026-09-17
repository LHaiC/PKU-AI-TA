# Development

## Setup and tests

```bash
uv sync --extra dev
uv run pytest tests/ -v
uv run python main.py --help
```

CI runs the same test suite on `ubuntu-latest` and `windows-latest`
(`.github/workflows/ci.yml`). Tests never touch the network or the LLM:
`tests/conftest.py` sets a dummy `OPENAI_API_KEY` so `config.Settings` can be
imported in a clean environment.

## Layout

| Path | Purpose |
|---|---|
| `main.py` | Typer CLI: `assignments`, `grade`, `review`, `status`, `show`, `approve`, `submit` |
| `config.py` | pydantic-settings configuration from `.env` |
| `models.py` | Pydantic models: `Submission`, `ScoringResult`, `ReviewRecord`, … |
| `auth/iaaa.py` | IAAA SSO login |
| `crawler/pku_homework.py` | Assignment/submission crawler |
| `scorer/llm.py` | LLM scoring and attachment handling |
| `review/spreadsheet.py` | Excel export/import |
| `review/tui.py`, `review/tui_components.py` | Interactive reviewer |
| `review/demo_data.py` | Sample data for `review --demo` and the preview renderer |
| `submitter/blackboard.py` | Grade submission |
| `prompts/` | System prompts (English and Chinese) |
| `.agents/skills/pku-ta/SKILL.md` | Agent workflow (see `docs/agent-integration.md`) |
| `scripts/render_tui_preview.py` | Regenerates `docs/tui-preview.svg` |

## Adding a command

Commands are plain Typer functions in `main.py`. Heavy imports go inside the
function body (matching the existing style) so `--help` stays fast and the
config module is only imported when credentials are actually needed.

## Changing the grading behavior

Edit `prompts/system_en.md` / `prompts/system_zh.md`. They define the JSON
contract the parser expects; if you change the schema, update
`scorer/llm.py` (`score_submission`) and the tests in `tests/test_scorer_llm.py`.

## Regenerating the TUI preview

```bash
uv run python scripts/render_tui_preview.py   # writes docs/tui-preview.svg
```

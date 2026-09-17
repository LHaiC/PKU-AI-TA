# Windows notes

Everything works on Windows 10/11 with Python 3.12+; this page collects the
details that differ from macOS/Linux.

## Install

```powershell
# Install uv (one of these)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install --id=astral-sh.uv -e

# In the repository
uv sync --extra dev
```

`uv sync` downloads Python 3.12 automatically if it is missing.

## Configure

```powershell
Copy-Item .env.example .env
notepad .env          # fill in OPENAI_API_KEY, PKU_USERNAME, PKU_PASSWORD, COURSE_ID
```

## Commands

All commands are identical to macOS/Linux, except the whitelist example.
Bash's `$(cat student_list | tr '\n' ',')` does not work in PowerShell:

```powershell
# PowerShell: grade exactly the students listed in student_list
$ids = (Get-Content student_list | Where-Object { $_.Trim() -ne "" }) -join ","
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md --whitelist $ids
```

```bash
# macOS/Linux equivalent
uv run python main.py grade --course _98024_1 --column 423829 --rubric rubric.md \
  --whitelist "$(paste -sd, student_list)"
```

## Terminal and encoding

- Use **Windows Terminal** rather than the legacy console for proper colors
  and UTF-8.
- If Chinese characters look garbled, run `chcp 65001` in the session, or set
  `$env:PYTHONUTF8=1` before running commands.
- Chinese characters in student names and submission file names are handled
  correctly.

## FAQ

| Question | Answer |
|---|---|
| Does line editing (arrow keys) work in the TUI? | Yes. `pyreadline3` is installed automatically on Windows and provides a `readline` shim. If it is unavailable, basic `input()` still works. |
| How does `o` (open submission) work? | It uses `os.startfile`, so PDFs open in your default viewer with no extra setup. |
| The path is too long / files fail to open | Enable long paths in Windows, or clone to a short path like `C:\PKU-AI-TA`. |
| `uv run pytest` complains about `OPENAI_API_KEY` | Tests use a dummy key from `tests/conftest.py`; make sure you run pytest from the repository root. |
| Where do I get the `--course` / `--column` values? | See [finding-ids.md](finding-ids.md). |

CI runs the test suite on both `windows-latest` and `ubuntu-latest`; see
`.github/workflows/ci.yml`.

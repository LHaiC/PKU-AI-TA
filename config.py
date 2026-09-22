from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (OpenAI-compatible) — only required for --engine api
    openai_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: str = ""
    ta_model: str = "qwen/qwen3.5-397b-a17b"
    # Scoring engine: "api" uses the OpenAI-compatible endpoint above;
    # "devin"/"claude"/"codex"/"opencode" shell out to that agentic CLI.
    # TA_CLI_ENGINE is the canonical name; TA_ENGINE still accepted.
    ta_cli_engine: str = Field(
        default="api", validation_alias=AliasChoices("TA_CLI_ENGINE", "TA_ENGINE")
    )
    # Model and reasoning effort passed to the CLI engine (devin/claude/codex/
    # opencode/cmdc). TA_DEVIN_MODEL still accepted as an alias.
    ta_cli_model: str = Field(
        default="", validation_alias=AliasChoices("TA_CLI_MODEL", "TA_DEVIN_MODEL")
    )
    ta_cli_effort: str = ""  # e.g. low/medium/high; mapped per engine
    # Custom grader command template overriding the engine presets.
    # Must contain {prompt_file}; the command reads the prompt file and
    # prints the scoring JSON on stdout. e.g. 'devin -p --prompt-file {prompt_file}'
    grader_cmd: str = ""
    # Disable chain-of-thought thinking tokens (Qwen3 series); faster + cheaper
    enable_thinking: bool = False
    # Number of parallel LLM scoring threads
    ta_threads: int = 4

    # Confidence threshold below which a result is flagged for human review
    review_threshold: float = 0.75
    # Percentage score below which a result is ALWAYS flagged for review —
    # a confident 5/100 can still be a pipeline bug, not a bad student.
    review_below: float = 90.0

    # PKU credentials for IAAA SSO
    pku_username: str = ""
    pku_password: str = ""
    # Alternative: raw Cookie header copied from a logged-in browser session on
    # course.pku.edu.cn (DevTools → Network → any request → Request Headers).
    # Skips IAAA login entirely — useful when the account requires OTP or is
    # rate-limited. Expires with the browser session; paste a fresh one then.
    # Prefer BB_COOKIE_FILE (path to a file containing the raw header) over
    # BB_COOKIE — long cookie values break .env parsing when pasted inline.
    bb_cookie: str = ""
    bb_cookie_file: str = ""

    # course.pku.edu.cn Blackboard course ID (e.g. "_12345_1")
    course_id: str = ""

    # Comma-separated student ID whitelist; empty = all students
    student_whitelist: str = ""

    @property
    def whitelist_ids(self) -> set[str]:
        if not self.student_whitelist.strip():
            return set()
        return {s.strip() for s in self.student_whitelist.split(",") if s.strip()}


settings = Settings()

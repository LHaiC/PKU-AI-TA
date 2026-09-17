"""Shared pytest configuration.

`config.Settings` requires OPENAI_API_KEY at import time. Tests never call the
LLM, so a dummy value keeps collection working in a clean environment (CI).
"""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

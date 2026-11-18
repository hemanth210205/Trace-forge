"""Configuration loader for Traceforge Customer Support Agent.

Loads environment variables reliably from the agent's local .env file regardless
of current working directory, provides fallback defaults, and performs validation
without exposing sensitive secrets in logs or terminal outputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Path definitions anchored to this file's directory
AGENT_DIR = Path(__file__).resolve().parent
ENV_PATH = AGENT_DIR / ".env"
KB_DIR = AGENT_DIR / "knowledge_base"

# Explicitly load .env from AGENT_DIR regardless of current working directory
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)
else:
    # Fallback to system environment
    load_dotenv(override=False)


@dataclass(frozen=True)
class AgentSettings:
    """Agent runtime settings."""

    endpoint: str
    api_key: str
    project_id: str
    kb_dir: Path

    @property
    def is_configured(self) -> bool:
        """Returns True only when all required live integration values are present."""
        return bool(self.endpoint and self.api_key and self.project_id)

    def validate(self) -> dict[str, str]:
        """Validates configuration and reports status safely without printing secret values."""
        status: dict[str, str] = {}

        # Endpoint
        status["TRACEFORGE_ENDPOINT"] = f"configured ({self.endpoint})" if self.endpoint else "missing"

        # API Key
        if not self.api_key:
            status["TRACEFORGE_API_KEY"] = "missing (needs to be supplied manually)"
        elif not self.api_key.startswith("tf_"):
            status["TRACEFORGE_API_KEY"] = "invalid format (expected prefix 'tf_')"
        else:
            status["TRACEFORGE_API_KEY"] = "configured (secret masked)"

        # Project ID
        if not self.project_id:
            status["TRACEFORGE_PROJECT_ID"] = "missing (needs to be supplied manually)"
        else:
            status["TRACEFORGE_PROJECT_ID"] = f"configured ({self.project_id})"

        # Knowledge Base directory
        status["KB_DIR"] = f"present ({self.kb_dir})" if self.kb_dir.exists() else "missing"

        return status


def get_settings() -> AgentSettings:
    """Returns populated AgentSettings instance."""
    return AgentSettings(
        endpoint=os.getenv("TRACEFORGE_ENDPOINT", "http://127.0.0.1:8000").strip(),
        api_key=os.getenv("TRACEFORGE_API_KEY", "").strip(),
        project_id=os.getenv("TRACEFORGE_PROJECT_ID", "").strip(),
        kb_dir=KB_DIR,
    )


if __name__ == "__main__":
    settings = get_settings()
    print("Agent Configuration Check:")
    print(f"  Config File Path: {ENV_PATH}")
    print(f"  File Exists: {ENV_PATH.exists()}")
    for var, state in settings.validate().items():
        print(f"  {var}: {state}")
    print(f"  Fully Configured: {settings.is_configured}")

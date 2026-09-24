"""Environment-backed configuration for the optional AI integration."""

import os
from dataclasses import dataclass


class AIConfigurationError(RuntimeError):
    """AI was requested but its configuration is disabled or incomplete."""


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False
    provider: str = "openai-compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "AISettings":
        return cls(
            enabled=_env_bool("WALLAPOP_AI_ENABLED", False),
            provider=os.getenv("WALLAPOP_AI_PROVIDER", "openai-compatible"),
            base_url=os.getenv("WALLAPOP_AI_BASE_URL", ""),
            api_key=os.getenv("WALLAPOP_AI_API_KEY", ""),
            model=os.getenv("WALLAPOP_AI_MODEL", ""),
            timeout_seconds=float(os.getenv("WALLAPOP_AI_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("WALLAPOP_AI_MAX_RETRIES", "2")),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}

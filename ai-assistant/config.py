import os
from dataclasses import dataclass

DEFAULT_NAME = "AI Assistant"
DEFAULT_MAX_HISTORY_MESSAGES = 20
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0
DEFAULT_OLLAMA_NUM_CTX = 2048


@dataclass(frozen=True)
class Settings:
    assistant_name: str = DEFAULT_NAME
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS
    ollama_num_ctx: int = DEFAULT_OLLAMA_NUM_CTX

    @classmethod
    def from_env(cls) -> "Settings":
        name = os.getenv("ASSISTANT_NAME", DEFAULT_NAME).strip() or DEFAULT_NAME
        raw_history = os.getenv(
            "MAX_HISTORY_MESSAGES", str(DEFAULT_MAX_HISTORY_MESSAGES)
        )
        base_url = (
            os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
            or DEFAULT_OLLAMA_BASE_URL
        )
        model = (
            os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
            or DEFAULT_OLLAMA_MODEL
        )
        raw_timeout = os.getenv(
            "OLLAMA_TIMEOUT_SECONDS", str(DEFAULT_OLLAMA_TIMEOUT_SECONDS)
        )
        raw_num_ctx = os.getenv("OLLAMA_NUM_CTX", str(DEFAULT_OLLAMA_NUM_CTX))

        try:
            max_history = int(raw_history)
        except ValueError:
            max_history = DEFAULT_MAX_HISTORY_MESSAGES

        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = DEFAULT_OLLAMA_TIMEOUT_SECONDS

        try:
            num_ctx = int(raw_num_ctx)
        except ValueError:
            num_ctx = DEFAULT_OLLAMA_NUM_CTX

        return cls(
            assistant_name=name,
            max_history_messages=max(2, max_history),
            ollama_base_url=base_url,
            ollama_model=model,
            ollama_timeout_seconds=max(1.0, timeout),
            ollama_num_ctx=max(512, min(32768, num_ctx)),
        )

import http.client
import json
import socket
import urllib.error
import urllib.request
from collections import deque

from config import Settings


class _OllamaError(Exception):
    pass


class Assistant:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._history: deque[dict[str, str]] = deque(
            maxlen=self.settings.max_history_messages
        )
        self._endpoint = f"{self.settings.ollama_base_url}/api/chat"

    @property
    def history(self) -> list[dict[str, str]]:
        return list(self._history)

    def reply(self, message: str) -> str:
        cleaned = message.strip()
        if not cleaned:
            return "Please enter a message."

        self._history.append({"role": "user", "content": cleaned})
        try:
            response = self._respond(cleaned)
        except _OllamaError as exc:
            self._history.pop()
            return str(exc)
        except Exception:
            self._history.pop()
            return "Unexpected error while contacting Ollama. Please try again."

        self._history.append({"role": "assistant", "content": response})
        return response

    def _respond(self, message: str) -> str:
        normalized = message.lower()

        if normalized in {"help", "/help"}:
            return (
                f"Type a message to chat with {self.settings.ollama_model} through "
                "Ollama, or use 'exit'/'quit' to leave. This assistant can chat "
                "only; it does not use external tools."
            )

        return self._query_ollama()

    def _query_ollama(self) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are {self.settings.assistant_name}, a helpful, concise "
                    "AI assistant running locally through Ollama. Reply in plain text."
                ),
            }
        ]
        messages.extend(self._history)

        payload = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.ollama_timeout_seconds
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = self._read_error_detail(exc)
            if exc.code == 404:
                raise _OllamaError(
                    f"Ollama is running but the model "
                    f"'{self.settings.ollama_model}' is not installed. "
                    f"Run `ollama pull {self.settings.ollama_model}` and try again."
                ) from exc
            suffix = f": {detail}" if detail else "."
            raise _OllamaError(
                f"Ollama returned HTTP {exc.code}{suffix} Please try again."
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise _OllamaError(self._timeout_message()) from exc
            raise _OllamaError(
                f"Could not reach Ollama at {self.settings.ollama_base_url}. "
                "Start it with `ollama serve`, then verify the model with "
                "`ollama list`."
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _OllamaError(self._timeout_message()) from exc
        except OSError as exc:
            raise _OllamaError(
                f"Could not reach Ollama at {self.settings.ollama_base_url}. "
                "Start it with `ollama serve`, then verify the model with "
                "`ollama list`."
            ) from exc
        except http.client.HTTPException as exc:
            raise _OllamaError(
                "Ollama closed the connection unexpectedly. Please try again."
            ) from exc

        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _OllamaError(
                "Ollama returned an invalid response. Please try again."
            ) from exc

        if not isinstance(body, dict):
            raise _OllamaError(
                "Ollama returned an invalid response. Please try again."
            )

        response_message = body.get("message")
        content = (
            response_message.get("content")
            if isinstance(response_message, dict)
            else None
        )
        if not isinstance(content, str) or not content.strip():
            raise _OllamaError("Ollama returned an empty response. Please try again.")

        return content.strip()

    def _timeout_message(self) -> str:
        return (
            f"Ollama did not respond within "
            f"{self.settings.ollama_timeout_seconds:g} seconds. The model may "
            "still be loading; please try again."
        )

    @staticmethod
    def _read_error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            return ""

        if not isinstance(payload, dict):
            return ""

        detail = payload.get("error")
        if not isinstance(detail, str):
            return ""

        return detail.strip()[:300]

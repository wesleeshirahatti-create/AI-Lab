import http.client
import json
import socket
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from config import Settings

from .tools import (
    ApprovalCallback,
    AuditLogger,
    ToolGateway,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
    build_default_registry,
)

MAX_TOOL_CALL_ROUNDS = 2
MAX_TOOL_CALLS_PER_ROUND = 8
MAX_TOOL_CONTEXT_MULTIPLIER = 2


class _OllamaError(Exception):
    pass


@dataclass(frozen=True)
class _ToolCall:
    name: str
    arguments: Any


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


class Assistant:
    def __init__(
        self,
        settings: Settings | None = None,
        policy: ToolPolicy | None = None,
        *,
        tool_policy: ToolPolicy | None = None,
        registry: ToolRegistry | None = None,
        gateway: ToolGateway | None = None,
        audit_logger: AuditLogger | None = None,
        approver: ApprovalCallback | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        chosen_policy = tool_policy or policy or ToolPolicy.from_env()

        if gateway is not None:
            self.gateway = gateway
            gateway_policy = getattr(gateway, "policy", None)
            self.policy = (
                gateway_policy
                if isinstance(gateway_policy, ToolPolicy)
                else chosen_policy
            )
            gateway_registry = getattr(gateway, "registry", None)
            self.registry = (
                registry
                or (
                    gateway_registry
                    if isinstance(gateway_registry, ToolRegistry)
                    else None
                )
                or build_default_registry(policy=self.policy)
            )
            gateway_audit = getattr(gateway, "audit_logger", None)
            self.audit_logger = audit_logger or (
                gateway_audit
                if isinstance(gateway_audit, AuditLogger)
                else None
            )
        else:
            self.policy = chosen_policy
            self.registry = registry or build_default_registry(policy=self.policy)
            self.audit_logger = audit_logger or AuditLogger(self.policy.log_path)
            self.gateway = ToolGateway(
                self.policy,
                self.registry,
                self.audit_logger,
                approver=approver,
            )

        self.tool_policy = self.policy
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
            if self.policy.tools_enabled:
                if self._write_tool_available():
                    return (
                        f"Type a message to chat with "
                        f"{self.settings.ollama_model} through Ollama, or use "
                        "'exit'/'quit' to leave. Tools are enabled: list_files, "
                        "read_file, and write_file. Every write requires "
                        "explicit approval, and existing files are only "
                        "replaced when overwrite=true and approved. The "
                        "assistant cannot run Python or use a shell."
                    )
                return (
                    f"Type a message to chat with {self.settings.ollama_model} "
                    "through Ollama, or use 'exit'/'quit' to leave. Read-only "
                    "tools are enabled: list_files and read_file. The assistant "
                    "cannot write files, run Python, or use a shell."
                )
            return (
                f"Type a message to chat with {self.settings.ollama_model} through "
                "Ollama, or use 'exit'/'quit' to leave. This assistant can chat "
                "only; it does not use external tools."
            )

        return self._query_ollama()

    def _query_ollama(self) -> str:
        messages: list[dict[str, Any]] = self._build_messages()
        tools = self._tool_definitions()
        max_rounds = (
            min(MAX_TOOL_CALL_ROUNDS, self.policy.max_tool_rounds)
            if tools is not None
            else 0
        )
        rounds = 0
        request_id = uuid.uuid4().hex
        remaining_budget = (
            self.policy.max_output_bytes * MAX_TOOL_CONTEXT_MULTIPLIER
        )

        while True:
            response_message = self._request_chat(messages, tools)
            content = response_message.get("content")
            if not isinstance(content, str):
                content = ""
            raw_tool_calls = response_message.get("tool_calls")
            tool_calls = self._parse_tool_calls(raw_tool_calls)

            if not tool_calls:
                if content.strip():
                    return content.strip()
                raise _OllamaError(
                    "Ollama returned an empty response. Please try again."
                )

            if tools is None:
                if content.strip():
                    return content.strip()
                raise _OllamaError(
                    "Ollama requested a tool, but tools are disabled. "
                    "Set TOOLS_ENABLED=true to allow read-only tools."
                )

            if rounds >= max_rounds:
                if content.strip():
                    return content.strip()
                return (
                    "I stopped because the maximum of "
                    f"{max_rounds} tool-call round(s) was reached."
                )

            rounds += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": raw_tool_calls,
                }
            )

            for index, tool_call in enumerate(tool_calls):
                if index >= MAX_TOOL_CALLS_PER_ROUND:
                    result = ToolResult.failure(
                        tool_call.name or "<invalid>",
                        "Too many tool calls in one round.",
                    )
                else:
                    result = self._execute_tool_call(tool_call, request_id)

                text = self._format_tool_result(result, remaining_budget)
                remaining_budget -= len(text.encode("utf-8"))
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_call.name or "<invalid>",
                        "content": text,
                    }
                )

    def _build_messages(self) -> list[dict[str, Any]]:
        system_content = (
            f"You are {self.settings.assistant_name}, a helpful, concise "
            "AI assistant running locally through Ollama. Reply in plain text."
        )
        if self.policy.tools_enabled:
            if self._write_tool_available():
                system_content += (
                    " You may use the provided tools to inspect the workspace "
                    "and to write files after explicit user approval. "
                    "Permissions are enforced by the application; never claim "
                    "to approve or grant permissions yourself."
                )
            else:
                system_content += (
                    " You may use the provided read-only tools to inspect "
                    "files in the workspace. Permissions are enforced by the "
                    "application; never claim to approve or grant permissions "
                    "yourself."
                )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]
        messages.extend(self._history)
        return messages

    def _write_tool_available(self) -> bool:
        return self.policy.allow_writes and "write_file" in self.registry

    def _tool_definitions(self) -> list[dict[str, Any]] | None:
        if not self.policy.tools_enabled:
            return None

        definitions: list[dict[str, Any]] = []
        for spec in self.registry.specs():
            if self.policy.denial_reason(spec) is not None:
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": dict(spec.parameters),
                    },
                }
            )
        return definitions or None

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: Any) -> list[_ToolCall]:
        if not isinstance(raw_tool_calls, list):
            return []

        calls: list[_ToolCall] = []
        for item in raw_tool_calls:
            if not isinstance(item, Mapping):
                calls.append(_ToolCall("", {}))
                continue

            function = item.get("function")
            if not isinstance(function, Mapping):
                function = item if "name" in item else {}

            name = function.get("name")
            if not isinstance(name, str):
                name = ""
            name = name.strip()

            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    pass
            if arguments is None:
                arguments = {}

            calls.append(_ToolCall(name, arguments))

        return calls

    def _execute_tool_call(
        self, tool_call: _ToolCall, request_id: str
    ) -> ToolResult:
        if not tool_call.name:
            return ToolResult.failure("<invalid>", "Invalid tool call.")
        try:
            result = self.gateway.dispatch(
                tool_call.name,
                tool_call.arguments,
                request_id=request_id,
            )
        except Exception:
            return ToolResult.failure(
                tool_call.name, "Tool execution failed."
            )
        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                tool_call.name, "Tool returned an invalid result."
            )
        return result

    def _format_tool_result(
        self, result: ToolResult, remaining_budget: int
    ) -> str:
        if remaining_budget <= 0:
            return "[tool result omitted: context limit reached]"

        if result.ok:
            text = result.output
            if result.metadata.get("truncated"):
                text = f"{text}\n[truncated]"
        else:
            text = f"Error: {result.error}"

        limit = max(0, min(self.policy.max_output_bytes, remaining_budget))
        return _truncate_utf8(text, limit)

    def _request_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        if tools:
            payload["tools"] = tools

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
        if not isinstance(response_message, dict):
            raise _OllamaError(
                "Ollama returned an invalid response. Please try again."
            )

        return response_message

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

import hashlib
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .audit import AuditLogger
from .base import (
    ToolExecutionError,
    ToolResult,
    ToolSpec,
    ToolValidationError,
)
from .policy import ToolPolicy
from .registry import ToolRegistry

ApprovalCallback = Callable[[ToolSpec, Mapping[str, Any]], bool]

_SENSITIVE_ARGUMENT_NAMES = {
    "code",
    "content",
    "data",
    "script",
    "source",
    "stdin",
    "text",
}


class ToolGateway:
    def __init__(
        self,
        policy: ToolPolicy,
        registry: ToolRegistry,
        audit_logger: AuditLogger,
        approver: ApprovalCallback | None = None,
    ) -> None:
        self.policy = policy
        self.registry = registry
        self.audit_logger = audit_logger
        self.approver = approver

    def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        display_name = (
            tool_name
            if isinstance(tool_name, str) and tool_name
            else "<invalid>"
        )

        if arguments is None:
            normalized_arguments: dict[str, Any] = {}
            argument_summary: dict[str, Any] = {}
        elif isinstance(arguments, Mapping):
            normalized_arguments = dict(arguments)
            argument_summary = self._summarize_arguments(
                normalized_arguments,
                allow_content_preview=False,
            )
        else:
            normalized_arguments = {}
            argument_summary = {"value_type": type(arguments).__name__}

        event: dict[str, Any] = {
            "request_id": request_id or uuid.uuid4().hex,
            "tool": display_name,
            "arguments": argument_summary,
        }

        if display_name == "<invalid>":
            return self._finish(
                event,
                ToolResult.failure("<invalid>", "Invalid tool name."),
                "invalid_arguments",
                started,
            )

        if not self.policy.tools_enabled:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Tools are disabled."),
                "disabled",
                started,
            )

        if not isinstance(arguments, Mapping):
            return self._finish(
                event,
                ToolResult.failure(
                    tool_name, "Tool arguments must be an object."
                ),
                "invalid_arguments",
                started,
                error_type="TypeError",
            )

        tool = self.registry.get(tool_name)
        if tool is None:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Unknown tool."),
                "unknown_tool",
                started,
            )

        allow_content_preview = not tool.spec.mutating
        event["arguments"] = self._summarize_arguments(
            normalized_arguments,
            allow_content_preview=allow_content_preview,
        )

        try:
            validated = tool.validate(normalized_arguments)
        except ToolValidationError as exc:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Invalid tool arguments."),
                "invalid_arguments",
                started,
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )
        except Exception as exc:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Tool validation failed."),
                "invalid_arguments",
                started,
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )

        if not isinstance(validated, Mapping):
            return self._finish(
                event,
                ToolResult.failure(
                    tool_name, "Tool validation returned invalid arguments."
                ),
                "invalid_arguments",
                started,
                error_type="TypeError",
            )

        normalized_validated = dict(validated)
        event["arguments"] = self._summarize_arguments(
            normalized_validated,
            allow_content_preview=allow_content_preview,
        )

        denial = self.policy.denial_reason(tool.spec)
        if denial is not None:
            return self._finish(
                event,
                ToolResult.failure(tool_name, denial),
                "denied",
                started,
            )

        permission = "auto"
        if self.policy.approval_required(tool.spec):
            if self.approver is None:
                return self._finish(
                    event,
                    ToolResult.failure(
                        tool_name,
                        "Approval is required but no approver is configured.",
                    ),
                    "denied",
                    started,
                )
            try:
                approved = bool(
                    self.approver(tool.spec, dict(normalized_validated))
                )
            except Exception as exc:
                return self._finish(
                    event,
                    ToolResult.failure(tool_name, "Approval failed."),
                    "denied",
                    started,
                    error_type=type(exc).__name__,
                    error_detail=str(exc),
                )
            if not approved:
                return self._finish(
                    event,
                    ToolResult.failure(tool_name, "Permission denied."),
                    "denied",
                    started,
                )
            permission = "approved"

        try:
            result = tool.execute(dict(normalized_validated))
        except ToolExecutionError as exc:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Tool execution failed."),
                permission,
                started,
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )
        except Exception as exc:
            return self._finish(
                event,
                ToolResult.failure(tool_name, "Tool execution failed."),
                permission,
                started,
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )

        if not isinstance(result, ToolResult):
            return self._finish(
                event,
                ToolResult.failure(
                    tool_name, "Tool returned an invalid result."
                ),
                permission,
                started,
                error_type="TypeError",
            )

        return self._finish(event, result, permission, started)

    def _finish(
        self,
        event: dict[str, Any],
        result: ToolResult,
        permission: str,
        started: float,
        *,
        error_type: str | None = None,
        error_detail: str | None = None,
    ) -> ToolResult:
        event.update(
            {
                "permission": permission,
                "ok": result.ok,
                "duration_ms": round(
                    (time.perf_counter() - started) * 1000, 2
                ),
                "output_bytes": len(result.output.encode("utf-8")),
                "error": result.error[:500],
                "metadata": self._summarize_arguments(
                    dict(result.metadata),
                    allow_content_preview=False,
                ),
            }
        )
        if error_type is not None:
            event["error_type"] = error_type
        if error_detail:
            event["error_detail"] = str(error_detail)[:500]

        try:
            self.audit_logger.log(event)
        except Exception:
            pass
        return result

    def _summarize_arguments(
        self,
        arguments: Mapping[str, Any],
        *,
        allow_content_preview: bool = True,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in arguments.items():
            name = str(key)[:100]
            if name.lower() in _SENSITIVE_ARGUMENT_NAMES:
                summary[name] = self._content_summary(
                    value,
                    allow_preview=allow_content_preview,
                )
            elif isinstance(value, str):
                summary[name] = value[:200]
            elif isinstance(value, (int, float, bool)) or value is None:
                summary[name] = value
            else:
                summary[name] = f"<{type(value).__name__}>"
        return summary

    def _content_summary(
        self,
        value: Any,
        *,
        allow_preview: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(value, str):
            return {"type": type(value).__name__}
        encoded = value.encode("utf-8")
        summary = {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
        }
        if allow_preview and self.policy.log_content:
            summary["preview"] = value[:200]
        return summary


__all__ = ["ApprovalCallback", "ToolGateway"]

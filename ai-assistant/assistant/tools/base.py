import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolError(Exception):
    pass


class ToolValidationError(ToolError):
    pass


class ToolPermissionError(ToolError):
    pass


class ToolExecutionError(ToolError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    mutating: bool = False
    requires_approval: bool = False
    requires_python: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                "Tool name must match ^[a-z][a-z0-9_]*$."
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Tool description must be a non-empty string.")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("Tool parameters must be a mapping.")
        for attribute in ("mutating", "requires_approval", "requires_python"):
            if not isinstance(getattr(self, attribute), bool):
                raise ValueError(f"{attribute} must be a boolean.")
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool: str
    output: str = ""
    error: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise ValueError("ToolResult.ok must be a boolean.")
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("ToolResult.tool must be a non-empty string.")
        if not isinstance(self.output, str):
            raise ValueError("ToolResult.output must be a string.")
        if not isinstance(self.error, str):
            raise ValueError("ToolResult.error must be a string.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("ToolResult.metadata must be a mapping.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "output": self.output,
            "error": self.error,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def success(
        cls,
        tool: str,
        output: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            ok=True,
            tool=tool,
            output=output,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        tool: str,
        error: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            ok=False,
            tool=tool,
            error=error,
            metadata=dict(metadata or {}),
        )


class Tool(Protocol):
    spec: ToolSpec

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        ...

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        ...


__all__ = [
    "Tool",
    "ToolError",
    "ToolExecutionError",
    "ToolPermissionError",
    "ToolResult",
    "ToolSpec",
    "ToolValidationError",
]

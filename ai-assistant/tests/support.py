import json
from pathlib import Path

from assistant.tools import (
    AuditLogger,
    ToolPolicy,
    ToolResult,
    ToolSpec,
)


class FakeTool:
    def __init__(
        self,
        name: str = "sample_tool",
        *,
        mutating: bool = False,
        requires_approval: bool = False,
        requires_python: bool = False,
        result: ToolResult | None = None,
        validation_error: Exception | None = None,
        execution_error: Exception | None = None,
    ) -> None:
        self.spec = ToolSpec(
            name=name,
            description="A fake tool used only by tests.",
            parameters={"type": "object", "properties": {}},
            mutating=mutating,
            requires_approval=requires_approval,
            requires_python=requires_python,
        )
        self.result = result
        self.validation_error = validation_error
        self.execution_error = execution_error
        self.calls = 0
        self.seen_arguments: dict | None = None

    def validate(self, arguments):
        if self.validation_error is not None:
            raise self.validation_error
        return dict(arguments)

    def execute(self, arguments):
        self.calls += 1
        self.seen_arguments = dict(arguments)
        if self.execution_error is not None:
            raise self.execution_error
        if self.result is not None:
            return self.result
        return ToolResult.success(self.spec.name, "ok")


def make_policy(root: Path, **overrides) -> ToolPolicy:
    values = {
        "project_root": Path(root),
        "tools_enabled": True,
        "allow_writes": True,
        "allow_python": True,
        "require_confirmation": True,
    }
    values.update(overrides)
    return ToolPolicy(**values)


def make_audit(root: Path, name: str = "tool_calls.jsonl") -> AuditLogger:
    return AuditLogger(Path(root) / "logs" / name)


def read_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

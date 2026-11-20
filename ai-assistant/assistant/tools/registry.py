from collections.abc import Mapping
from pathlib import Path

from .base import Tool, ToolSpec
from .files import ListFilesTool, ReadFileTool
from .policy import ToolPolicy


class ToolRegistry:
    def __init__(self, tools: Mapping[str, Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools.values():
                self.register(tool)

    def register(self, tool: Tool) -> None:
        spec = getattr(tool, "spec", None)
        if not isinstance(spec, ToolSpec):
            raise TypeError("Tool must expose a ToolSpec as 'spec'.")
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        self._tools[spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in self.names())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def build_default_registry(
    root: Path | str | ToolPolicy | None = None,
    *,
    policy: ToolPolicy | None = None,
    read_root: Path | str | None = None,
    max_file_bytes: int | None = None,
    max_output_bytes: int | None = None,
    max_list_entries: int | None = None,
) -> ToolRegistry:
    if isinstance(root, ToolPolicy):
        policy = root
        root = None

    base_root = read_root if read_root is not None else root
    if policy is not None:
        if base_root is None:
            base_root = policy.read_root
        if max_file_bytes is None:
            max_file_bytes = policy.max_file_bytes
        if max_output_bytes is None:
            max_output_bytes = policy.max_output_bytes
        if max_list_entries is None:
            max_list_entries = policy.max_list_entries

    registry = ToolRegistry()
    registry.register(
        ListFilesTool(
            base_root,
            max_list_entries=max_list_entries,
            max_output_bytes=max_output_bytes,
        )
    )
    registry.register(
        ReadFileTool(
            base_root,
            max_file_bytes=max_file_bytes,
            max_output_bytes=max_output_bytes,
        )
    )
    return registry


build_readonly_registry = build_default_registry


__all__ = [
    "ToolRegistry",
    "build_default_registry",
    "build_readonly_registry",
]
